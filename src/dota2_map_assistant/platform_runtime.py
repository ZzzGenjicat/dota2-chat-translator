"""Platform-specific resource policy. Translation itself is portable."""
from __future__ import annotations

import os
import sys


def configure_low_impact_runtime() -> None:
    # Do not let idle OpenMP threads spin while Dota needs the CPU.
    os.environ.setdefault('OMP_WAIT_POLICY', 'PASSIVE')
    os.environ.setdefault('KMP_BLOCKTIME', '0')
    os.environ.setdefault('CT2_PACKED_GEMM', '0')  # Trade a little speed for less RAM.
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00004000)  # BELOW_NORMAL
    elif sys.platform == 'darwin':
        try:
            if os.getpriority(os.PRIO_PROCESS, 0) < 5:
                os.setpriority(os.PRIO_PROCESS, 0, 5)
        except OSError:
            pass


def offline_install_hint() -> str:
    if sys.platform == 'darwin':
        return '请重新安装包含本地模型的 Mac 安装包；源码运行请先执行 install_offline.command'
    return '请运行 install_offline.ps1（64 位 Python 3.12）'
