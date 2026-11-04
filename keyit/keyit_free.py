"""Buddy's free brain: works out of the box, no account, no key, nothing to install.

A small Cloudflare Worker (cloud/brain in the repo) answers with a vision model on Cloudflare Workers AI.
It is shared by everyone, so it has a daily cap that keeps it inside Cloudflare's free allowance. The
screenshot and question go to Cloudflare to be answered and are not stored. For unlimited answers that
never leave this PC, use a local brain (Settings -> Brain: Local GPU / Local CPU with Ollama).

Same methods Buddy uses on its other brains: warm_up, prefill, chat_stream.
"""
import base64
import io
import json
import logging
import os
import time
import urllib.error
import urllib.request

CHAT_URL = os.environ.get("BUDDY_FREE_URL", "https://buddy-brain.juwenmondial.workers.dev/v1/chat/completions")
HEALTH_URL = CHAT_URL.replace("/v1/chat/completions", "/health")
MODEL = "buddy-free"
MAX_TOKENS = 400
# Cloudflare blocks the default "Python-urllib" user agent (error 1010)
USER_AGENT = "Buddy/1.1 (Windows)"


class FreeBrainError(RuntimeError):
    """code: 'daily_cap', 'rate_limited', 'unreachable' or the server's error code."""

    def __init__(self, message, code=""):
        super().__init__(message)
        self.code = code


def _error_from_http(error):
    detail = error.read(600).decode("utf-8", "replace")
    code, message = "", detail[:200]
    try:
        info = json.loads(detail).get("error") or {}
        code, message = info.get("code") or "", info.get("message") or message
    except (ValueError, AttributeError):
        pass
    return FreeBrainError(f"Free brain error {error.code}: {message}", code)


def _as_jpeg(messages):
    """Buddy's screenshots are PNG; the free brain's model points much more accurately at a JPEG
    (tested: PNG put "Save As" 25% of the screen off, JPEG right on it). Also ~4x smaller to upload."""
    try:
        from PIL import Image
    except ImportError:
        return messages
    converted = []
    for message in messages:
        images = []
        for encoded in message.get("images") or []:
            try:
                picture = Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGB")
                buffer = io.BytesIO()
                picture.save(buffer, "JPEG", quality=75)
                images.append(("jpeg", base64.b64encode(buffer.getvalue()).decode("ascii")))
            except Exception:                       # keep the original if it isn't a readable image
                images.append(("png", encoded))
        converted.append((message, images))
    result = []
    for message, images in converted:
        if not images:
            result.append({"role": message["role"], "content": message.get("content", "")})
            continue
        parts = [{"type": "image_url", "image_url": {"url": f"data:image/{kind};base64,{data}"}}
                 for kind, data in images]
        if message.get("content"):
            parts.append({"type": "text", "text": message["content"]})
        result.append({"role": message["role"], "content": parts})
    return result


class FreeClient:
    gpu = False
    smart = True                       # an off-PC brain: Buddy falls back to this PC if it fails
    label = "Free cloud brain"

    def _open(self, request, timeout):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            raise _error_from_http(error) from error
        except (urllib.error.URLError, OSError) as error:
            raise FreeBrainError(f"Can't reach the free brain ({getattr(error, 'reason', error)})",
                                 "unreachable") from error

    def warm_up(self, model=None):
        """Check the free brain is up and has questions left today."""
        request = urllib.request.Request(HEALTH_URL, headers={"User-Agent": USER_AGENT})
        with self._open(request, timeout=10) as response:
            health = json.load(response)
        if not (health.get("today") or {}).get("left", 1):
            raise FreeBrainError("The free brain is out of questions for today.", "daily_cap")

    def prefill(self, model, messages, stop):
        return False                   # nothing to read ahead: the answer starts in a few seconds

    def chat_stream(self, model, messages, timeout=60):
        body = {"model": MODEL, "messages": _as_jpeg(messages), "temperature": 0.2,
                "max_tokens": MAX_TOKENS, "stream": True}
        request = urllib.request.Request(
            CHAT_URL,
            json.dumps(body).encode(),
            {"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        started = time.perf_counter()
        with self._open(request, timeout=timeout) as response:
            first = True
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                content = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content")
                if content is not None and not isinstance(content, str):
                    content = str(content)
                if content:
                    if first:
                        logging.info("Free brain first words after %.1fs", time.perf_counter() - started)
                        first = False
                    yield content
