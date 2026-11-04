"""Buddy's "Smart" brain: a big vision model on your company's AI gateway instead of the small model
on this laptop. A large hosted vision model answers in ~1-2 s instead of ~10 s locally, and is far smarter.

The screenshot and question go to that gateway, so this is opt-in (Settings -> Brain). Point it at any
OpenAI-compatible chat endpoint with the BUDDY_SMART_URL and BUDDY_SMART_MODEL environment variables. It logs
in with the MAAS account set up for Pips' token card (pips/maas_tokens).

Same methods Buddy uses on its other brains: warm_up, prefill, chat_stream.
"""
import json
import os
import logging
import time
import urllib.error
import urllib.request

import keyit_gpu

# Your gateway's OpenAI-compatible chat endpoint and a model that can read images
CHAT_URL = os.environ.get("BUDDY_SMART_URL", "https://ai-gateway.example.com/v1/chat/completions")
MODEL = os.environ.get("BUDDY_SMART_MODEL", "vision-model")
MAX_TOKENS = 400


def _accounts():
    """Pips' MAAS login helper (Buddy hosts Pips, so it's importable); None in a standalone KeyIt."""
    try:
        import maas_tokens
    except ImportError:
        return None
    return maas_tokens if maas_tokens.enabled() else None


def available():
    """'' when the smart brain can be used, else why not."""
    accounts = _accounts()
    if accounts is None:
        return "no MAAS account set up (Pips config.json -> maas)"
    if not accounts.access_token():
        return "MAAS login failed"
    return ""


class MaasClient:
    gpu = False
    smart = True

    def __init__(self):
        self.accounts = _accounts()

    def warm_up(self, model=None):
        if not self.accounts or not self.accounts.access_token():
            raise RuntimeError("MAAS login failed")

    def prefill(self, model, messages, stop):
        return False                      # nothing to read ahead: the gateway is fast enough

    def _request(self, messages, force_login=False):
        token = self.accounts.access_token(force=force_login) if self.accounts else None
        if not token:
            raise RuntimeError("MAAS login failed")
        body = {"model": MODEL, "messages": keyit_gpu.to_openai(messages), "temperature": 0.2,
                "max_tokens": MAX_TOKENS, "stream": True,
                "chat_template_kwargs": {"enable_thinking": False}}
        request = urllib.request.Request(CHAT_URL, json.dumps(body).encode(),
                                         {"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        return urllib.request.urlopen(request, timeout=60)

    def chat_stream(self, model, messages, timeout=60):
        started = time.perf_counter()
        try:
            response = self._request(messages)
        except urllib.error.HTTPError as error:
            if error.code != 401:
                raise RuntimeError(f"MAAS error {error.code}: {error.read(200)!r}") from error
            response = self._request(messages, force_login=True)          # token expired: log in again once
        with response:
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
                if content:
                    if first:
                        logging.info("Smart brain (MAAS %s) first words after %.1fs", MODEL,
                                     time.perf_counter() - started)
                        first = False
                    yield content
