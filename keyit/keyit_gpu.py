"""
Fast Buddy brain: the same Ollama model, run on the laptop's GPU.

Ollama on this kind of laptop (Intel Arc iGPU) runs on the CPU only, which takes 70-140 s to read a
screenshot. Ollama ships llama.cpp's own server and a Vulkan GPU backend, so Buddy starts that server
itself with the model files Ollama already downloaded: ~12 s for a fresh screenshot, ~4 s for a
follow-up. Nothing extra to download; if anything is missing Buddy quietly uses Ollama instead.

Settings that matter on Intel Arc 140V (measured, see dev_checks/gpu_quality_matrix.py):
  - GGML_VK_DISABLE_COOPMAT=1: the driver's cooperative-matrix path computes wrong results
    (made-up answers, "The@@@@", wrong coordinates). Without it answers and [POINT]s are correct.
  - f32 KV cache (-ctk/-ctv f32): the f16 cache still breaks long (screenshot) prompts.
  - 576 image tokens instead of Ollama's 1024: about 3x faster, same answers; the arrow is snapped
    to the exact UI element afterwards anyway (keyit_targets).
"""
import base64
import ctypes
import http.client
import json
import logging
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

OLLAMA_LIB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "lib" / "ollama"
SERVER_ARGS = [
    "-ngl", "99",                       # whole model on the GPU
    "-c", "8192",
    "--parallel", "1",                  # one conversation -> its cache is always reused
    "-t", "4",
    "-ctk", "f32", "-ctv", "f32",
    "--image-max-tokens", "576",        # ~3x faster than Ollama's 1024, answers unchanged
    "--no-webui",
]
SERVER_ENV = {"GGML_VK_DISABLE_COOPMAT": "1"}  # wrong results on Intel Arc otherwise
START_TIMEOUT_S = 120
IDLE_STOP_S = 30 * 60                   # free the ~6 GB of shared memory after 30 min without questions
CREATE_NO_WINDOW = 0x08000000
LOG_FILE = Path(tempfile.gettempdir()) / "keyit_gpu_server.log"


# --------------------------------------------------------------------------- finding the pieces
def models_dir():
    """Where Ollama keeps its models (OLLAMA_MODELS, also read from the registry in case Buddy was
    started before the variable was set)."""
    value = os.environ.get("OLLAMA_MODELS")
    if not value:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value = winreg.QueryValueEx(key, "OLLAMA_MODELS")[0]
        except OSError:
            value = None
    return Path(os.path.expandvars(value)) if value else Path.home() / ".ollama" / "models"


def model_files(model, root=None):
    """(weights, vision projector) blob paths for an Ollama model name like 'qwen3-vl:4b-instruct'."""
    root = root or models_dir()
    name, _, tag = model.partition(":")
    parts = name.split("/")
    if len(parts) == 1:
        parts = ["registry.ollama.ai", "library"] + parts
    elif len(parts) == 2:
        parts = ["registry.ollama.ai"] + parts
    manifest = root.joinpath("manifests", *parts, tag or "latest")
    layers = json.loads(manifest.read_text(encoding="utf-8"))["layers"]

    def blob(media_type):
        for layer in layers:
            if layer.get("mediaType") == media_type:
                path = root / "blobs" / layer["digest"].replace(":", "-")
                if path.exists():
                    return path
        return None

    return blob("application/vnd.ollama.image.model"), blob("application/vnd.ollama.image.projector")


def runtime():
    """(llama-server.exe, ggml-vulkan.dll) from the Ollama install, or None."""
    server = OLLAMA_LIB / "llama-server.exe"
    vulkan = OLLAMA_LIB / "vulkan" / "ggml-vulkan.dll"
    if server.exists() and vulkan.exists():
        return server, vulkan
    return None


def available(model):
    """Why the GPU brain can't run, or '' if it can."""
    if os.name != "nt":
        return "not Windows"
    if runtime() is None:
        return "Ollama's GPU runtime (lib\\ollama\\vulkan) was not found"
    try:
        weights, projector = model_files(model)
    except (OSError, ValueError, KeyError):
        return f"model '{model}' is not downloaded in Ollama"
    if weights is None or projector is None:
        return f"model '{model}' has no vision files"
    return ""


# --------------------------------------------------------------------------- server process
class _KillWithBuddy:
    """A Windows job object: the GPU server dies with Buddy, even if Buddy crashes."""

    class _Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _Extended(ctypes.Structure):
        pass

    _Extended._fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", ctypes.c_uint64 * 6),
                          ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                          ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    def __init__(self):
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self.handle = kernel32.CreateJobObjectW(None, None)
        info = self._Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(wintypes.HANDLE(self.handle), 9, ctypes.byref(info),
                                         ctypes.sizeof(info))

    def add(self, process):
        ctypes.windll.kernel32.AssignProcessToJobObject(wintypes.HANDLE(self.handle),
                                                        wintypes.HANDLE(int(process._handle)))


_job = None


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class GpuServer:
    """Starts llama-server on the GPU on demand and stops it after a long idle time."""

    def __init__(self, model):
        self.model = model
        self.process = None
        self.port = None
        self.lock = threading.Lock()
        self.last_used = time.monotonic()
        self.failed = ""
        threading.Thread(target=self._idle_watch, daemon=True).start()

    def running(self):
        return self.process is not None and self.process.poll() is None

    def ensure(self):
        """Start the server if needed and wait until it answers. Raises RuntimeError on failure."""
        global _job
        self.last_used = time.monotonic()
        with self.lock:
            if self.running():
                return
            reason = available(self.model)
            if reason:
                raise RuntimeError(reason)
            server, vulkan = runtime()
            weights, projector = model_files(self.model)
            self.port = _free_port()
            args = [str(server), "--model", str(weights), "--mmproj", str(projector),
                    "--host", "127.0.0.1", "--port", str(self.port)] + SERVER_ARGS
            env = dict(os.environ, GGML_BACKEND_PATH=str(vulkan), **SERVER_ENV)
            started = time.perf_counter()
            log = open(LOG_FILE, "wb")
            try:
                self.process = subprocess.Popen(args, env=env, stdout=log, stderr=subprocess.STDOUT,
                                                stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
            finally:
                log.close()
            try:
                if _job is None:
                    _job = _KillWithBuddy()
                _job.add(self.process)
            except Exception:
                logging.debug("Could not tie the GPU server to Buddy's lifetime", exc_info=True)
            while time.perf_counter() - started < START_TIMEOUT_S:
                if self.process.poll() is not None:
                    break
                try:
                    if self._get("/health", timeout=2).get("status") == "ok":
                        logging.info("GPU brain ready in %.1fs (port %d)", time.perf_counter() - started, self.port)
                        return
                except (OSError, ValueError, http.client.HTTPException):
                    pass
                time.sleep(0.5)
            self.stop()
            raise RuntimeError(f"the GPU server didn't start (see {LOG_FILE})")

    def _get(self, path, timeout):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{self.port}{path}", timeout=timeout) as response:
            return json.loads(response.read())

    def stop(self):
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()

    def _idle_watch(self):
        while True:
            time.sleep(60)
            if self.running() and time.monotonic() - self.last_used > IDLE_STOP_S:
                logging.info("GPU brain idle for %d min: stopping it to free memory", IDLE_STOP_S // 60)
                with self.lock:
                    self.stop()


# --------------------------------------------------------------------------- client (OllamaClient look-alike)
def to_openai(messages):
    """Ollama-style messages (content + images: [base64]) -> OpenAI chat messages."""
    converted = []
    for message in messages:
        images = message.get("images") or []
        if not images:
            converted.append({"role": message["role"], "content": message.get("content", "")})
            continue
        parts = [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + image}}
                 for image in images]
        if message.get("content"):
            parts.append({"type": "text", "text": message["content"]})
        converted.append({"role": message["role"], "content": parts})
    return converted


class GpuClient:
    """Same methods Buddy uses on OllamaClient: warm_up, prefill, chat_stream."""

    gpu = True

    def __init__(self, server):
        self.server = server

    def _payload(self, messages, **extra):
        payload = {"messages": to_openai(messages), "temperature": 0.2, "cache_prompt": True}
        payload.update(extra)
        return json.dumps(payload)

    def _connection(self, timeout):
        self.server.ensure()
        return http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=timeout)

    def warm_up(self, model=None):
        """Start the server and compile the GPU kernels with a one-word answer."""
        self.server.ensure()
        connection = self._connection(120)
        try:
            body = self._payload([{"role": "user", "content": "Hi"}], max_tokens=1)
            connection.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
            connection.getresponse().read()
        finally:
            connection.close()

    def prefill(self, model, messages, stop):
        """Read the screenshot now (look-ahead); the real question reuses it. `stop` aborts."""
        finished = threading.Event()
        try:
            connection = self._connection(300)
        except RuntimeError:
            return False

        def abort_when_stopped():
            while not finished.is_set():
                if stop.wait(0.3):
                    sock = connection.sock
                    if sock is not None:
                        try:
                            sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    connection.close()
                    return

        threading.Thread(target=abort_when_stopped, daemon=True).start()
        try:
            connection.request("POST", "/v1/chat/completions", self._payload(messages, max_tokens=1),
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            response.read()
            return response.status == 200 and not stop.is_set()
        except (OSError, http.client.HTTPException):
            return False
        finally:
            finished.set()
            connection.close()

    def chat_stream(self, model, messages, timeout=180):
        connection = self._connection(timeout)
        try:
            connection.request("POST", "/v1/chat/completions",
                               self._payload(messages, stream=True, max_tokens=400),
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f"GPU server error {response.status}: {response.read()[:200]!r}")
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                choices = chunk.get("choices") or [{}]
                content = (choices[0].get("delta") or {}).get("content")
                if content:
                    yield content
                if choices[0].get("finish_reason"):
                    break
        finally:
            connection.close()
