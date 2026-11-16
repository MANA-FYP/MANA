"""The laptop's own Wi-Fi signal (RSSI, dBm), read through Windows' WLAN API, for the indoor page.

Why it is shown, and its limit: Wi-Fi sensing (people and movement from how a wall-bounced radio
signal changes) needs the channel state (CSI) of every subcarrier, many times a second. Windows
gives a program only the RSSI, one whole number in dBm, and refreshes it rarely (on this laptop it
stayed the same over 20 s read ten times a second). The page plots it and counts how often it
changed, so the limit is visible. Not on Windows, or with no Wi-Fi adapter: None.
"""

from __future__ import annotations

import sys
import time
from collections import deque

RSSI_OPCODE = 0x10000102  # wlan_intf_opcode_rssi


class LaptopWifi:
    def __init__(self, keep: int = 300):
        self.samples: deque = deque(maxlen=keep)  # (time, dBm)
        self.changes = 0
        self.name = None
        self._h = self._guid = None
        self._open()

    def _open(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes
            import ctypes.wintypes as wt

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("a", wt.DWORD),
                    ("b", wt.WORD),
                    ("c", wt.WORD),
                    ("d", ctypes.c_ubyte * 8),
                ]

            class Info(ctypes.Structure):
                _fields_ = [
                    ("guid", GUID),
                    ("desc", ctypes.c_wchar * 256),
                    ("state", ctypes.c_uint),
                ]

            class InfoList(ctypes.Structure):
                _fields_ = [("n", wt.DWORD), ("i", wt.DWORD), ("items", Info * 1)]

            api = ctypes.windll.wlanapi
            ver, h = wt.DWORD(), wt.HANDLE()
            if api.WlanOpenHandle(2, None, ctypes.byref(ver), ctypes.byref(h)) != 0:
                return
            lst = ctypes.POINTER(InfoList)()
            if api.WlanEnumInterfaces(h, None, ctypes.byref(lst)) != 0 or lst.contents.n == 0:
                return
            info = lst.contents.items[0]
            self._api, self._ct, self._wt = api, ctypes, wt
            self._h, self._guid, self.name = h, GUID.from_buffer_copy(info.guid), info.desc
        except (OSError, AttributeError):
            self._h = None

    def read(self) -> int | None:
        """The RSSI now, in dBm; None when not connected or not readable."""
        if self._h is None:
            return None
        ct, wt = self._ct, self._wt
        size, data, kind = wt.DWORD(), ct.c_void_p(), ct.c_uint()
        r = self._api.WlanQueryInterface(
            self._h,
            ct.byref(self._guid),
            RSSI_OPCODE,
            None,
            ct.byref(size),
            ct.byref(data),
            ct.byref(kind),
        )
        if r != 0:
            return None
        v = ct.cast(data, ct.POINTER(ct.c_long)).contents.value
        self._api.WlanFreeMemory(data)
        return int(v)

    def sample(self, now: float | None = None) -> None:
        v = self.read()
        if v is None:
            return
        if self.samples and self.samples[-1][1] != v:
            self.changes += 1
        self.samples.append((round(time.monotonic() if now is None else now, 2), v))

    def view(self) -> dict | None:
        if self._h is None:
            return None
        s = list(self.samples)
        span = s[-1][0] - s[0][0] if len(s) > 1 else 0.0
        return {
            "adapter": self.name,
            "dbm": s[-1][1] if s else None,
            "samples": s[-120:],
            "reads": len(s),
            "changes": self.changes,
            "span_s": round(span, 1),
        }
