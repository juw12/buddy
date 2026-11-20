"""Which certificate authorities Buddy Lite for Mac trusts for https (no PyObjC here, so it is unit-tested anywhere).

The built app carries its own OpenSSL, and that OpenSSL looks for the certificate authorities in the folder
of the Python the app was built with (/Library/Frameworks/Python.framework/Versions/3.12/etc/openssl). A Mac
that never had that Python installed has nothing there, so every https call failed with
CERTIFICATE_VERIFY_FAILED: the update check, the update download and the brain. Found on the MacBook Pro 2019
on 2026-11-20; the tests never saw it because they talk to a brain on this computer and read the update
manifest from a file.

So: when Python's own places hold no certificate authority, use the ones macOS ships.
"""
from __future__ import annotations

import logging
import ssl
import subprocess

# macOS keeps its root certificates as one file here (it is what the system's own curl reads).
MAC_CA_FILE = "/etc/ssl/cert.pem"
# The same roots, straight from the keychain Apple ships. Asked only when the file above is missing or empty.
MAC_ROOT_KEYCHAIN = "/System/Library/Keychains/SystemRootCertificates.keychain"
SECURITY_TIMEOUT_SECONDS = 15

_context: ssl.SSLContext | None = None


def _authorities(context: ssl.SSLContext) -> int:
    return int(context.cert_store_stats().get("x509_ca", 0))


def _keychain_roots() -> str:
    """Apple's root certificates as PEM text, or "" when they cannot be read."""
    try:
        done = subprocess.run(
            ["/usr/bin/security", "find-certificate", "-a", "-p", MAC_ROOT_KEYCHAIN],
            capture_output=True, text=True, timeout=SECURITY_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def build_context(ca_file: str = MAC_CA_FILE, keychain_roots=_keychain_roots) -> tuple[ssl.SSLContext, str]:
    """An https context that checks certificates, and where its authorities came from:
    "python" (the places Python and $SSL_CERT_FILE name), "macos file", "macos keychain" or "none"."""
    context = ssl.create_default_context()
    if _authorities(context):
        return context, "python"
    try:
        context.load_verify_locations(cafile=ca_file)
    except (OSError, ssl.SSLError):
        pass
    if _authorities(context):
        return context, "macos file"
    roots = keychain_roots()
    if roots:
        try:
            context.load_verify_locations(cadata=roots)
        except (ssl.SSLError, ValueError):
            pass
    if _authorities(context):
        return context, "macos keychain"
    return context, "none"


def https_context() -> ssl.SSLContext:
    """The context every https call of Buddy uses. Made once; the log says where the authorities came from."""
    global _context
    if _context is None:
        _context, source = build_context()
        if source == "none":
            logging.warning("https: no certificate authorities found on this Mac, so https calls will fail")
        else:
            logging.info("https certificates: %s (%d authorities)", source, _authorities(_context))
    return _context
