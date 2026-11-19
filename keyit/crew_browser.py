"""Where a browser that a program steers is clicked and typed in, for the crew's task watch (crew_tasks.py).

A browser started with --remote-debugging-port (Playwright over CDP, an AI agent's own sign-up browser) is
worked by a program: its clicks never move your mouse, so nothing on screen shows them. This asks the browser
itself, over that same port. In every page a small listener (in its own script world: the page cannot see it)
reports "a click at this spot on a button" or "typing in this box". Never what is typed, never the text of a
button or a link, never the address. The spot is turned into a screen point and handed to the task watch,
which lets a crew pointer fly there.

watch(pid, command, on_action, alive, log) blocks while the browser lives; run it in its own thread.

The browser speaks WebSocket on that port. Buddy ships no WebSocket library, and this needs very little of
one, so the few lines it needs are here (WebSocket: text messages, no extensions)."""
import base64
import ctypes
import hashlib
import json
import os
import re
import socket
import struct
import time
import urllib.request
from ctypes import wintypes

WORLD = "buddy-crew"
BINDING = "buddySaw"
OWN_CLICK_PX = 10          # a click this close to your real mouse is your own click, not the agent's
OWN_KEYS_S = 0.3           # typing this soon after your own keyboard or mouse, in the window in front, is yours
GA_ROOT = 2
MONITOR_DEFAULTTONULL = 0
VK_LBUTTON = 0x01

# Runs in every frame of every tab. It reports a place and a kind, nothing else.
LISTENER = r"""(() => {
  if (globalThis.__buddyListens) return;
  globalThis.__buddyListens = true;
  const inTab = (x, y) => {                 // this frame's corner -> the tab's corner; null across sites
    let w = window;
    for (let depth = 0; w !== w.top && depth < 20; depth++) {
      let frame = null;
      try { frame = w.frameElement; } catch (error) {}
      if (!frame) return null;
      const box = frame.getBoundingClientRect();
      x += box.left + frame.clientLeft;
      y += box.top + frame.clientTop;
      w = w.parent;
    }
    return [x, y];
  };
  const kindOf = (element) => {
    if (!element || !element.closest) return '';
    const near = element.closest('a,button,input,select,textarea,summary,[role],[contenteditable]') || element;
    const name = (near.tagName || '').toLowerCase();
    const role = (near.getAttribute && near.getAttribute('role')) || '';
    const type = name === 'input' ? (near.type || 'text').toLowerCase() : '';
    if (['checkbox', 'radio'].includes(type) || ['checkbox', 'radio', 'switch'].includes(role)) return 'tick box';
    if (name === 'button' || role === 'button' || ['submit', 'button', 'reset', 'image'].includes(type)) return 'button';
    if (name === 'a' || role === 'link') return 'link';
    if (name === 'select' || ['combobox', 'listbox', 'option', 'menuitem'].includes(role)) return 'list';
    if (role === 'tab') return 'tab';
    if (name === 'input' || name === 'textarea' || near.isContentEditable) return 'box';
    return '';
  };
  const tell = (action, x, y, what) => {
    if (document.visibilityState !== 'visible') return;
    const place = inTab(x, y);
    if (!place) return;
    try {
      buddySaw(JSON.stringify({action, what, x: place[0], y: place[1], scale: window.devicePixelRatio || 1}));
    } catch (error) {}
  };
  const target = (event) => (event.composedPath && event.composedPath()[0]) || event.target;
  addEventListener('mousedown', (event) => {
    if (event.button === 0) tell('click', event.clientX, event.clientY, kindOf(target(event)));
  }, true);
  let typedAt = 0;
  const typing = (event) => {
    const element = target(event);
    if (!element || !element.getBoundingClientRect || !['box', 'list'].includes(kindOf(element))) return;
    const now = Date.now();
    if (now - typedAt < 1500) return;
    const box = element.getBoundingClientRect();
    if (!box.width || !box.height) return;
    typedAt = now;
    tell('type', box.left + Math.min(box.width / 2, 80), box.top + box.height / 2, 'box');
  };
  addEventListener('input', typing, true);
  addEventListener('keydown', typing, true);
})()"""

user32 = ctypes.WinDLL("user32")                  # its own copy: the argument types below must not reach other modules
kernel32 = ctypes.WinDLL("kernel32")
ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [ENUM_PROC, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [wintypes.HWND, ENUM_PROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p


class WebSocket:
    """A WebSocket client for a browser's debugging port: whole text messages in and out."""

    MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, address, timeout=5):
        match = re.fullmatch(r"ws://([^/:]+)(?::(\d+))?(/.*)?", address)
        if not match:
            raise OSError(f"not a WebSocket address: {address[:60]}")
        host, port, path = match.group(1), int(match.group(2) or 80), match.group(3) or "/"
        self.buffer = bytearray()          # what came in and is not a whole frame yet (kept over a timeout)
        self.pieces = []                   # the frames so far of a message that comes in several
        self.socket = socket.create_connection((host, port), timeout=timeout)
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            # no Origin line: a browser only lets a program without one use its debugging port
            self.socket.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                                 f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                                 "Sec-WebSocket-Version: 13\r\n\r\n").encode())
            while b"\r\n\r\n" not in self.buffer:
                self._read_more()
            answer, _, rest = bytes(self.buffer).partition(b"\r\n\r\n")
            self.buffer = bytearray(rest)
            proof = base64.b64encode(hashlib.sha1((key + self.MAGIC).encode()).digest())
            if b" 101 " not in answer.split(b"\r\n")[0] or proof not in answer:
                raise ConnectionError("the browser did not accept the connection")
        except BaseException:
            self.socket.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        try:
            self.socket.close()
        except OSError:
            pass

    def _read_more(self):
        data = self.socket.recv(65536)
        if not data:
            raise ConnectionError("the browser closed the connection")
        self.buffer += data

    def _write(self, kind, data):
        size = len(data)
        head = bytes([0x80 | kind])                      # one whole frame
        if size < 126:
            head += bytes([0x80 | size])                 # 0x80: masked, as a client must
        elif size < 65536:
            head += bytes([0x80 | 126]) + struct.pack("!H", size)
        else:
            head += bytes([0x80 | 127]) + struct.pack("!Q", size)
        mask = os.urandom(4)
        long_mask = (mask * (size // 4 + 1))[:size]
        masked = (int.from_bytes(data, "big") ^ int.from_bytes(long_mask, "big")).to_bytes(size, "big")
        self.socket.settimeout(5)
        self.socket.sendall(head + mask + masked)

    def send(self, text):
        self._write(0x1, text.encode("utf-8"))

    def _frame(self):
        """(last one of its message, kind, data) of the next whole frame in the buffer, or None."""
        buffer = self.buffer
        if len(buffer) < 2:
            return None
        size, start = buffer[1] & 0x7F, 2
        if size == 126:
            if len(buffer) < 4:
                return None
            size, start = struct.unpack("!H", buffer[2:4])[0], 4
        elif size == 127:
            if len(buffer) < 10:
                return None
            size, start = struct.unpack("!Q", buffer[2:10])[0], 10
        mask = b""
        if buffer[1] & 0x80:                             # a browser does not mask; read it right all the same
            if len(buffer) < start + 4:
                return None
            mask, start = bytes(buffer[start:start + 4]), start + 4
        if len(buffer) < start + size:
            return None
        data = bytes(buffer[start:start + size])
        if mask:
            data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        last, kind = bool(buffer[0] & 0x80), buffer[0] & 0x0F
        del buffer[:start + size]
        return last, kind, data

    def recv(self, timeout):
        """The next whole message as text. TimeoutError when none came in time: what did come is kept."""
        deadline = time.monotonic() + timeout
        while True:
            frame = self._frame()
            if frame is None:
                wait = deadline - time.monotonic()
                if wait <= 0:
                    raise TimeoutError
                self.socket.settimeout(wait)
                self._read_more()                        # socket.timeout is TimeoutError
                continue
            last, kind, data = frame
            if kind == 0x9:
                self._write(0xA, data)                   # ping: answer it
            elif kind == 0x8:
                raise ConnectionError("the browser closed the connection")
            elif kind in (0x0, 0x1, 0x2):
                self.pieces.append(data)
                if last:
                    message, self.pieces = b"".join(self.pieces), []
                    return message.decode("utf-8", "replace")


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def _text(function, hwnd, size=300):
    buffer = ctypes.create_unicode_buffer(size)
    function(hwnd, buffer, size)
    return buffer.value


def page_areas(pid):
    """[(window, title, (left, top, right, bottom) of the web page in it)] for the browser's windows on screen."""
    found = []

    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        pages = []

        def visit_child(child, _):
            if user32.IsWindowVisible(child) and _text(user32.GetClassNameW, child) == "Chrome_RenderWidgetHostHWND":
                rect = wintypes.RECT()
                if user32.GetWindowRect(child, ctypes.byref(rect)) and rect.right - rect.left > 50:
                    pages.append((rect.left, rect.top, rect.right, rect.bottom))
            return True

        user32.EnumChildWindows(hwnd, ENUM_PROC(visit_child), 0)
        if pages:
            found.append((int(hwnd), _text(user32.GetWindowTextW, hwnd), pages[0]))
        return True

    user32.EnumWindows(ENUM_PROC(visit), 0)
    return found


def pick_area(areas, page_title, front):
    """The window that shows this tab: the one titled with it, else the one in front, else the only one."""
    wanted = " ".join(page_title.split()).lower()
    if wanted:
        for area in areas:
            if " ".join(area[1].replace("\u200b", "").split()).lower().startswith(wanted):
                return area
    for area in areas:
        if area[0] == front:
            return area
    return areas[0] if len(areas) == 1 else None


def screen_point(area, x, y, scale):
    """The screen point of a spot in the page, or None when that spot is outside what the window shows."""
    left, top, right, bottom = area[2]
    point = left + round(x * scale), top + round(y * scale)
    return point if left <= point[0] < right and top <= point[1] < bottom else None


def is_yours(action, hwnd, point):
    """Your own click or typing in that browser is not the agent's work."""
    if action == "click":
        cursor = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        return abs(cursor.x - point[0]) <= OWN_CLICK_PX and abs(cursor.y - point[1]) <= OWN_CLICK_PX
    last = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    user32.GetLastInputInfo(ctypes.byref(last))
    idle = ((kernel32.GetTickCount() - last.dwTime) & 0xFFFFFFFF) / 1000
    return idle < OWN_KEYS_S and int(user32.GetForegroundWindow() or 0) == hwnd


def on_top(hwnd, point):
    """True when `hwnd` is the window you see at this screen point. A point off every screen is never seen:
    Windows names the window that lies there all the same (a browser kept as a strip at the screen edge)."""
    point = wintypes.POINT(*point)
    if not user32.MonitorFromPoint(point, MONITOR_DEFAULTTONULL):
        return False
    seen = user32.WindowFromPoint(point)
    return bool(seen) and int(user32.GetAncestor(seen, GA_ROOT) or 0) == hwnd


def port_of(command):
    """The debugging port in a browser's command line; 0 = the browser picks one and writes it in its profile
    folder; None = it has none."""
    port = re.search(r"--remote-debugging-port=(\d+)", command)
    if not port:
        return None
    if int(port.group(1)):
        return int(port.group(1))
    folder = re.search(r'--user-data-dir=(?:"([^"]+)"|(\S+))', command)
    if not folder:
        return None
    try:
        with open((folder.group(1) or folder.group(2)) + "\\DevToolsActivePort", encoding="utf-8") as file:
            return int(file.readline().strip())
    except (OSError, ValueError):
        return None


class Browser:
    """One connection to a browser's debugging port."""

    def __init__(self, pid, port, on_action):
        self.pid = pid
        self.port = port
        self.on_action = on_action
        self.socket = None
        self.sent = 0
        self.asked = {}            # id of a question -> (what it was, session)
        self.titles = {}           # target -> the tab's title
        self.sessions = {}         # session -> target

    def send(self, method, params=None, session=None, why=None):
        self.sent += 1
        message = {"id": self.sent, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        if why:
            self.asked[self.sent] = (why, session)
        self.socket.send(json.dumps(message))

    def run(self, alive):
        straight = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # this PC, never a proxy
        with straight.open(f"http://127.0.0.1:{self.port}/json/version", timeout=3) as answer:
            address = json.load(answer)["webSocketDebuggerUrl"]
        with WebSocket(address) as self.socket:
            self.send("Target.setDiscoverTargets", {"discover": True})
            while alive():
                try:
                    message = json.loads(self.socket.recv(timeout=2))
                except TimeoutError:
                    continue
                self.handle(message)

    def handle(self, message):
        method, params, session = message.get("method"), message.get("params") or {}, message.get("sessionId")
        if method in ("Target.targetCreated", "Target.targetInfoChanged"):
            info = params["targetInfo"]
            if info.get("type") != "page":
                return
            known = info["targetId"] in self.titles
            self.titles[info["targetId"]] = info.get("title") or ""
            if not known:
                self.send("Target.attachToTarget", {"targetId": info["targetId"], "flatten": True})
        elif method == "Target.attachedToTarget":
            session = params["sessionId"]
            self.sessions[session] = params["targetInfo"]["targetId"]
            self.send("Page.enable", session=session)                # it then tells of every new page
            self.send("Page.getFrameTree", session=session, why="frames")       # the pages that are open already
        elif method == "Page.frameNavigated":
            self.listen_in(session, params["frame"]["id"])
        elif method == "Target.detachedFromTarget":
            self.sessions.pop(params.get("sessionId"), None)
        elif method == "Target.targetDestroyed":
            self.titles.pop(params.get("targetId"), None)
        elif method == "Runtime.bindingCalled" and params.get("name") == BINDING:
            self.saw(self.titles.get(self.sessions.get(session), ""), params.get("payload") or "")
        elif "id" in message and message["id"] in self.asked:
            why, session = self.asked.pop(message["id"])
            result = message.get("result") or {}
            if why == "frames":
                frames = [result.get("frameTree") or {}]
                while frames:
                    tree = frames.pop()
                    frames.extend(tree.get("childFrames") or [])
                    if tree.get("frame"):
                        self.listen_in(session, tree["frame"]["id"])
            elif why == "world" and result.get("executionContextId"):
                self.send("Runtime.evaluate", {"expression": LISTENER, "contextId": result["executionContextId"]}, session)
                # the browser gives a new script world the way to report only when asked again (it would do
                # it by itself with Runtime.enable, which a page can notice: not used here)
                self.send("Runtime.removeBinding", {"name": BINDING}, session)
                self.send("Runtime.addBinding", {"name": BINDING, "executionContextName": WORLD}, session)

    def listen_in(self, session, frame):
        """Put the listener in one frame's document, in a script world of its own."""
        self.send("Page.createIsolatedWorld", {"frameId": frame, "worldName": WORLD}, session, why="world")

    def saw(self, page_title, payload):
        try:
            seen = json.loads(payload)
            action, what = str(seen["action"]), str(seen.get("what") or "")[:12]
            x, y, scale = float(seen["x"]), float(seen["y"]), float(seen.get("scale") or 1)
        except (ValueError, KeyError, TypeError):
            return
        if action not in ("click", "type"):
            return
        area = pick_area(page_areas(self.pid), page_title, int(user32.GetForegroundWindow() or 0))
        point = screen_point(area, x, y, scale) if area else None
        if point is None or not on_top(area[0], point):
            # the spot is not on screen (another window covers it, minimised, scrolled away): the work is
            # still told, with the window instead of a point
            self.on_action(self.pid, action, what, None, None, window=area[1] if area else page_title)
        elif not is_yours(action, area[0], point):
            self.on_action(self.pid, action, what, point[0], point[1])


def watch(pid, command, on_action, alive, log):
    """Follow one browser (`command` is its command line) until it ends. `alive()` says whether it still runs."""
    try:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))      # real pixels on a scaled screen
    except (AttributeError, OSError):
        pass
    failures = 0
    while alive() and failures < 8:
        began = time.monotonic()
        try:
            port = port_of(command)
            if port is None:
                raise OSError("its debugging port is not known yet")
            Browser(pid, port, on_action).run(alive)
        except Exception as error:                   # the browser is starting, closing, or said something odd
            failures = 0 if time.monotonic() - began > 60 else failures + 1
            if failures == 8:
                log(f"clicks of browser pid {pid} are not followed: {type(error).__name__}: {error}")
            time.sleep(2)
