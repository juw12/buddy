"""
Pixel-exact pointing for Buddy.

The vision model's [POINT] tags land near the right element but rarely dead on it (it sees a
downscaled screenshot). Windows UI Automation knows every visible button, tab, menu item and text box
and its exact rectangle, so Buddy snaps each point onto the element the model meant: the label has to
match the element's name, or the point has to fall inside a small clickable element.

Works in most Windows apps (Office, browsers, Electron apps, Explorer, the taskbar, settings...).
Apps that draw their own UI (games, Blender, some CAD) expose little; there the model's point is used.
"""
import ctypes
import logging
import math
import os
import re
import time
from ctypes import wintypes

MAX_ELEMENTS_PER_WINDOW = 2000
SNAPSHOT_BUDGET_S = 4.0

# UIA control type ids -> the word the model sees. Containers (pane, group, window...) are left out.
KINDS = {
    50000: "button", 50002: "checkbox", 50003: "dropdown", 50004: "text box", 50005: "link",
    50006: "icon", 50007: "list item", 50011: "menu item", 50013: "option", 50015: "slider",
    50019: "tab", 50020: "text", 50024: "tree item", 50029: "cell", 50031: "button",
    50035: "column header", 50016: "spinner",
}
# Things you click rank above plain text when nothing else decides
CLICKABLE = {"button", "checkbox", "dropdown", "text box", "link", "icon", "list item", "menu item",
             "option", "tab", "tree item", "slider", "spinner"}
STOP_WORDS = set("""a an the to of in on at for and or is it this that you your i me my can how do does
where what which then click press select open use go find here there with from by be will button tab
menu icon field bar""".split())
SNAP_RADIUS = 0.12            # max distance (fraction of the screen diagonal) for a name match
SMALL_ELEMENT = 0.03          # an element under 3% of the screen is a precise target on its own

user32 = ctypes.windll.user32
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
GA_ROOT = 2
OWN_PID = os.getpid()


def _root_at(x, y):
    hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    return user32.GetAncestor(hwnd, GA_ROOT) if hwnd else None


def _visible_at(hwnd, x, y):
    root = _root_at(x, y)
    if root == hwnd:
        return True
    if root:                                   # Buddy's own overlay/window doesn't count as covering
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
        return pid.value == OWN_PID
    return False


def _window_is_visible(hwnd, rect, monitor_rect):
    """Is any part of this top-level window on top somewhere on the monitor? (5x5 grid test)"""
    left, top = max(rect[0], monitor_rect[0]), max(rect[1], monitor_rect[1])
    right, bottom = min(rect[2], monitor_rect[2]), min(rect[3], monitor_rect[3])
    if right - left < 4 or bottom - top < 4:
        return False
    for i in range(5):
        for j in range(5):
            x = left + (right - left) * (i + 0.5) / 5
            y = top + (bottom - top) * (j + 0.5) / 5
            if _visible_at(hwnd, x, y):
                return True
    return False


def snapshot(monitor_rect, exclude_pid=None):
    """Visible UI elements on one monitor (physical pixels), front window first.

    Returns dicts: name, kind, rect (l, t, r, b in physical pixels), window, foreground.
    Run it on a worker thread; it takes ~0.1-0.4 s per visible window.
    """
    from keyit_buddy import _uia, physical_pixels

    exclude_pid = os.getpid() if exclude_pid is None else exclude_pid
    started = time.perf_counter()
    targets = []
    with physical_pixels():
        uia, automation = _uia()
        request = automation.CreateCacheRequest()
        for prop in (uia.UIA_NamePropertyId, uia.UIA_ControlTypePropertyId,
                     uia.UIA_BoundingRectanglePropertyId, uia.UIA_NativeWindowHandlePropertyId,
                     uia.UIA_ProcessIdPropertyId):
            request.AddProperty(prop)
        on_screen = automation.CreatePropertyCondition(uia.UIA_IsOffscreenPropertyId, False)
        condition = automation.CreateAndCondition(on_screen, automation.ControlViewCondition)
        request.TreeFilter = condition
        foreground = user32.GetForegroundWindow()
        windows = automation.GetRootElement().FindAllBuildCache(uia.TreeScope_Children, condition, request)
        for index in range(windows.Length if windows else 0):
            if time.perf_counter() - started > SNAPSHOT_BUDGET_S:
                logging.info("UI snapshot stopped early after %.1fs", time.perf_counter() - started)
                break
            window = windows.GetElement(index)
            hwnd = window.CachedNativeWindowHandle
            if not hwnd or window.CachedProcessId == exclude_pid:
                continue
            box = window.CachedBoundingRectangle
            window_rect = (box.left, box.top, box.right, box.bottom)
            if not _window_is_visible(hwnd, window_rect, monitor_rect):
                continue
            title = window.CachedName or ""
            found = window.FindAllBuildCache(uia.TreeScope_Descendants, condition, request)
            for j in range(min(found.Length if found else 0, MAX_ELEMENTS_PER_WINDOW)):
                element = found.GetElement(j)
                kind = KINDS.get(element.CachedControlType)
                if kind is None:
                    continue
                name = " ".join((element.CachedName or "").split())
                if not name and kind != "text box":
                    continue
                rect = element.CachedBoundingRectangle
                left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
                if right - left < 3 or bottom - top < 3:
                    continue
                cx, cy = (left + right) / 2, (top + bottom) / 2
                if not (monitor_rect[0] <= cx < monitor_rect[2] and monitor_rect[1] <= cy < monitor_rect[3]):
                    continue
                if not _visible_at(hwnd, cx, cy):          # covered by another window
                    continue
                targets.append({"name": name, "kind": kind, "rect": (left, top, right, bottom),
                                "window": title, "foreground": hwnd == foreground})
    logging.info("UI snapshot: %d visible elements in %.2fs", len(targets), time.perf_counter() - started)
    return targets


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1 and w not in STOP_WORDS}


def _name_match(label, name):
    """0..1: how well the model's label matches an element name."""
    label_words, name_words = _words(label), _words(name)
    if not label_words or not name_words:
        return 0.0
    if label.strip().lower() == name.strip().lower():
        return 1.0
    shared = len(label_words & name_words)
    return shared / max(len(label_words), min(len(name_words), 4))


def snap(point, targets, monitor_rect):
    """Move a (x, y, label) 0..1000 point onto the UI element it most likely means.
    Returns (point, target_or_None); the point is unchanged when nothing fits well."""
    if not targets:
        return point, None
    x_norm, y_norm, label = point
    left, top, right, bottom = monitor_rect
    width, height = right - left, bottom - top
    px, py = left + x_norm / 1000.0 * width, top + y_norm / 1000.0 * height
    diagonal = math.hypot(width, height)
    screen_area = float(width * height)
    best, best_score = None, 0.0
    for target in targets:
        t_left, t_top, t_right, t_bottom = target["rect"]
        area = (t_right - t_left) * (t_bottom - t_top) / screen_area
        if area > 0.25:                                   # whole panes / documents are no target
            continue
        dx = max(t_left - px, 0, px - t_right)
        dy = max(t_top - py, 0, py - t_bottom)
        distance = math.hypot(dx, dy) / diagonal
        inside = distance == 0
        match = _name_match(label, target["name"]) if label else 0.0
        if match > 0 and distance <= SNAP_RADIUS:
            score = 1.0 + 2.0 * match - 4.0 * distance - area
        elif inside and area <= SMALL_ELEMENT and target["kind"] in CLICKABLE:
            score = 0.8 - 10.0 * area                    # the smallest clickable thing under the point
        else:
            continue
        if score > best_score:
            best, best_score = target, score
    if best is None:
        return point, None
    return to_normalized(best, monitor_rect, label), best


def to_normalized(target, monitor_rect, label=None):
    """Element center as Buddy's 0..1000 screenshot point (x, y, label)."""
    left, top, right, bottom = target["rect"]
    width = monitor_rect[2] - monitor_rect[0]
    height = monitor_rect[3] - monitor_rect[1]
    x = ((left + right) / 2 - monitor_rect[0]) / width * 1000.0
    y = ((top + bottom) / 2 - monitor_rect[1]) / height * 1000.0
    return (x, y, label if label else target["name"][:30])
