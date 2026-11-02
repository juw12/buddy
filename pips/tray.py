"""
tray.py - Pips in the Windows notification area (next to the clock) + a global "find my pet" hotkey.

Why: the pet and the card are borderless windows without a taskbar button, so once the pet is hidden
(or wandered off-screen) there was nothing to click. The tray icon is always there while Pips runs:

    left click / balloon click  -> "summon"   (bring the pet back)
    right click                 -> menu: bring back / focus timer / Friends & chat / quit
    Ctrl+Alt+P (anywhere)       -> "hotkey"   (falls back to Ctrl+Alt+Shift+P if another app owns it)

Pure ctypes, no extra packages. All Win32 work happens on one background thread with its own message
loop; clicks are handed to the Tk main thread through a queue (`drain()`), so Tk is never touched off
the main thread.
"""

from __future__ import annotations

import atexit
import logging
import queue
import sys
import threading

logger = logging.getLogger("tray")

WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_CONTEXTMENU = 0x007B
WM_HOTKEY = 0x0312
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_USER = 0x0400
WM_TRAY = WM_USER + 20
NIN_BALLOONUSERCLICK = WM_USER + 5

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO, NIIF_NOSOUND = 0x01, 0x10

MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD = 0x0002, 0x0080, 0x0100
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
SM_CXSMICON, SM_CYSMICON = 49, 50
IDI_APPLICATION = 32512
ERROR_CLASS_ALREADY_EXISTS = 1410

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000
VK_P = 0x50
HOTKEY_ID = 0x5150
# tried in order until one is free
HOTKEYS = (
    (MOD_CONTROL | MOD_ALT, VK_P, "Ctrl+Alt+P"),
    (MOD_CONTROL | MOD_ALT | MOD_SHIFT, VK_P, "Ctrl+Alt+Shift+P"),
)
MENU = ("summon", "card", "friends", None, "quit")   # None = separator; "summon" is the bold default
CLASS_NAME = "PipsTrayWindow"

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes as wt

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wt.UINT),
            ("style", wt.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wt.HINSTANCE),
            ("hIcon", wt.HICON),
            ("hCursor", wt.HANDLE),
            ("hbrBackground", wt.HANDLE),
            ("lpszMenuName", wt.LPCWSTR),
            ("lpszClassName", wt.LPCWSTR),
            ("hIconSm", wt.HICON),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wt.DWORD),
            ("hWnd", wt.HWND),
            ("uID", wt.UINT),
            ("uFlags", wt.UINT),
            ("uCallbackMessage", wt.UINT),
            ("hIcon", wt.HICON),
            ("szTip", wt.WCHAR * 128),
            ("dwState", wt.DWORD),
            ("dwStateMask", wt.DWORD),
            ("szInfo", wt.WCHAR * 256),
            ("uVersion", wt.UINT),          # union with uTimeout
            ("szInfoTitle", wt.WCHAR * 64),
            ("dwInfoFlags", wt.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wt.HICON),
        ]


def _win32():
    """user32 / shell32 / kernel32 with the prototypes this module needs (own DLL objects, so the
    argtypes never clash with other modules)."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.RegisterClassExW.restype = wt.ATOM
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    user32.CreateWindowExW.restype = wt.HWND
    user32.DestroyWindow.argtypes = [wt.HWND]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
    user32.RegisterWindowMessageW.restype = wt.UINT
    user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.LoadImageW.restype = wt.HANDLE
    user32.LoadIconW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
    user32.LoadIconW.restype = wt.HICON
    user32.DestroyIcon.argtypes = [wt.HICON]
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.CreatePopupMenu.restype = wt.HMENU
    user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
    user32.SetMenuDefaultItem.argtypes = [wt.HMENU, wt.UINT, wt.UINT]
    user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND,
                                      ctypes.c_void_p]
    user32.TrackPopupMenu.restype = ctypes.c_int
    user32.DestroyMenu.argtypes = [wt.HMENU]
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
    user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
    user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
    shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = wt.BOOL
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wt.HMODULE
    return user32, shell32, kernel32


class TrayIcon:
    """The Pips icon in the notification area. Start it once; poll `drain()` from the Tk thread."""

    def __init__(self, icon_path=None, tooltip="Pips", labels=None, hotkey=True):
        self.icon_path = str(icon_path) if icon_path else ""
        self.tooltip = tooltip
        # labels: dict or callable -> {"summon": ..., "card": ..., "friends": ..., "quit": ...}
        # (a callable is evaluated each time the menu opens, so names / language stay current)
        self.labels = labels or {}
        self.want_hotkey = hotkey
        self.hotkey_label = ""          # e.g. "Ctrl+Alt+P" once registered
        self.ok = False                 # icon is in the notification area
        self.commands: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._thread = None
        self._hwnd = None
        self._icon = None
        self._own_icon = False
        self._taskbar_created = 0
        self._wndproc = None
        self._user32 = self._shell32 = self._kernel32 = None
        self._class_name = f"{CLASS_NAME}_{id(self):x}"   # own class: a restarted tray never reuses a dead WNDPROC

    # ---- public (any thread) ----
    def start(self, timeout: float = 3.0) -> bool:
        if sys.platform != "win32" or self._thread is not None:
            return self.ok
        self._thread = threading.Thread(target=self._run, name="pips-tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        if self.ok:
            atexit.register(self.stop)
        return self.ok

    def drain(self) -> list:
        """Commands clicked since the last call: "summon", "hotkey", "card", "friends", "quit"."""
        out = []
        while True:
            try:
                out.append(self.commands.get_nowait())
            except queue.Empty:
                return out

    def notify(self, title: str, text: str) -> bool:
        """Balloon / Windows notification from the tray icon (no sound)."""
        if not self.ok:
            return False
        nid = self._nid(NIF_INFO)
        nid.szInfoTitle = (title or "")[:63]
        nid.szInfo = (text or " ")[:255]
        nid.dwInfoFlags = NIIF_INFO | NIIF_NOSOUND
        return bool(self._shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))

    def set_tooltip(self, text: str) -> bool:
        self.tooltip = text or "Pips"
        if not self.ok:
            return False
        nid = self._nid(NIF_TIP)
        return bool(self._shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))

    def stop(self):
        hwnd = self._hwnd
        if hwnd and self._user32 is not None:
            self._user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self.ok = False

    # ---- tray thread ----
    def _run(self):
        try:
            self._setup()
        except Exception:
            logger.exception("tray: setup failed")
            self._ready.set()
            return
        self._ready.set()
        msg = wt.MSG()
        user32 = self._user32
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _setup(self):
        self._user32, self._shell32, self._kernel32 = _win32()
        user32 = self._user32
        hinst = self._kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self._proc)          # keep a reference for the lifetime of the window
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = self._class_name
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            if ctypes.get_last_error() != ERROR_CLASS_ALREADY_EXISTS:
                raise OSError(f"RegisterClassExW failed ({ctypes.get_last_error()})")
        self._hwnd = user32.CreateWindowExW(0, self._class_name, "Pips", 0, 0, 0, 0, 0, None, None, hinst, None)
        if not self._hwnd:
            raise OSError(f"CreateWindowExW failed ({ctypes.get_last_error()})")
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self._icon = self._load_icon()
        self.ok = self._add_icon()
        if not self.ok:
            logger.warning("tray: Shell_NotifyIcon(NIM_ADD) failed (%s)", ctypes.get_last_error())
        if self.want_hotkey:
            for mods, vk, label in HOTKEYS:
                if user32.RegisterHotKey(self._hwnd, HOTKEY_ID, mods | MOD_NOREPEAT, vk):
                    self.hotkey_label = label
                    break
            if not self.hotkey_label:
                logger.info("tray: no free hotkey (Ctrl+Alt+P taken)")

    def _load_icon(self):
        user32 = self._user32
        if self.icon_path:
            cx = user32.GetSystemMetrics(SM_CXSMICON) or 16
            cy = user32.GetSystemMetrics(SM_CYSMICON) or 16
            handle = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, cx, cy, LR_LOADFROMFILE)
            if handle:
                self._own_icon = True
                return handle
        return user32.LoadIconW(None, IDI_APPLICATION)

    def _nid(self, flags):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._icon
        nid.szTip = (self.tooltip or "Pips")[:127]
        return nid

    def _add_icon(self) -> bool:
        nid = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        return bool(self._shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

    def _remove_icon(self):
        nid = self._nid(0)
        self._shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        if self._own_icon and self._icon:
            self._user32.DestroyIcon(self._icon)
            self._icon = None

    def _labels(self) -> dict:
        try:
            return dict(self.labels() if callable(self.labels) else self.labels)
        except Exception:
            logger.exception("tray: labels failed")
            return {}

    def _popup_menu(self):
        user32 = self._user32
        labels = self._labels()
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        ids = {}
        chosen = 0
        try:
            for command in MENU:
                if command is None:
                    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                    continue
                item_id = len(ids) + 1
                ids[item_id] = command
                text = str(labels.get(command, command)).replace("&", "&&")   # "&" = mnemonic in Win32 menus
                user32.AppendMenuW(menu, MF_STRING, item_id, text)
            user32.SetMenuDefaultItem(menu, 1, False)
            point = wt.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.SetForegroundWindow(self._hwnd)      # so the menu closes when you click elsewhere
            chosen = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD,
                                           point.x, point.y, 0, self._hwnd, None)
            user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(menu)
        if chosen in ids:
            self.commands.put(ids[chosen])

    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                event = lparam & 0xFFFF
                if event in (WM_LBUTTONUP, NIN_BALLOONUSERCLICK):
                    self.commands.put("summon")
                elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._popup_menu()
                return 0
            if msg == WM_HOTKEY:
                self.commands.put("hotkey")
                return 0
            if self._taskbar_created and msg == self._taskbar_created:
                self.ok = self._add_icon()               # Explorer restarted: put the icon back
                return 0
            if msg == WM_CLOSE:
                self._user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                self._remove_icon()
                if self.hotkey_label:
                    self._user32.UnregisterHotKey(hwnd, HOTKEY_ID)
                self._hwnd = None
                self.ok = False
                self._user32.PostQuitMessage(0)
                return 0
        except Exception:
            logger.exception("tray: message %s failed", msg)
        return self._user32.DefWindowProcW(hwnd, msg, wparam, lparam)
