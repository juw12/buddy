"""Objective-C class names are global to the process: two PyObjC subclasses with the same
name in different modules crash the app at import ("X is overriding existing Objective-C class").
This test runs anywhere (no AppKit needed) by reading the source."""
import ast
from collections import Counter
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "buddy_mac"
COCOA_BASES = {"NSObject", "NSView", "NSPanel", "NSWindow", "NSWindowController", "NSResponder"}


def _objc_classes():
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = {getattr(base, "id", getattr(base, "attr", "")) for base in node.bases}
                if bases & COCOA_BASES:
                    yield node.name, path.name


def test_objc_class_names_are_unique():
    found = list(_objc_classes())
    assert found, "expected some PyObjC classes in buddy_mac"
    counts = Counter(name for name, _ in found)
    duplicates = {name: [f for n, f in found if n == name] for name, c in counts.items() if c > 1}
    assert not duplicates, f"duplicate Objective-C class names: {duplicates}"
