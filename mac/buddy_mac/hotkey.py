"""Global hotkey for macOS through Carbon's RegisterEventHotKey.

Why Carbon: it reports both press and release (needed for hold-to-talk), it swallows the keystroke so the
front app doesn't also get it, and unlike an NSEvent global monitor it needs no Accessibility permission.
Cocoa apps still dispatch these events, so it works inside a normal NSApplication run loop.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging

KEY_SPACE = 49
CMD_KEY = 0x0100
SHIFT_KEY = 0x0200
OPTION_KEY = 0x0800
CONTROL_KEY = 0x1000

K_EVENT_HOT_KEY_PRESSED = 5
K_EVENT_HOT_KEY_RELEASED = 6
K_EVENT_PARAM_DIRECT_OBJECT = 0x2D2D2D2D     # '----'
TYPE_EVENT_HOT_KEY_ID = 0x686B6964           # 'hkid'


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


K_EVENT_CLASS_KEYBOARD = _fourcc("keyb")
SIGNATURE = _fourcc("BDDY")


class EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


EVENT_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


class HotKey:
    """Calls on_press() / on_release() on the main thread for one key combo (default Control+Option+Space)."""

    def __init__(self, on_press, on_release, key_code=KEY_SPACE, modifiers=CONTROL_KEY | OPTION_KEY):
        self.on_press = on_press
        self.on_release = on_release
        self.key_code = key_code
        self.modifiers = modifiers
        self.error = None
        self._handler = EVENT_HANDLER(self._handle)          # keep a reference: Carbon holds a raw pointer
        self._hotkey_ref = ctypes.c_void_p()
        self._handler_ref = ctypes.c_void_p()
        try:
            self._register()
        except Exception as error:                            # noqa: BLE001 - report, keep the app running
            self.error = str(error)
            logging.exception("hotkey registration failed")

    @property
    def ok(self) -> bool:
        return self.error is None

    def _register(self):
        path = ctypes.util.find_library("Carbon") or "/System/Library/Frameworks/Carbon.framework/Carbon"
        carbon = ctypes.CDLL(path)
        self._carbon = carbon
        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        carbon.GetApplicationEventTarget.argtypes = []
        carbon.InstallEventHandler.restype = ctypes.c_int32
        carbon.InstallEventHandler.argtypes = [
            ctypes.c_void_p, EVENT_HANDLER, ctypes.c_uint32,
            ctypes.POINTER(EventTypeSpec), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.RegisterEventHotKey.restype = ctypes.c_int32
        carbon.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, EventHotKeyID, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.UnregisterEventHotKey.restype = ctypes.c_int32
        carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        carbon.GetEventKind.restype = ctypes.c_uint32
        carbon.GetEventKind.argtypes = [ctypes.c_void_p]

        target = carbon.GetApplicationEventTarget()
        specs = (EventTypeSpec * 2)(
            EventTypeSpec(K_EVENT_CLASS_KEYBOARD, K_EVENT_HOT_KEY_PRESSED),
            EventTypeSpec(K_EVENT_CLASS_KEYBOARD, K_EVENT_HOT_KEY_RELEASED),
        )
        status = carbon.InstallEventHandler(target, self._handler, 2, specs, None, ctypes.byref(self._handler_ref))
        if status != 0:
            raise OSError(f"InstallEventHandler failed ({status})")
        status = carbon.RegisterEventHotKey(
            self.key_code, self.modifiers, EventHotKeyID(SIGNATURE, 1), target, 0, ctypes.byref(self._hotkey_ref)
        )
        if status != 0:
            # -9878 = eventHotKeyExistsErr: another app already owns this combo
            raise OSError(f"RegisterEventHotKey failed ({status}); is another app using Control+Option+Space?")
        logging.info("hotkey registered: key %s modifiers %#x", self.key_code, self.modifiers)

    def _handle(self, next_handler, event, user_data):
        try:
            kind = self._carbon.GetEventKind(event)
            if kind == K_EVENT_HOT_KEY_PRESSED:
                self.on_press()
            elif kind == K_EVENT_HOT_KEY_RELEASED:
                self.on_release()
        except Exception:                                    # noqa: BLE001 - never let an error reach Carbon
            logging.exception("hotkey handler failed")
        return 0

    def close(self):
        if self._hotkey_ref:
            self._carbon.UnregisterEventHotKey(self._hotkey_ref)
            self._hotkey_ref = ctypes.c_void_p()
