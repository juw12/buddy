"""Buddy Lite for Mac: which certificate authorities https calls trust (runs anywhere, no network)."""
import ssl
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buddy_mac import certs, core, updater  # noqa: E402

ONE_ROOT = Path(__file__).parent / "fixtures" / "one-root.pem"      # ISRG Root X1, a public certificate


def _bare_context():
    """What the built app gets on a Mac without the Python it was built with: a context with no authorities."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    assert context.cert_store_stats()["x509_ca"] == 0
    return context


def _never():
    raise AssertionError("the keychain must not be asked")


def test_pythons_own_authorities_are_used_when_there_are_some(monkeypatch):
    def with_one_root():
        context = _bare_context()
        context.load_verify_locations(cafile=str(ONE_ROOT))
        return context

    monkeypatch.setattr(ssl, "create_default_context", with_one_root)
    context, source = certs.build_context(ca_file="/nowhere/cert.pem", keychain_roots=_never)
    assert source == "python"
    assert context.cert_store_stats()["x509_ca"] == 1


def test_the_macos_file_is_used_when_python_has_none(monkeypatch):
    monkeypatch.setattr(ssl, "create_default_context", _bare_context)
    context, source = certs.build_context(ca_file=str(ONE_ROOT), keychain_roots=_never)
    assert source == "macos file"
    assert context.cert_store_stats()["x509_ca"] == 1
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname       # still checking


def test_the_keychain_is_asked_when_the_file_is_missing(monkeypatch):
    monkeypatch.setattr(ssl, "create_default_context", _bare_context)
    context, source = certs.build_context(ca_file="/nowhere/cert.pem",
                                          keychain_roots=lambda: ONE_ROOT.read_text(encoding="utf-8"))
    assert source == "macos keychain"
    assert context.cert_store_stats()["x509_ca"] == 1


def test_nothing_found_is_said_and_never_switches_checking_off(monkeypatch):
    monkeypatch.setattr(ssl, "create_default_context", _bare_context)
    for roots in ("", "not a certificate"):
        context, source = certs.build_context(ca_file="/nowhere/cert.pem", keychain_roots=lambda roots=roots: roots)
        assert source == "none"
        assert context.cert_store_stats()["x509_ca"] == 0
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_the_context_is_made_once(monkeypatch):
    made = []

    def build():
        made.append(1)
        return _bare_context(), "macos file"

    monkeypatch.setattr(certs, "_context", None)
    monkeypatch.setattr(certs, "build_context", build)
    assert certs.https_context() is certs.https_context()
    assert made == [1]


def test_the_brain_call_brings_the_context_and_a_local_brain_does_not(monkeypatch):
    marker = _bare_context()
    seen = {}

    def fake_urlopen(request, timeout=None, context=None):
        seen["context"] = context
        return "internet"

    monkeypatch.setattr(certs, "_context", marker)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    request = urllib.request.Request("https://example.com/v1/chat/completions", b"{}")
    assert core._open(request, 5) == "internet"
    assert seen["context"] is marker

    class Direct:
        def open(self, request, timeout=None):
            return "this computer"

    monkeypatch.setattr(core, "_DIRECT_OPENER", Direct())
    seen.clear()
    assert core._open(urllib.request.Request("http://127.0.0.1:11434/v1/chat/completions", b"{}"), 5) == "this computer"
    assert seen == {}


def test_both_update_routes_check_certificates_with_the_context(monkeypatch):
    marker = _bare_context()
    monkeypatch.setattr(certs, "_context", marker)
    openers = list(updater._openers())
    assert len(openers) == 2
    for opener in openers:
        https = [handler for handler in opener.handlers if isinstance(handler, urllib.request.HTTPSHandler)]
        assert len(https) == 1
        assert https[0]._context is marker
    # the second route still goes around a proxy
    assert not any(isinstance(handler, urllib.request.ProxyHandler) and handler.proxies
                   for handler in openers[1].handlers)
