"""Buddy's file ring (like Filee's drag menu): hold Ctrl while you drag files from Explorer and a ring
pops up at the mouse. Drop the files on a slice:

  PDF      images -> one PDF, Word/Excel/PowerPoint -> PDF (through Office, invisibly)
  Image    images -> PNG (PNG -> JPG), PowerPoint -> one PNG per slide
  Text     Word/PDF -> .txt, Excel -> .csv (UTF-8), PowerPoint -> .txt; images: Buddy reads the text out
  Smaller  images -> a smaller JPG (longest side 1600 px)
  Zip      files/folders -> one .zip; a single .zip -> unzipped into a folder
  Ask      ask Buddy about the file(s): it reads them (text, Office, PDF, images) and answers

Results always go next to the first file and never overwrite anything ("name (2).pdf").
The originals are never changed, moved or deleted.
"""

import ctypes
import logging
import re
import shutil
import tempfile
import threading
import time
import zipfile
from ctypes import wintypes
from pathlib import Path
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".ico"}
WORD_EXT = {".doc", ".docx", ".docm", ".rtf", ".odt"}
EXCEL_EXT = {".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".csv"}
POWERPOINT_EXT = {".ppt", ".pptx", ".pptm", ".odp"}
PLAIN_TEXT_EXT = {".txt", ".log", ".md", ".json", ".xml", ".ini", ".cfg", ".conf", ".yaml", ".yml", ".py", ".js",
                  ".ts", ".html", ".htm", ".css", ".sql", ".bat", ".cmd", ".ps1", ".sh", ".c", ".h", ".cpp", ".cs",
                  ".java", ".csv", ".tsv"}

# (id, label, little picture) in ring order, clockwise from the top
ACTIONS = [
    ("ask", "Ask Buddy", "\U0001F4AC"),
    ("pdf", "PDF", "\U0001F4C4"),
    ("image", "Image", "\U0001F5BC"),
    ("text", "Text", "\U0001F4DD"),
    ("smaller", "Smaller", "\U0001F90F"),
    ("zip", "Zip", "\U0001F5DC"),
]
SMALL_SIDE = 1600
ASK_MAX_CHARS = 12000


class Skip(Exception):
    """This file can't get this treatment (the message is shown to the user)."""


# --------------------------------------------------------------------------- helpers
def unique_path(path):
    """path, or 'name (2).ext', 'name (3).ext', ... so nothing is ever overwritten."""
    path = Path(path)
    if not path.exists():
        return path
    for number in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise Skip("too many files with that name already")


def kind(path):
    suffix = Path(path).suffix.lower()
    if Path(path).is_dir():
        return "folder"
    if suffix in IMAGE_EXT:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix in WORD_EXT:
        return "word"
    if suffix in EXCEL_EXT:
        return "excel"
    if suffix in POWERPOINT_EXT:
        return "powerpoint"
    if suffix == ".zip":
        return "zip"
    if suffix in PLAIN_TEXT_EXT:
        return "text"
    return "other"


def _running(exe_name):
    """Is a program with this exe name running for this user? (tasklist, no extra packages)"""
    import subprocess

    try:
        listing = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"], capture_output=True,
                                 text=True, timeout=5, creationflags=0x08000000).stdout
    except Exception:
        return True                           # unsure: behave as if it is running (the safe answer)
    return exe_name.lower() in listing.lower()


class Office:
    """Word / Excel / PowerPoint driven invisibly through COM, started on demand, closed afterwards."""

    def __init__(self):
        self.apps = {}
        self.started_powerpoint = False

    def __enter__(self):
        import pythoncom

        pythoncom.CoInitialize()
        return self

    def __exit__(self, *exc):
        import pythoncom

        for name, app in self.apps.items():
            try:
                if name == "PowerPoint" and app.Presentations.Count:
                    continue                      # you opened PowerPoint meanwhile: leave it alone
                if name != "PowerPoint" or self.started_powerpoint:
                    app.Quit()
            except Exception:
                logger.debug("Office quit failed", exc_info=True)
        self.apps.clear()
        pythoncom.CoUninitialize()
        return False

    def app(self, name):
        if name in self.apps:
            return self.apps[name]
        import win32com.client

        try:
            if name == "PowerPoint":
                # PowerPoint has only one copy per user: never borrow the one you are working in (a crash
                # there, e.g. in a company file-protection add-in, would take your open decks with it)
                if _running("powerpnt.exe"):
                    raise Skip("needs PowerPoint, but you have PowerPoint open \u2013 close it and drop again")
                app = win32com.client.DispatchEx("PowerPoint.Application")
                self.started_powerpoint = True
            else:
                app = win32com.client.DispatchEx(f"{name}.Application")   # private instance, never yours
                app.Visible = False
                app.DisplayAlerts = False if name == "Excel" else 0
        except Exception as error:
            raise Skip(f"needs Microsoft {name}, which I couldn't start ({error})") from error
        self.apps[name] = app
        return app

    # Word
    def word_open(self, path):
        word = self.app("Word")
        return word.Documents.Open(str(path), ConfirmConversions=False, ReadOnly=True, AddToRecentFiles=False,
                                   Visible=False)

    def word_to_pdf(self, path, out):
        document = self.word_open(path)
        try:
            document.ExportAsFixedFormat(str(out), 17)          # wdExportFormatPDF
        finally:
            document.Close(False)

    def word_to_text(self, path, out):
        document = self.word_open(path)
        try:
            document.SaveAs2(str(out), FileFormat=2, Encoding=65001)   # wdFormatText, UTF-8
        finally:
            document.Close(False)

    # Excel
    def excel_open(self, path):
        excel = self.app("Excel")
        return excel.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=True, AddToMru=False)

    def excel_to_pdf(self, path, out):
        workbook = self.excel_open(path)
        try:
            workbook.ExportAsFixedFormat(0, str(out))           # xlTypePDF
        finally:
            workbook.Close(False)

    def excel_to_csv(self, path, out):
        workbook = self.excel_open(path)
        try:
            workbook.SaveAs(str(out), 62)                       # xlCSVUTF8 (the sheet that is open)
        finally:
            workbook.Close(False)

    # PowerPoint
    def powerpoint_open(self, path):
        powerpoint = self.app("PowerPoint")
        return powerpoint.Presentations.Open(str(path), ReadOnly=True, Untitled=False, WithWindow=False)

    def powerpoint_to_pdf(self, path, out):
        presentation = self.powerpoint_open(path)
        try:
            presentation.SaveAs(str(out), 32)                   # ppSaveAsPDF
        finally:
            presentation.Close()

    def powerpoint_to_images(self, path, folder):
        presentation = self.powerpoint_open(path)
        try:
            folder.mkdir(parents=True, exist_ok=False)
            for index in range(1, presentation.Slides.Count + 1):
                presentation.Slides(index).Export(str(folder / f"Slide {index}.png"), "PNG")
        finally:
            presentation.Close()


# --------------------------------------------------------------------------- reading files (no Office needed)
def _xml_text(data, tag_suffix, paragraph_suffix=None):
    """All text in an Office XML part, one line per paragraph."""
    root = ElementTree.fromstring(data)
    lines, current = [], []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == tag_suffix and element.text:
            current.append(element.text)
        elif paragraph_suffix and tag == paragraph_suffix:
            if current:
                lines.append("".join(current))
            current = []
    if current:
        lines.append("".join(current))
    return "\n".join(lines)


def _numbered(names, pattern):
    found = [(int(match.group(1)), name) for name in names for match in [re.match(pattern, name)] if match]
    return [name for _, name in sorted(found)]


def read_docx(path):
    with zipfile.ZipFile(path) as archive:
        return _xml_text(archive.read("word/document.xml"), "t", "p")


def read_pptx(path):
    with zipfile.ZipFile(path) as archive:
        slides = _numbered(archive.namelist(), r"ppt/slides/slide(\d+)\.xml$")
        parts = []
        for number, name in enumerate(slides, start=1):
            parts.append(f"--- Slide {number} ---\n" + _xml_text(archive.read(name), "t", "p"))
        return "\n".join(parts)


def read_xlsx(path, max_rows=400):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root:
                shared.append("".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")))
        sheet_names = []
        if "xl/workbook.xml" in names:
            root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            sheet_names = [node.get("name") for node in root.iter() if node.tag.endswith("}sheet")]
        parts = []
        for index, name in enumerate(_numbered(names, r"xl/worksheets/sheet(\d+)\.xml$")):
            title = sheet_names[index] if index < len(sheet_names) else f"Sheet{index + 1}"
            root = ElementTree.fromstring(archive.read(name))
            rows = []
            for row in root.iter():
                if not row.tag.endswith("}row"):
                    continue
                cells = []
                for cell in row:
                    if not cell.tag.endswith("}c"):
                        continue
                    value = next((node.text for node in cell.iter() if node.tag.endswith("}v")), None)
                    inline = "".join(node.text or "" for node in cell.iter() if node.tag.endswith("}t"))
                    if cell.get("t") == "s" and value is not None and value.isdigit() and int(value) < len(shared):
                        cells.append(shared[int(value)])
                    else:
                        cells.append(inline or value or "")
                if any(cells):
                    rows.append("\t".join(cells))
                if len(rows) >= max_rows:
                    rows.append("\u2026 (more rows)")
                    break
            parts.append(f"--- Sheet: {title} ---\n" + "\n".join(rows))
        return "\n".join(parts)


def read_plain(path, limit=ASK_MAX_CHARS * 4):
    data = Path(path).read_bytes()[:limit]
    for encoding in ("utf-8-sig", "utf-16") if data[:2] not in (b"\xff\xfe", b"\xfe\xff") else ("utf-16",):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("cp1252", errors="replace")


def image_png(path, max_side=1600):
    """Image file -> PNG bytes the brain can look at."""
    import io

    from PIL import Image

    with Image.open(path) as image:
        image = image.convert("RGB")
        scale = min(1.0, max_side / max(image.size))
        if scale < 1.0:
            image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


def read_for_ask(paths, office=None):
    """What Buddy gets to read: {'label', 'text', 'png'} for the dropped files."""
    texts, png, notes = [], None, []
    for path in paths:
        path = Path(path)
        what = kind(path)
        try:
            if what == "image":
                if png is None:
                    png = image_png(path)
                    texts.append(f"=== {path.name} (the image is attached) ===")
                else:
                    notes.append(f"{path.name} (only the first image is attached)")
                continue
            if what == "folder":
                listing = sorted(item.name + ("/" if item.is_dir() else "") for item in path.iterdir())[:200]
                body = "Folder contents:\n" + "\n".join(listing)
            elif path.suffix.lower() in (".docx", ".docm"):
                body = read_docx(path)
            elif path.suffix.lower() in (".pptx", ".pptm"):
                body = read_pptx(path)
            elif path.suffix.lower() in (".xlsx", ".xlsm"):
                body = read_xlsx(path)
            elif what == "text":
                body = read_plain(path)
            elif what in ("pdf", "word") and office is not None:
                with tempfile.TemporaryDirectory() as folder:
                    out = Path(folder) / "text.txt"
                    office.word_to_text(path, out)
                    body = read_plain(out)
            elif what == "excel" and office is not None:
                with tempfile.TemporaryDirectory() as folder:
                    out = Path(folder) / "sheet.csv"
                    office.excel_to_csv(path, out)
                    body = read_plain(out)
            elif what == "zip":
                with zipfile.ZipFile(path) as archive:
                    body = "Zip contents:\n" + "\n".join(archive.namelist()[:300])
            else:
                notes.append(f"{path.name} (I can't read this kind of file)")
                continue
        except Skip as reason:
            notes.append(f"{path.name} ({reason})")
            continue
        except Exception as error:
            logger.exception("Could not read %s", path)
            notes.append(f"{path.name} (could not read: {error})")
            continue
        texts.append(f"=== {path.name} ===\n{body.strip()}")
    text = "\n\n".join(texts)
    if len(text) > ASK_MAX_CHARS:
        text = text[:ASK_MAX_CHARS] + "\n\u2026 (the rest is cut off)"
    if notes:
        text += "\n\nNot included: " + "; ".join(notes)
    names = [Path(path).name for path in paths]
    label = names[0] if len(names) == 1 else f"{names[0]} + {len(names) - 1} more"
    return {"label": label, "text": text, "png": png}


# --------------------------------------------------------------------------- the conversions
def _next_to(first, name):
    return unique_path(Path(first).parent / name)


def make_pdf(paths, office):
    outputs, skipped = [], []
    images = [path for path in paths if kind(path) == "image"]
    if images:
        from PIL import Image

        pages = []
        for path in images:
            with Image.open(path) as image:
                pages.append(image.convert("RGB"))
        out = _next_to(images[0], Path(images[0]).stem + ".pdf")
        pages[0].save(out, "PDF", resolution=150, save_all=True, append_images=pages[1:])
        outputs.append(out)
    for path in paths:
        what = kind(path)
        if what == "image":
            continue
        out = _next_to(path, Path(path).stem + ".pdf")
        try:
            if what == "word" or what == "text":
                office.word_to_pdf(path, out)
            elif what == "excel":
                office.excel_to_pdf(path, out)
            elif what == "powerpoint":
                office.powerpoint_to_pdf(path, out)
            elif what == "pdf":
                raise Skip("is already a PDF")
            else:
                raise Skip("can't be turned into a PDF")
            outputs.append(out)
        except Skip as reason:
            skipped.append((path, str(reason)))
    return outputs, skipped


def make_image(paths, office):
    from PIL import Image

    outputs, skipped = [], []
    for path in paths:
        what = kind(path)
        try:
            if what == "image":
                target = ".jpg" if Path(path).suffix.lower() == ".png" else ".png"
                out = _next_to(path, Path(path).stem + target)
                with Image.open(path) as image:
                    if target == ".jpg":
                        image.convert("RGB").save(out, "JPEG", quality=92)
                    else:
                        image.save(out, "PNG")
                outputs.append(out)
            elif what == "powerpoint":
                folder = _next_to(path, Path(path).stem + " slides")
                office.powerpoint_to_images(path, folder)
                outputs.append(folder)
            else:
                raise Skip("can't be turned into a picture (yet)")
        except Skip as reason:
            skipped.append((path, str(reason)))
    return outputs, skipped


def make_text(paths, office):
    outputs, skipped = [], []
    for path in paths:
        what = kind(path)
        try:
            if what in ("word", "pdf"):
                out = _next_to(path, Path(path).stem + ".txt")
                office.word_to_text(path, out)
            elif what == "excel":
                if Path(path).suffix.lower() == ".csv":
                    raise Skip("is already text")
                out = _next_to(path, Path(path).stem + ".csv")
                office.excel_to_csv(path, out)
            elif what == "powerpoint":
                out = _next_to(path, Path(path).stem + ".txt")
                if Path(path).suffix.lower() in (".pptx", ".pptm"):
                    body = read_pptx(path)
                else:
                    raise Skip("old .ppt: save it as .pptx first")
                out.write_text(body, encoding="utf-8")
            elif what == "text":
                raise Skip("is already text")
            else:
                raise Skip("has no text I can pull out")
            outputs.append(out)
        except Skip as reason:
            skipped.append((path, str(reason)))
    return outputs, skipped


def make_smaller(paths, office=None):
    from PIL import Image

    outputs, skipped = [], []
    for path in paths:
        if kind(path) != "image":
            skipped.append((path, "only pictures can be made smaller"))
            continue
        out = _next_to(path, Path(path).stem + " (small).jpg")
        with Image.open(path) as image:
            image = image.convert("RGB")
            scale = min(1.0, SMALL_SIDE / max(image.size))
            if scale < 1.0:
                image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
            image.save(out, "JPEG", quality=82, optimize=True)
        outputs.append(out)
    return outputs, skipped


def make_zip(paths, office=None):
    paths = [Path(path) for path in paths]
    if len(paths) == 1 and kind(paths[0]) == "zip":
        folder = _next_to(paths[0], paths[0].stem)
        with zipfile.ZipFile(paths[0]) as archive:
            root = folder.resolve()
            for member in archive.infolist():
                target = (folder / member.filename).resolve()
                if not str(target).startswith(str(root)):
                    raise Skip(f"unsafe path inside the zip: {member.filename}")
            archive.extractall(folder)
        return [folder], []
    name = (paths[0].stem if len(paths) == 1 else paths[0].parent.name or "files") + ".zip"
    out = _next_to(paths[0], name)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            if path.is_dir():
                for item in path.rglob("*"):
                    if item.is_file() and item != out:
                        archive.write(item, Path(path.name) / item.relative_to(path))
            elif path != out:
                archive.write(path, path.name)
    return [out], []


CONVERTERS = {"pdf": make_pdf, "image": make_image, "text": make_text, "smaller": make_smaller, "zip": make_zip}
NEEDS_OFFICE = {"word", "excel", "powerpoint", "pdf", "text"}


def convert(action, paths):
    """Run one ring action on the files. Returns (outputs, skipped [(path, why)])."""
    paths = [Path(path) for path in paths]
    converter = CONVERTERS[action]
    if any(kind(path) in NEEDS_OFFICE for path in paths) and action in ("pdf", "image", "text"):
        with Office() as office:
            return converter(paths, office)
    return converter(paths, None)


def summary(action, outputs, skipped):
    """One short line for the bubble."""
    if outputs:
        names = ", ".join(path.name for path in outputs[:3]) + ("\u2026" if len(outputs) > 3 else "")
        line = f"Done \u2713 {names}"
    else:
        line = "Nothing to do."
    if skipped:
        line += "\n" + "; ".join(f"{Path(path).name} {why}" for path, why in skipped[:3])
    return line


# --------------------------------------------------------------------------- watching for Ctrl + drag
user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
LONG_PTR = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LONG_PTR, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
user32.SetWindowLongPtrW.restype = LONG_PTR
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = LONG_PTR
user32.CallWindowProcW.argtypes = [LONG_PTR, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.CallWindowProcW.restype = LONG_PTR
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT]
shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
shell32.DragQueryFileW.restype = wintypes.UINT
shell32.DragQueryPoint.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.POINT)]
shell32.DragFinish.argtypes = [wintypes.HANDLE]

VK_LBUTTON = 0x01
VK_CONTROL = 0x11
GA_ROOT = 2
GWLP_WNDPROC = -4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
WM_DROPFILES = 0x0233
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DRAG_START_PX = 12


def _down(key):
    return bool(user32.GetAsyncKeyState(key) & 0x8000)


def _cursor():
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def _process_name(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(520)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).name.lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _under_explorer(x, y):
    """Did the drag start on Explorer or the desktop (where files are)?"""
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    root = user32.GetAncestor(hwnd, GA_ROOT) if hwnd else None
    return bool(root) and _process_name(root) == "explorer.exe"


class FileRing:
    """The ring window (Tk, on the UI thread) and the little thread that watches for Ctrl + file drags."""

    SIZE = 248
    OUTER = 118
    INNER = 42
    KEY = "#010203"
    INK = "#1f2130"
    POLL_S = 0.025

    def __init__(self, root, post, on_drop, accent=lambda: "#ff8424", enabled=True):
        import tkinter as tk

        self.root = root
        self.post = post                      # thread-safe "run on the UI thread"
        self.on_drop = on_drop                # on_drop(action_id, [paths]) on the UI thread
        self.accent = accent
        self.enabled = bool(enabled)
        self.shown = False
        self.hover = None
        self._hide_job = None
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.config(bg=self.KEY)
        self.top.attributes("-transparentcolor", self.KEY)
        self.top.geometry(f"{self.SIZE}x{self.SIZE}+-4000+-4000")
        self.canvas = tk.Canvas(self.top, width=self.SIZE, height=self.SIZE, bg=self.KEY, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.top.update_idletasks()
        self.frame_hwnd = int(self.top.wm_frame(), 16)
        self.canvas_hwnd = self.canvas.winfo_id()
        style = user32.GetWindowLongPtrW(self.frame_hwnd, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(self.frame_hwnd, GWL_EXSTYLE,
                                 style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST)
        user32.ShowWindow(self.frame_hwnd, SW_HIDE)
        self._procs = {}
        for hwnd in (self.frame_hwnd, self.canvas_hwnd):
            shell32.DragAcceptFiles(hwnd, True)
            self._subclass(hwnd)
        self._draw()
        threading.Thread(target=self._watch, name="BuddyFileRing", daemon=True).start()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if not self.enabled:
            self.post(self.hide)

    # -- drop handling (WM_DROPFILES on the ring)
    def _subclass(self, hwnd):
        old = user32.GetWindowLongPtrW(hwnd, GWLP_WNDPROC)

        def procedure(window, message, wparam, lparam):
            if message == WM_DROPFILES:
                try:
                    self._dropped(window, wparam)
                except Exception:
                    logger.exception("File ring drop failed")
                return 0
            return user32.CallWindowProcW(old, window, message, wparam, lparam)

        callback = WNDPROC(procedure)
        self._procs[hwnd] = (callback, old)                # keep the callback alive
        user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, ctypes.cast(callback, ctypes.c_void_p).value)

    def _dropped(self, window, handle):
        handle = wintypes.HANDLE(handle)
        count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
        paths = []
        for index in range(count):
            length = shell32.DragQueryFileW(handle, index, None, 0) + 1
            buffer = ctypes.create_unicode_buffer(length)
            shell32.DragQueryFileW(handle, index, buffer, length)
            paths.append(buffer.value)
        point = wintypes.POINT()
        shell32.DragQueryPoint(handle, ctypes.byref(point))
        shell32.DragFinish(handle)
        action = self._slice_at(point.x, point.y)
        logger.info("File ring: %d file(s) dropped on %s", len(paths), action or "the middle (cancel)")
        self.post(self.hide)
        if action and paths:
            self.post(lambda: self.on_drop(action, paths))

    # -- geometry
    def _slice_at(self, x, y):
        import math

        center = self.SIZE / 2
        dx, dy = x - center, y - center
        distance = math.hypot(dx, dy)
        if distance < self.INNER or distance > self.OUTER + 6:
            return None
        angle = (math.degrees(math.atan2(dx, -dy)) + 360) % 360       # 0 = up, clockwise
        step = 360 / len(ACTIONS)
        return ACTIONS[int(((angle + step / 2) % 360) // step)][0]

    def _draw(self):
        import math

        canvas = self.canvas
        canvas.delete("all")
        accent = self.accent()
        center = self.SIZE / 2
        step = 360 / len(ACTIONS)
        box = (center - self.OUTER, center - self.OUTER, center + self.OUTER, center + self.OUTER)
        for index, (action, label, icon) in enumerate(ACTIONS):
            middle = index * step                                     # clockwise from the top
            start = 90 - middle - step / 2                            # Tk: counter-clockwise from 3 o'clock
            hot = action == self.hover
            canvas.create_arc(*box, start=start, extent=step, style="pieslice",
                              fill=accent if hot else self.INK, outline=accent, width=2)
            radius = (self.OUTER + self.INNER) / 2 + 4
            angle = math.radians(middle)
            x, y = center + radius * math.sin(angle), center - radius * math.cos(angle)
            color = self.INK if hot else "#ffffff"
            canvas.create_text(x, y - 9, text=icon, font=("Segoe UI Emoji", 15), fill=color)
            canvas.create_text(x, y + 13, text=label, font=("Segoe UI Semibold", 9), fill=color)
        canvas.create_oval(center - self.INNER, center - self.INNER, center + self.INNER, center + self.INNER,
                           fill="#fff7ef", outline=accent, width=2)
        canvas.create_text(center, center - 7, text="Buddy", font=("Segoe UI Semibold", 10), fill=self.INK)
        canvas.create_text(center, center + 10, text="drop on a slice", font=("Segoe UI", 7), fill="#6b6f80")

    # -- show / hide (UI thread)
    def show(self, x, y):
        if self._hide_job:
            self.root.after_cancel(self._hide_job)
            self._hide_job = None
        self.hover = None
        self._draw()
        left, top = int(x - self.SIZE / 2), int(y - self.SIZE / 2)
        user32.SetWindowPos(self.frame_hwnd, HWND_TOPMOST, left, top, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)
        user32.ShowWindow(self.frame_hwnd, SW_SHOWNOACTIVATE)
        self.shown = True
        self.origin = (left, top)
        self._track()

    def _track(self):
        """Light up the slice under the mouse while the ring is open."""
        if not self.shown:
            return
        x, y = _cursor()
        hover = self._slice_at(x - self.origin[0], y - self.origin[1])
        if hover != self.hover:
            self.hover = hover
            self._draw()
        self.root.after(40, self._track)

    def hide(self):
        if self.shown:
            user32.ShowWindow(self.frame_hwnd, SW_HIDE)
        self.shown = False
        self.hover = None

    def hide_soon(self, delay_ms=400):
        """After the mouse button goes up: give the drop message time to arrive first."""
        if self._hide_job:
            self.root.after_cancel(self._hide_job)
        self._hide_job = self.root.after(delay_ms, self.hide)

    # -- the watcher thread
    def _watch(self):
        pressed_at, from_explorer, opened = None, False, False
        while True:
            time.sleep(self.POLL_S)
            if not self.enabled:
                pressed_at, opened = None, False
                continue
            try:
                button = _down(VK_LBUTTON)
                if button and pressed_at is None:
                    pressed_at = _cursor()
                    from_explorer = _under_explorer(*pressed_at)
                    opened = False
                elif button and not opened and from_explorer and _down(VK_CONTROL):
                    x, y = _cursor()
                    if abs(x - pressed_at[0]) + abs(y - pressed_at[1]) >= DRAG_START_PX:
                        opened = True
                        self.post(lambda x=x, y=y: self.show(x, y))
                elif not button and pressed_at is not None:
                    if opened:
                        self.post(self.hide_soon)
                    pressed_at, opened = None, False
            except Exception:
                logger.exception("File ring watcher")
                time.sleep(1.0)
