#!/usr/bin/python3
# -*- coding: utf-8 -*-

import subprocess
import re
import sys
import os
import tempfile

import config

## Script based on
# https://sites.google.com/site/akohlmey/random-hacks/nvidia-gpu-coolness#TOC-Faking-a-Head-for-a-Headless-X-Server
# https://devtalk.nvidia.com/default/topic/789888/set-fan-speed-without-an-x-server-solved-/?offset=3
# https://devtalk.nvidia.com/default/topic/769851/multi-nvidia-gpus-and-xorg-conf-how-to-account-for-pci-bus-busid-change-/
# https://devtalk.nvidia.com/default/topic/981655/fan-speed-on-headless-linux-machine-without-performance-loss/?offset=3


# Note: Create configuration when not running this script
# nvidia-xconfig -a --cool-bits=31 --allow-empty-initial-configuration


class GPU(object):

    def __init__(self, name, uuid, index=-1, slot=None):
        self._name = name
        self._uuid = uuid
        self._index = index
        self._slot = slot


    def __str__(self):
        if self._slot is None:
            return "GPU <name={} uuid={} index=GPU:{}>".format(self._name, self._uuid, self._index)
        else:
            return "GPU <name={} uuid={} index=GPU:{} slot=PCI:{}>".format(self._name, self._uuid, self._index, self._slot)

    def __eq__(self, other):
        return self._uuid == other._uuid and self._index == other._index

    @property
    def name(self):
        return self._name

    @property
    def uuid(self):
        return self._uuid

    @property
    def index(self):
        return self._index

    @property
    def slot(self):
        return self._slot


class GPUConfig(object):

    def __init__(self, cfg):
        # parse and check config
        if "fan_speed" in cfg and cfg["fan_speed"] > 0 and cfg["fan_speed"] <= 100:
            self._fan_speed = cfg["fan_speed"]
        else:
            self._fan_speed = None

        if "clock_offset" in cfg and cfg["clock_offset"] >= -200 and cfg["clock_offset"] <= 1200:
            self._clock_offset = cfg["clock_offset"]
        else:
            self._clock_offset = None

        if "memory_offset" in cfg and cfg["memory_offset"] >= -2000 and cfg["memory_offset"] <= 2000:
            self._memory_offset = cfg["memory_offset"]
        else:
            self._memory_offset = None

    def config_str(self):
        s = ""

        if self._fan_speed is not None:
            s += (" -a [gpu:{{0}}]/GPUFanControlState=1"
                  " -a [fan:{{0}}]/GPUTargetFanSpeed={}").format(self._fan_speed)

        if self._clock_offset is not None or self._memory_offset is not None:
            s += " -a [gpu:{0}]/GPUPowerMizerMode=1"

        if self._clock_offset is not None:
            s += " -a [gpu:{{0}}]/GPUGraphicsClockOffset[0]={}".format(self._clock_offset)

        if self._memory_offset is not None:
            s += " -a [gpu:{{0}}]/GPUMemoryTransferRateOffset[3]={}".format(self._memory_offset)

        return s

    def __str__(self):
        return self.config_str()


class Nvidia(object):

    def __init__(self, template_file="xorg.template"):
        # Note: check installed Nvidia GPUs
        # nvidia-xconfig --query-gpu-info
        # nvidia-smi -L
        # nvidia-settings -q gpus

        # check if we are running as root
        if os.geteuid() != 0:
            print("[-] Script not running as root!")
            sys.exit(1)

        self._is_persistent = False

        # load xorg.conf template
        print("[*] Loading template xorg.conf")
        self._template = ""
        self._load_template(template_file)

        # create a list of installed GPUs
        print("[*] Identifying installed GPUs")
        self._gpus = list()
        self._find_gpus()

        print("[*] Detected {} GPU(s)".format(len(self._gpus)))
        # print("[*] {}".format([str(i) for i in self._gpus]))

    def run(self, config, edid_file="~/mining/edid.bin"):
        if not os.path.exists(edid_file):
            print("[-] edid file does not exist")
            sys.exit(1)

        print("[*] Applying configurations")
        for g in self._gpus:
            if g.uuid not in config:
                print("[!] Don't have configuration for {}. Skipped".format(g))
                continue

            self._configure_gpu(g, GPUConfig(config[g.uuid]), edid_file)

    def _set_persistent(self):
        if not self._is_persistent:
            exitcode, output = subprocess.getstatusoutput("nvidia-smi -pm 1")
            if exitcode != 0:
                print("[!] Could not set GPUs to persistent mode.")

            self._is_persistent = True

    def _load_template(self, template_file):
        if not os.path.exists(template_file):
            print("[-] Cannot find xorg.conf template file")
            sys.exit(1)

        with open(template_file, "rb") as f:
            self._template = f.read().decode("utf-8")


    def _find_gpus(self):
        exitcode, output = subprocess.getstatusoutput("nvidia-smi -L")
        if exitcode == 9:
            print("[-] Failed to communicate with Nvidia driver")
        assert (exitcode == 0)

        matches = re.findall(
            r"GPU (?P<number>\d): "
            r"(?P<name>[\w ]+) "
            r"\(UUID: GPU-(?P<uuid>\w{8}-\w{4}-\w{4}-\w{4}-\w{12})\)",
            output
        )

        cards1 = None if not matches else [GPU(name, uuid, number, None) for number, name, uuid in matches]
        # print([str(i) for i in cards1])

        if cards1 is None:
            print("[-] Cannot detect any cards via 'nvidia-smi -L'")
            sys.exit(1)

        # double check and get bus id
        exitcode, output = subprocess.getstatusoutput("nvidia-xconfig --query-gpu-info")
        if exitcode == 1:
            print("[-] Failed to communicate with Nvidia driver")
        assert (exitcode == 0)

        matches = re.findall(
            r"GPU #(?P<number>\d):"
            r"\s+?"
            r"Name\s+?: (?P<name>[\w ]+)"
            r"\s+?"
            r"UUID\s+?: GPU-(?P<uuid>\w{8}-\w{4}-\w{4}-\w{4}-\w{12})"
            r"\s+?"
            r"PCI BusID\s+?: PCI:(?P<slot>\d{1,2}:\d{1,2}:\d{1,2})",
            output
        )

        cards2 = None if not matches else [GPU(name, uuid, number, slot) for number, name, uuid, slot in matches]
        #print([str(i) for i in cards2])

        if cards2 is None:
            print("[-] Cannot detect any cards via 'nvidia-xconfig --query-gpu-info'")
            sys.exit(1)

        for c in cards2:
            if c in cards1:
                self._gpus.append(c)
            else:
                print("[!] Found GPU that was not detected by both methods. Ignoring it: {}".format(c))


    def _configure_gpu(self, gpu, cfg, edid_file):
        """Note: This function needs to run as root!

        :param gpu:
        :type gpu: GPU
        :param cfg:
        :type cfg: GPUConfig
        :param edid_file: path to edid file
        :type edid_file: str
        """
        assert isinstance(gpu, GPU)
        assert isinstance(cfg, GPUConfig)

        self._set_persistent()

        print("[*] Configuring GPU:{}".format(gpu.index))
        with tempfile.NamedTemporaryFile() as xcfg:
            # prepare 'fake' xorg.conf
            xcfg.write(self._template.format(edid_file, gpu.slot).encode("utf-8"))
            xcfg.flush()

            exitcode, output = subprocess.getstatusoutput(("xinit /usr/bin/nvidia-settings{}"
                                                          " -- :1 -once -config {}").format(str(cfg), xcfg.name).format(gpu.index))

            if exitcode != 0:
                print("[-] Errors occured while configuring the device.")


def main():
    n = Nvidia()
    n.run(config.C, "~/mining/edid.bin")



if __name__ == "__main__":
    main()
