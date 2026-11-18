"""Buddy.exe --pointers on|off|toggle: the outside switch for the crew's pointers (buddy_host.py)."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST = (ROOT / "buddy_host.py").read_text(encoding="utf-8")


def _function(name):
    """One plain function of buddy_host.py, without importing the whole app (it needs a screen)."""
    tree = ast.parse(HOST)
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    scope = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "buddy_host.py", "exec"), scope)
    return scope[name]


def test_pointers_wanted():
    wanted = _function("pointers_wanted")
    assert wanted("on", False) is True and wanted("on", True) is True
    assert wanted("off", True) is False and wanted("off", False) is False
    assert wanted("toggle", True) is False and wanted("toggle", False) is True
    assert wanted("", True) is None and wanted("maybe", False) is None


def test_the_switch_is_wired():
    assert 'parser.add_argument("--pointers", choices=("on", "off", "toggle")' in HOST
    assert "return request_pointers(args.pointers)" in HOST
    assert "win32event.CreateEvent(None, False, False, POINTERS_EVENT_NAME)" in HOST
    assert "self._pointers_from_outside()" in HOST
    # the answer is saved and applied the same way as the tick box in Settings: one switch for all of them
    assert 'self.set_pointers(enabled, f"--pointers {wanted}")' in \
        HOST.split("def _pointers_from_outside(self):")[1].split("\n    def ")[0]
    body = HOST.split("def set_pointers(self, enabled, why):")[1].split("\n    def ")[0]
    assert 'self.settings["crew_enabled"] = enabled' in body
    assert "keyit_core.save_settings(self.settings)" in body and "self.set_crew(enabled)" in body
