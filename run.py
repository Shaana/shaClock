#!/usr/bin/python3
# -*- coding: utf-8 -*-

import subprocess
import re
import sys
import os
import tempfile
import shutil

## Script based on
# https://sites.google.com/site/akohlmey/random-hacks/nvidia-gpu-coolness#TOC-Faking-a-Head-for-a-Headless-X-Server
# https://devtalk.nvidia.com/default/topic/789888/set-fan-speed-without-an-x-server-solved-/?offset=3
# https://devtalk.nvidia.com/default/topic/769851/multi-nvidia-gpus-and-xorg-conf-how-to-account-for-pci-bus-busid-change-/
# https://devtalk.nvidia.com/default/topic/981655/fan-speed-on-headless-linux-machine-without-performance-loss/?offset=3
# https://github.com/justinjereza/nvfan-headless/blob/master/nvfan-headless
# https://wiki.archlinux.org/index.php/NVIDIA/Tips_and_tricks
# https://devtalk.nvidia.com/default/topic/973877/how-to-under-overclock-from-command-line-/?offset=2

# Note: Create configuration when not running this script
# nvidia-xconfig -a --cool-bits=28 --allow-empty-initial-configuration -o out.conf

# TODO
# nvidia-settings -q [gpu:0]/GPUMemoryTransferRateOffset

# nvidia-smi -q -d TEMPERATURE,POWER
# nvidia-smi -q -i {0} | grep -i "fan speed"
# nvidia-smi -i 0 --format=csv --query-gpu=power.limit

#    Option  "RegistryDwords" "PowerMizerEnable=0x1; PerfLevelSrc=0x2222; PowerMizerDefault=0x1; PowerMizerDefaultAC=0x1"

class GPU(object):

    def __init__(self, name, uuid, index=-1, slot=None):
        self._name = name
        self._uuid = uuid
        self._index = index
        self._slot = slot

        # will be updated by the Nvidia class
        self._perf = 0

        self._limits = {}
        self._determine_limits()

    def _determine_limits(self):
        # mostly hard coded for now, seams to work for GTX 10xx GPUs
        self._limits["fan_speed"] = (0, 100)
        self._limits["clock_offset"] = (-200, 1200)
        self._limits["memory_offset"] = (-2000, 2000)
        self._limits["voltage"] = (0, 0)
        self._limits["power"] = None

        exitcode, p_range = subprocess.getstatusoutput(("nvidia-smi -i {} --format=csv,noheader,nounits "
                                                      "--query-gpu=power.min_limit,power.max_limit").format(self._index))

        if exitcode != 0 or p_range == "[Not Supported]":
            print("[-] Error, invalid power range or not supported")
        else:
            self._limits["power"] = tuple([float(i) for i in p_range.replace(" ", "").split(",")])
            assert len(self._limits["power"]) == 2

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

    @property
    def perf(self):
        return self._perf

    @property
    def limits(self):
        return dict(self._limits)


# class GPUConfig(object):
#
#     def __init__(self, cfg):
#         # parse and check config
#         if "fan_speed" in cfg and cfg["fan_speed"] is not None and cfg["fan_speed"] > 0 and cfg["fan_speed"] <= 100:
#             self._fan_speed = cfg["fan_speed"]
#         else:
#             self._fan_speed = None
#
#         if "clock_offset" in cfg and cfg["clock_offset"] is not None and cfg["clock_offset"] >= -200 and cfg["clock_offset"] <= 1200:
#             self._clock_offset = cfg["clock_offset"]
#         else:
#             self._clock_offset = None
#
#         if "memory_offset" in cfg and cfg["memory_offset"] is not None and cfg["memory_offset"] >= -2000 and cfg["memory_offset"] <= 2000:
#             self._memory_offset = cfg["memory_offset"]
#         else:
#             self._memory_offset = None
#
#
#
#     def config_str(self):
#         s = ""
#
#         if self._fan_speed is not None:
#             s += (" -a [gpu:{{0}}]/GPUFanControlState=1"
#                   " -a [fan:{{0}}]/GPUTargetFanSpeed={}").format(self._fan_speed)
#         else:
#             s += " -a [gpu:{0}]/GPUFanControlState=0"
#
#
#         if self._clock_offset is not None or self._memory_offset is not None:
#             s += " -a [gpu:{0}]/GPUPowerMizerMode=1"
#
#         # if self._clock_offset is not None:
#         #     s += " -a [gpu:{{0}}]/GPUGraphicsClockOffset[0]={}".format(self._clock_offset)
#
#         if self._memory_offset is not None:

#
#         # debuG
#         #s = " -d -q GPUMemoryTransferRateOffset"
#         #s = " -q [gpu:1]/GPUPerfModes"
#         #s = " -e GPUOverVoltageOffset"
#         return s
#
#     def __str__(self):
#         return self.config_str()
#

def in_limit(v, l):
    assert isinstance(l, (tuple, list)) and len(l) == 2
    if v is None:
        return False
    else:
        return (v >= l[0] and v <= l[1])



class Nvidia(object):

    DEBUG = True

    def __init__(self, template_file="xorg.template"):
        # Note: check installed Nvidia GPUs
        # nvidia-xconfig --query-gpu-info
        # nvidia-smi -L
        # nvidia-settings -q gpus

        # check if we are running as root
        if os.geteuid() != 0:
            print("[-] Script not running as root!")
            sys.exit(1)

        # create a list of installed GPUs
        print("[*] Identifying installed GPUs")
        self._gpus = list()
        self._find_gpus()

        print("[*] Detected {} GPU(s)".format(len(self._gpus)))

        # switch gpus to persistent mode
        print("[*] Enabling persistent mode")
        self._is_persistent = False
        self._set_persistent()

        # load xorg.conf template
        # print("[*] Loading template xorg.conf")
        # self._template = ""
        # self._load_template(template_file)

        # create instead of lead the xconf
        # TODO change, unreliable
        self._xorg_conf = tempfile.NamedTemporaryFile()
        print("[*] Preparing xorg.conf at '{}'".format(self._xorg_conf.name))
        self._create_xord_conf()

        print("[*] Determine available performance levels")
        self._find_perf()


    def __del__(self):
        try:
            self._xorg_conf.close()
        except:
            pass


    def apply(self, uuid, cfg):
        gpu = next((x for x in self._gpus if x.uuid == uuid), None)

        if gpu is None:
            print("[-] No GPU with uuid='{}' available".format(uuid))
            return

        print("[*] Configuring {}".format(str(gpu)))

        # set power limits
        if "power" in cfg and in_limit(cfg["power"], gpu.limits["power"]):
            exitcode, output = subprocess.getstatusoutput("nvidia-smi -i {0} -pl {1}".format(gpu.index, cfg["power"]))

            if exitcode != 0:
                print("[!] Failed to adjust power limit")
        else:
            print("[!] Invalid power setting")


        # set fan speed and memory/clock offset
        q = ""

        if "fan_speed" in cfg and in_limit(cfg["fan_speed"], gpu.limits["fan_speed"]):
            q += ("-a [gpu:{0}]/GPUFanControlState=1 "
                  "-a [fan:{0}]/GPUTargetFanSpeed={1} ").format(gpu.index, cfg["fan_speed"])
        else:
            print("[!] Invalid fan_speed setting")

        # TODO if we dont overclock at all, this option is added but actually not needed
        if "clock_offset" in cfg or "memory_offset" in cfg:
            q += ("-a [gpu:{0}]/GPUPowerMizerMode=1 ").format(gpu.index)

        if "clock_offset" in cfg and in_limit(cfg["clock_offset"], gpu.limits["clock_offset"]):
            q += ("-a [gpu:{0}]/GPUGraphicsClockOffset[{1}]={2} ").format(gpu.index, gpu.perf, cfg["clock_offset"])
        else:
            print("[!] Invalid clock_offset setting")

        if "memory_offset" in cfg and in_limit(cfg["memory_offset"], gpu.limits["memory_offset"]):
            q += ("-a [gpu:{0}]/GPUMemoryTransferRateOffset[{1}]={2} ").format(gpu.index, gpu.perf, cfg["memory_offset"])
        else:
            print("[!] Invalid memory_offset setting")

        q = q.rstrip(" ")
        output = self._query(q)

        # TODO double check output to see if settings were applied
        print(output)


    def _query(self, q):
        """Run a nvidia-settings command in headless Xserver

        Example for q:
        "-a [gpu:1]/GPUFanControlState=1"

        :param q: nvidia-settings arguments, the index of the gpu is replaced with
        :type q: str
        :return:
        """
        assert isinstance(q, str)

        s = ("xinit /usr/bin/nvidia-settings {}"
             " -- :1 -once -config {}").format(q, self._xorg_conf.name)

        exitcode, output = subprocess.getstatusoutput(s)

        if exitcode != 0:
            print("[-] Errors occured while configuring the device.")

        return output


    def _find_perf(self):
        for gpu in self._gpus:
            output = self._query("-q [gpu:{}]/GPUPerfModes".format(gpu.index))

            if output.find("Attribute 'GPUPerfModes'"):
                matches = re.findall("perf=(\d{1,2})"
                                     , output)

                if matches:
                    p = max([int(i) for i in matches])
                    assert (p >= 0)
                    gpu._perf = p


    # def run(self, config, edid_file="/home/share/mining/edid.bin"):
    #     if not os.path.exists(edid_file):
    #         print("[-] edid file does not exist")
    #         sys.exit(1)
    #
    #     print("[*] Applying configurations")
    #     for g in self._gpus:
    #         if g.uuid not in config:
    #             print("[!] Don't have configuration for {}. Skipped".format(g))
    #             continue
    #
    #         #self._fan_setup()
    #         self._configure_gpu(g, GPUConfig(config[g.uuid]), edid_file)


    def _set_persistent(self):
        if not self._is_persistent:
            exitcode, output = subprocess.getstatusoutput("nvidia-smi -pm 1")
            if exitcode != 0:
                print("[!] Could not set GPUs to persistent mode.")

            self._is_persistent = True


    # def _load_template(self, template_file):
    #     if not os.path.exists(template_file):
    #         print("[-] Cannot find xorg.conf template file")
    #         sys.exit(1)
    #
    #     with open(template_file, "rb") as f:
    #         self._template = f.read().decode("utf-8")


    def _create_xord_conf(self):
        exitcode, output = subprocess.getstatusoutput(("nvidia-xconfig -a --cool-bits=28 "
                                                       "--allow-empty-initial-configuration -o {}").format(self._xorg_conf.name))

        if exitcode != 0:
            print("[-] Could not create proper xord.conf")
            sys.exit(1)


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


    # def _configure_gpu(self, gpu, cfg, edid_file):
    #     """Note: This function needs to run as root!
    #
    #     :param gpu:
    #     :type gpu: GPU
    #     :param cfg:
    #     :type cfg: GPUConfig
    #     :param edid_file: path to edid file
    #     :type edid_file: str
    #     """
    #     assert isinstance(gpu, GPU)
    #     assert isinstance(cfg, GPUConfig)
    #
    #     self._set_persistent()
    #
    #     print("[*] Configuring GPU:{}".format(gpu.index))
    #     with tempfile.NamedTemporaryFile() as xcfg:
    #         # prepare 'fake' xorg.conf
    #         xcfg.write(self._template.format(edid_file, gpu.slot).encode("utf-8"))
    #         xcfg.flush()
    #
    #         s = ("xinit /usr/bin/nvidia-settings{}"
    #              " -- :1 -once -config {}").format(str(cfg), xcfg.name).format(gpu.index)
    #
    #         exitcode, output = subprocess.getstatusoutput(s)
    #         print(output)
    #
    #
    #         if exitcode != 0:
    #             print("[-] Errors occured while configuring the device.")


def main():
    # Check availability of dependencies in PATH
    assert bool(shutil.which("xinit"))
    assert bool(shutil.which("nvidia-settings"))
    assert bool(shutil.which("nvidia-xconfig"))
    assert bool(shutil.which("nvidia-smi"))

    n = Nvidia()
    # n.run(config.C, "/home/share/mining/scripts/edid.bin")

    import config

    for uuid, cfg in config.C.items():
        n.apply(uuid, cfg)





if __name__ == "__main__":
    main()
