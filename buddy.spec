# PyInstaller spec for Buddy (onedir). build.bat runs this, then copies the sources to dist\Buddy\app.
#
# Buddy.exe only freezes the launcher plus every library the sources need. Pips, KeyIt and the glue
# ship as plain .py files in dist\Buddy\app. That way Pips finds its assets and keeps its data next to its
# own code (app\pips\data), exactly like a normal Pips install.
import ast
import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

ROOT = Path(SPECPATH).resolve()
SOURCE_DIRS = [ROOT, ROOT / "pips", ROOT / "keyit"]
LOCAL_MODULES = {path.stem for folder in SOURCE_DIRS for path in folder.glob("*.py")}


def third_party_and_stdlib_imports():
    """Every absolute import in the shipped sources (incl. lazy ones inside functions)."""
    names = set()
    for folder in SOURCE_DIRS:
        for path in folder.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names.add(node.module)
    usable = []
    for name in sorted(names):
        if name.split(".")[0] in LOCAL_MODULES:
            continue
        try:
            if importlib.util.find_spec(name) is not None:
                usable.append(name)
        except (ImportError, ValueError):
            pass                                    # optional / dev-only dependency not installed
    return usable


whisper_datas, whisper_binaries, whisper_hidden = collect_all("faster_whisper")
# natural voices (keyit_voice): Kokoro + espeak-ng phonemizer; espeakng_loader ships the espeak DLL and its data
voice_datas, voice_binaries, voice_hidden = [], [], []
for package in ("kokoro_onnx", "phonemizer", "espeakng_loader", "joblib", "dlinfo", "attrs", "cloudpickle",
                "misaki", "jieba", "pypinyin", "cn2an", "proces", "regex", "ordered_set"):   # + Chinese
    datas_, binaries_, hidden_ = collect_all(package)
    voice_datas += datas_
    voice_binaries += binaries_
    voice_hidden += hidden_

analysis = Analysis(
    ["buddy.py"],
    pathex=[str(folder) for folder in SOURCE_DIRS],
    binaries=whisper_binaries + voice_binaries + collect_dynamic_libs("ctranslate2"),
    datas=whisper_datas + voice_datas,
    hiddenimports=third_party_and_stdlib_imports() + whisper_hidden + voice_hidden + [
        "pystray._win32",
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "comtypes.client",
        "comtypes.stream",
        "win32com.client",
    ],
    excludes=["pytest"],
)

# Keep our own modules OUT of the frozen archive: they are imported from dist\Buddy\app at run time
analysis.pure = [entry for entry in analysis.pure if not Path(entry[1]).resolve().is_relative_to(ROOT)]

pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Buddy",
    icon=str(ROOT / "assets" / "buddy.ico"),
    console=False,
)
coll = COLLECT(exe, analysis.binaries, analysis.datas, name="Buddy")
