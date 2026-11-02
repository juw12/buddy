"""Soundscape engine for the Pips.

Synthesises seamlessly loopable mono 22.05 kHz 16-bit PCM WAVs with the pure
standard library and plays them through ``winsound``.

Public API (see CONTRACT.md):

    class SoundEngine:
        __init__(cfg, base_dir)
        sounds() -> list[str]
        ensure_assets() -> None
        play(name="rain") -> None
        stop() -> None
        toggle(name=None) -> bool
        is_playing() -> bool
        set_volume(percent) -> None
        volume_for(name) -> int
        set_sound_volume(name, pct) -> None
        fade_out(seconds=1.5) -> None
        fade_in(name=None, seconds=1.5) -> None
        chime() -> None

Design notes
------------
* Importing this module has NO side effects: no files are written, no sound is
  played and no threads are started. All synthesis happens on demand inside
  :meth:`SoundEngine.ensure_assets` / :meth:`SoundEngine.play`.
* ``winsound`` cannot change the volume of a playing sound, so volume is baked
  into the samples. Each integer percent is cached as
  ``data/sounds/<name>_<vol>.wav``.
* ``winsound`` can only play one sound at a time, so starting a new sound always
  purges the previous one first.
"""

from __future__ import annotations

import logging
import math
import os
import random
import struct
import threading
import wave
from pathlib import Path

import winsound

__all__ = ["SoundEngine", "SOUNDS"]

# --- Audio format -----------------------------------------------------------
SAMPLE_RATE = 22050          # Hz, mono
DURATION_SECONDS = 10.0      # nominal loop length
XFADE_SECONDS = 0.200        # equal-power crossfade (~200 ms)
SAMPLE_COUNT = int(SAMPLE_RATE * DURATION_SECONDS)
XFADE_SAMPLES = int(SAMPLE_RATE * XFADE_SECONDS)
GEN_COUNT = SAMPLE_COUNT + XFADE_SAMPLES  # synthesis length before loopify

# winsound flags (confirmed on this machine).
_SND_FILENAME = winsound.SND_FILENAME
_SND_ASYNC = winsound.SND_ASYNC
_SND_LOOP = winsound.SND_LOOP
_SND_PURGE = winsound.SND_PURGE

# Canonical sound names / deterministic per-name seeds.
SOUNDS = ["rain", "thunder", "brown", "cafe",
          "crickets", "ocean", "wind", "fireplace", "forest"]
_SEEDS = {"rain": 101, "thunder": 202, "brown": 303, "cafe": 404,
          "crickets": 505, "ocean": 606, "wind": 707,
          "fireplace": 808, "forest": 909}

# --- Lazy logging -----------------------------------------------------------
_LOG = logging.getLogger("pips.audio")
_LOG.propagate = False
_LOG_READY = False


def _setup_log(log_dir):
    """Attach a file handler to logs/widget.log exactly once (lazy)."""
    global _LOG_READY
    if _LOG_READY:
        return _LOG
    try:
        os.makedirs(log_dir, exist_ok=True)
        handler = logging.FileHandler(
            os.path.join(log_dir, "widget.log"), encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        _LOG.addHandler(handler)
        _LOG.setLevel(logging.INFO)
        _LOG_READY = True
    except Exception:
        # Logging must never break playback.
        pass
    return _LOG


# --- Synthesis primitives ----------------------------------------------------

def _loopify(src):
    """Equal-power crossfade the tail of ``src`` into its head.

    ``src`` has ``SAMPLE_COUNT + XFADE_SAMPLES`` samples; the returned buffer has
    ``SAMPLE_COUNT`` samples and loops without a click.
    """
    n = len(src) - XFADE_SAMPLES
    out = [0.0] * n
    half_pi = 0.5 * math.pi
    for i in range(XFADE_SAMPLES):
        t = i / XFADE_SAMPLES
        f_in = math.sin(half_pi * t)   # ramps 0 -> 1
        f_out = math.cos(half_pi * t)  # ramps 1 -> 0
        out[i] = src[i] * f_in + src[n + i] * f_out
    out[XFADE_SAMPLES:n] = src[XFADE_SAMPLES:n]
    return out


def _normalize(buf, peak=0.85):
    mx = 0.0
    for v in buf:
        av = v if v >= 0.0 else -v
        if av > mx:
            mx = av
    if mx <= 1e-9:
        return buf
    k = peak / mx
    return [v * k for v in buf]


def _to_pcm(buf):
    out = []
    for v in buf:
        x = int(v * 32767.0)
        if x > 32767:
            x = 32767
        elif x < -32768:
            x = -32768
        out.append(x)
    return out


def _white(rng, m):
    return [rng.uniform(-1.0, 1.0) for _ in range(m)]


def _one_pole_lowpass(buf, alpha):
    """Simple causal one-pole IIR low-pass filter."""
    y = 0.0
    out = [0.0] * len(buf)
    for i, x in enumerate(buf):
        y += alpha * (x - y)
        out[i] = y
    return out


def _add_droplet(buf, rng):
    """Add a single sparse, fast-decaying droplet transient in place."""
    m = len(buf)
    dur = int(0.015 * SAMPLE_RATE)  # ~15 ms
    if dur < 8 or m <= dur + 1:
        return
    start = rng.randrange(0, m - dur)
    freq = rng.uniform(1200.0, 4500.0)
    amp = rng.uniform(0.15, 0.55)
    decay = rng.uniform(5.0, 12.0)
    w = 2.0 * math.pi * freq / SAMPLE_RATE
    for i in range(dur):
        env = math.exp(-decay * i / dur)
        buf[start + i] += amp * env * math.sin(w * i)


# --- Per-sound generators (return float buffers of length GEN_COUNT) ---------

def _gen_rain(rng, m):
    """Low-pass filtered white noise plus sparse droplet transients."""
    lp = _one_pole_lowpass(_white(rng, m), 0.45)
    n_drops = int(m / SAMPLE_RATE * 14.0)  # ~14 droplets per second
    for _ in range(n_drops):
        _add_droplet(lp, rng)
    return lp


def _gen_thunder(rng, m):
    """Low rumbling filtered noise with occasional deeper swells."""
    deep = _one_pole_lowpass(_white(rng, m), 0.02)   # ~70 Hz rumble
    body = _one_pole_lowpass(_white(rng, m), 0.12)   # adds texture
    src = [deep[i] + 0.4 * body[i] for i in range(m)]

    # Deeper swells. Wrap around so the loop stays continuous.
    for _ in range(3):
        start = rng.randrange(0, m)
        width = max(1, int(rng.uniform(0.8, 2.0) * SAMPLE_RATE))
        gain = rng.uniform(2.0, 4.0)
        for i in range(width):
            idx = (start + i) % m
            e = math.sin(math.pi * (i / width))
            src[idx] *= 1.0 + gain * e * e
    return src


def _gen_brown(rng, m):
    """Integrated (brownian) white noise, DC removed."""
    b = 0.0
    leak = 0.998
    step = 0.05
    out = [0.0] * m
    for i in range(m):
        b = b * leak + rng.uniform(-1.0, 1.0) * step
        out[i] = b
    mean = sum(out) / m
    return [v - mean for v in out]


def _gen_cafe(rng, m):
    """Pink-ish noise plus a slow low murmur (distinct from rain)."""
    white = _white(rng, m)

    # Paul Kellet pink-noise filter.
    b0 = b1 = b2 = b3 = b4 = b5 = b6 = 0.0
    pink = [0.0] * m
    for i, x in enumerate(white):
        b0 = 0.99886 * b0 + x * 0.0555179
        b1 = 0.99332 * b1 + x * 0.0750759
        b2 = 0.96900 * b2 + x * 0.1538520
        b3 = 0.86650 * b3 + x * 0.3104856
        b4 = 0.55000 * b4 + x * 0.5329522
        b5 = -0.7616 * b5 - x * 0.0168980
        pink[i] = b0 + b1 + b2 + b3 + b4 + b5 + b6 + x * 0.5362
        b6 = x * 0.115926

    # Muffled low murmur with slow amplitude modulation.
    murmur = _one_pole_lowpass(white, 0.05)
    for i in range(m):
        t = i / SAMPLE_RATE
        am = 0.5 + 0.5 * math.sin(2.0 * math.pi * 0.3 * t)
        murmur[i] *= am

    return [pink[i] * 0.5 + murmur[i] * 2.0 for i in range(m)]


def _gen_crickets(rng, m):
    """Nighttime crickets (jangkrik): soft low bed + irregular high chirps.

    The bed is heavily low-passed noise; on top sit call-and-response bursts
    of short (~15-30 ms) 4-5 kHz chirps with a fast amplitude tremolo. Each
    call is a little cluster of chirps followed by a pause. Events wrap around
    the buffer so the loop stays continuous.
    """
    bed = _one_pole_lowpass(_white(rng, m), 0.06)
    out = [v * 0.25 for v in bed]

    def _chirp(start, freq, amp):
        dur = int(rng.uniform(0.015, 0.030) * SAMPLE_RATE)
        if dur < 8:
            return
        attack = max(1, int(0.002 * SAMPLE_RATE))
        tw = 2.0 * math.pi * rng.uniform(25.0, 45.0) / SAMPLE_RATE  # tremolo
        w = 2.0 * math.pi * freq / SAMPLE_RATE
        for i in range(dur):
            idx = (start + i) % m
            if i < attack:
                e = i / attack
            else:
                e = math.exp(-6.0 * (i - attack) / max(1, dur - attack))
            tr = 0.55 + 0.45 * math.sin(tw * i)
            out[idx] += amp * e * tr * math.sin(w * i)

    total = m / SAMPLE_RATE
    t = 0.0
    while t < total - 0.2:
        start = int(t * SAMPLE_RATE)
        n_chirps = rng.randint(2, 5)
        freq = rng.uniform(4000.0, 5000.0)          # per-call pitch
        amp = rng.uniform(0.18, 0.32)
        for k in range(n_chirps):
            s = start + int(k * rng.uniform(0.045, 0.085) * SAMPLE_RATE)
            _chirp(s, freq * rng.uniform(0.97, 1.03), amp * rng.uniform(0.8, 1.0))
        t += rng.uniform(0.7, 1.2)                  # gap between calls
    return out


def _gen_ocean(rng, m):
    """Slow rolling ocean swells: a crash, then a long decay.

    Low-passed noise provides the body/foam; a small number of wrap-around
    swells (period ~7-10 s) shape it into waves. Envelope windows start and
    end at unity gain so the loop stays click-free.
    """
    white = _white(rng, m)
    deep = _one_pole_lowpass(white, 0.03)   # deep body
    foam = _one_pole_lowpass(white, 0.30)   # brighter surface
    base = [deep[i] * 3.0 + foam[i] * 0.8 for i in range(m)]

    for _ in range(rng.randint(1, 2)):
        width = max(1, int(rng.uniform(7.0, 10.0) * SAMPLE_RATE))
        start = rng.randrange(0, m)
        crash = 0.12  # fraction of the period spent rising into the crash
        for i in range(width):
            idx = (start + i) % m
            p = i / width
            if p < crash:
                e = p / crash
            else:
                e = math.exp(-4.0 * (p - crash) / (1.0 - crash))
            base[idx] *= 1.0 + 3.0 * e
    return base


def _gen_wind(rng, m):
    """Heavily low-passed noise with slow amplitude + timbre gusts.

    Both modulators use an integer number of cycles across the buffer so the
    result is exactly periodic (no clicks).
    """
    white = _white(rng, m)
    slow = _one_pole_lowpass(white, 0.015)   # deep gust body
    soft = _one_pole_lowpass(white, 0.06)    # a touch of air

    total = m / SAMPLE_RATE
    n_amp = max(1, int(round(total / rng.uniform(4.0, 8.0))))
    n_tim = max(1, int(round(total / rng.uniform(5.0, 8.0))))
    w_amp = 2.0 * math.pi * n_amp / m
    w_tim = 2.0 * math.pi * n_tim / m

    out = [0.0] * m
    for i in range(m):
        am = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(w_amp * i))   # 0.45..1.0
        tim = 0.5 + 0.5 * math.sin(w_tim * i)                  # 0..1
        out[i] = (slow[i] * (1.0 - 0.4 * tim) + soft[i] * (0.5 + 0.5 * tim)) * am
    return out


def _gen_fireplace(rng, m):
    """Low rumbling fire plus sparse random crackle pops."""
    rumble = _one_pole_lowpass(_white(rng, m), 0.03)
    base = [v * 3.0 for v in rumble]

    def _crackle(start):
        dur = int(rng.uniform(0.008, 0.030) * SAMPLE_RATE)
        if dur < 6:
            return
        amp = rng.uniform(0.12, 0.40)
        decay = rng.uniform(8.0, 20.0)
        for i in range(dur):
            idx = (start + i) % m
            e = math.exp(-decay * i / dur)
            base[idx] += amp * e * rng.uniform(-1.0, 1.0)

    n = int(m / SAMPLE_RATE * 5.0)  # ~5 crackles per second
    for _ in range(n):
        _crackle(rng.randrange(0, m))
    return base


def _gen_forest(rng, m):
    """Gentle pink-ish bed plus a few sparse, softly swept bird chirps."""
    white = _white(rng, m)

    # Paul Kellet pink-noise filter (same approach as _gen_cafe).
    b0 = b1 = b2 = b3 = b4 = b5 = b6 = 0.0
    pink = [0.0] * m
    for i, x in enumerate(white):
        b0 = 0.99886 * b0 + x * 0.0555179
        b1 = 0.99332 * b1 + x * 0.0750759
        b2 = 0.96900 * b2 + x * 0.1538520
        b3 = 0.86650 * b3 + x * 0.3104856
        b4 = 0.55000 * b4 + x * 0.5329522
        b5 = -0.7616 * b5 - x * 0.0168980
        pink[i] = b0 + b1 + b2 + b3 + b4 + b5 + b6 + x * 0.5362
        b6 = x * 0.115926

    air = _one_pole_lowpass(white, 0.10)
    out = [pink[i] * 0.5 + air[i] * 0.6 for i in range(m)]

    def _bird(start):
        dur = int(rng.uniform(0.04, 0.12) * SAMPLE_RATE)
        if dur < 8:
            return
        f0 = rng.uniform(2000.0, 3500.0)
        f1 = f0 * rng.uniform(1.3, 2.2)              # upward sweep
        amp = rng.uniform(0.06, 0.14)
        attack = max(1, int(0.004 * SAMPLE_RATE))
        phase = 0.0
        for i in range(dur):
            idx = (start + i) % m
            f = f0 + (f1 - f0) * (i / dur)
            phase += 2.0 * math.pi * f / SAMPLE_RATE
            if i < attack:
                e = i / attack
            else:
                e = math.exp(-4.0 * (i - attack) / max(1, dur - attack))
            out[idx] += amp * e * math.sin(phase)

    for _ in range(rng.randint(1, 3)):                # ~1-3 chirps per 10 s
        _bird(rng.randrange(0, m))
    return out


_GENERATORS = {
    "rain": _gen_rain,
    "thunder": _gen_thunder,
    "brown": _gen_brown,
    "cafe": _gen_cafe,
    "crickets": _gen_crickets,
    "ocean": _gen_ocean,
    "wind": _gen_wind,
    "fireplace": _gen_fireplace,
    "forest": _gen_forest,
}


def _synthesize(name):
    """Return a list of int16 samples (length SAMPLE_COUNT) for ``name``."""
    gen = _GENERATORS.get(name, _gen_rain)
    rng = random.Random(_SEEDS.get(name, 0))
    src = gen(rng, GEN_COUNT)
    looped = _loopify(src)
    normalized = _normalize(looped, 0.85)
    return _to_pcm(normalized)


def _synth_chime():
    """A short rising two-note chime (~1 s) used for reminder alerts."""
    total = int(SAMPLE_RATE * 1.0)
    out = [0.0] * total
    attack = max(1, int(0.005 * SAMPLE_RATE))
    for freq, start_s, dur_s, amp in (
        (880.00, 0.00, 0.55, 0.55),   # A5
        (1174.66, 0.32, 0.70, 0.50),  # D6
    ):
        start = int(start_s * SAMPLE_RATE)
        dur = max(attack + 1, int(dur_s * SAMPLE_RATE))
        for i in range(dur):
            idx = start + i
            if idx >= total:
                break
            env = (i / attack) if i < attack else math.exp(-3.5 * (i - attack) / (dur - attack))
            out[idx] += amp * env * math.sin(2.0 * math.pi * freq * i / SAMPLE_RATE)
    return _to_pcm(_normalize(out, 0.6))


def _samples_to_bytes(samples):
    parts = []
    step = 8192
    fmt = "<%dh"
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step]
        parts.append(struct.pack(fmt % len(chunk), *chunk))
    return b"".join(parts)


def _write_wav(path, samples):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(_samples_to_bytes(samples))


# --- Engine ------------------------------------------------------------------

class SoundEngine:
    """Synthesise and play a looping soundscape via ``winsound``."""

    def __init__(self, cfg, base_dir):
        self.base_dir = str(base_dir)
        self.data_dir = os.path.join(self.base_dir, "data", "sounds")
        self.log_dir = os.path.join(self.base_dir, "logs")

        try:
            audio_cfg = dict((cfg or {}).get("audio", {}) or {})
        except Exception:
            audio_cfg = {}

        default = str(audio_cfg.get("default", "rain"))
        self._default = default if default in SOUNDS else SOUNDS[0]

        try:
            vol = int(audio_cfg.get("volume", 60))
        except Exception:
            vol = 60
        self._volume = max(0, min(100, vol))

        # Per-sound presets (name -> 0..100). Missing names fall back to the
        # global default (self._volume).
        self._volumes = {}
        try:
            raw_vols = dict(audio_cfg.get("volumes", {}) or {})
        except Exception:
            raw_vols = {}
        for _nm, _pv in raw_vols.items():
            try:
                _iv = int(_pv)
            except Exception:
                continue
            self._volumes[str(_nm)] = max(0, min(100, _iv))

        self._lock = threading.RLock()
        self._playing = None          # name currently playing, or None
        self._raw_cache = {}          # name -> list[int] base samples
        self._log = None              # logger attached lazily
        self._fade_gen = 0            # generation counter cancels pending fade hand-offs

    # -- helpers -------------------------------------------------------------

    def _logger(self):
        if self._log is None:
            self._log = _setup_log(self.log_dir)
        return self._log

    def _gain_path(self, name, vol):
        return os.path.join(self.data_dir, "%s_%d.wav" % (name, int(vol)))

    def _raw_samples(self, name):
        cached = self._raw_cache.get(name)
        if cached is None:
            cached = _synthesize(name)
            self._raw_cache[name] = cached
        return cached

    def _ensure_gain_file(self, name, vol):
        """Return the path of the cached ``<name>_<vol>.wav``, writing it if needed."""
        path = self._gain_path(name, vol)
        if os.path.exists(path):
            return path
        os.makedirs(self.data_dir, exist_ok=True)
        base = self._raw_samples(name)
        gain = int(vol) / 100.0
        scaled = [int(round(s * gain)) for s in base]
        _write_wav(path, scaled)
        self._logger().info("synthesised sound asset %s", path)
        return path

    def _fade_out_path(self, name, vol):
        return os.path.join(self.data_dir, "%s_out_%d.wav" % (name, int(vol)))

    def _fade_in_path(self, name, vol, seconds):
        tag = ("%g" % float(seconds))
        return os.path.join(
            self.data_dir, "%s_in_%d_%s.wav" % (name, int(vol), tag)
        )

    def _fade_len(self, seconds, limit):
        n = int(max(0.05, float(seconds)) * SAMPLE_RATE)
        return max(1, min(n, limit))

    def _ensure_fade_out_file(self, name, vol, seconds):
        """Cache a one-shot fade-OUT WAV (length approx ``seconds``)."""
        path = self._fade_out_path(name, vol)
        if os.path.exists(path):
            return path
        os.makedirs(self.data_dir, exist_ok=True)
        base = self._raw_samples(name)
        gain = int(vol) / 100.0
        n = self._fade_len(seconds, len(base))
        denom = (n - 1) if n > 1 else 1
        out = [0] * n
        for i in range(n):
            t = i / denom
            env = 0.5 * (1.0 + math.cos(math.pi * t))   # smooth 1 -> 0
            out[i] = int(round(base[i] * gain * env))
        _write_wav(path, out)
        self._logger().info("synthesised fade-out asset %s", path)
        return path

    def _ensure_fade_in_file(self, name, vol, seconds):
        """Cache a one-loop lead-in WAV with a fade-IN over the first ``seconds``."""
        path = self._fade_in_path(name, vol, seconds)
        if os.path.exists(path):
            return path
        os.makedirs(self.data_dir, exist_ok=True)
        base = self._raw_samples(name)
        gain = int(vol) / 100.0
        m = len(base)
        n = self._fade_len(seconds, m)
        denom = (n - 1) if n > 1 else 1
        out = [0] * m
        for i in range(m):
            if i < n:
                t = i / denom
                env = 0.5 * (1.0 - math.cos(math.pi * t))   # smooth 0 -> 1
            else:
                env = 1.0
            out[i] = int(round(base[i] * gain * env))
        _write_wav(path, out)
        self._logger().info("synthesised fade-in asset %s", path)
        return path

    # -- public API ----------------------------------------------------------

    def sounds(self):
        """Return the list of available soundscape names."""
        return list(SOUNDS)

    def ensure_assets(self):
        """Synthesise any missing WAVs into ``data/sounds/`` for the current volume."""
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            vol = self._volume
            for name in SOUNDS:
                self._ensure_gain_file(name, vol)
        except Exception:
            self._logger().exception("ensure_assets failed")

    def play(self, name="rain"):
        """Start looping ``name`` asynchronously. Idempotent."""
        try:
            if name not in SOUNDS:
                name = self._default

            with self._lock:
                if self._playing == name:
                    return
                vol = self.volume_for(name)

            # Synthesis may take ~1 s; do it outside the lock.
            path = self._ensure_gain_file(name, vol)

            with self._lock:
                winsound.PlaySound(None, _SND_PURGE)
                winsound.PlaySound(
                    path,
                    _SND_FILENAME | _SND_ASYNC | _SND_LOOP,
                )
                self._playing = name
            self._logger().info("playing %s @ %d%%", name, vol)
        except Exception:
            self._logger().exception("play(%r) failed", name)

    def stop(self):
        """Stop playback."""
        try:
            with self._lock:
                self._fade_gen += 1
                winsound.PlaySound(None, _SND_PURGE)
                self._playing = None
            self._logger().info("sound stopped")
        except Exception:
            self._logger().exception("stop failed")

    def toggle(self, name=None):
        """Toggle playback; return the new playing state."""
        with self._lock:
            playing = self._playing is not None
        if playing:
            self.stop()
            return False
        self.play(name if name is not None else self._default)
        with self._lock:
            return self._playing is not None

    def is_playing(self):
        """Return True while a sound is looping."""
        with self._lock:
            return self._playing is not None

    def set_volume(self, percent):
        """Set volume 0..100; re-cache the gain file and resume if playing."""
        try:
            vol = int(percent)
        except Exception:
            vol = self._volume
        vol = max(0, min(100, vol))

        with self._lock:
            self._volume = vol
            playing = self._playing
            if playing is not None:
                self._volumes[playing] = vol

        # Cache the new gain file (synthesis outside the lock).
        name = playing if playing is not None else self._default
        try:
            path = self._ensure_gain_file(name, vol)
        except Exception:
            self._logger().exception("set_volume: could not cache %s @ %d", name, vol)
            return

        if playing is not None:
            try:
                with self._lock:
                    if self._playing == playing:
                        winsound.PlaySound(None, _SND_PURGE)
                        winsound.PlaySound(
                            path,
                            _SND_FILENAME | _SND_ASYNC | _SND_LOOP,
                        )
                self._logger().info("volume set to %d%% (resumed %s)", vol, playing)
            except Exception:
                self._logger().exception("set_volume: resume failed")

    def volume_for(self, name):
        """Return the preset volume (0..100) for ``name`` (falls back to global)."""
        try:
            with self._lock:
                vol = self._volumes.get(name, self._volume)
            return max(0, min(100, int(vol)))
        except Exception:
            return self._volume

    def set_sound_volume(self, name, pct):
        """Set ``name``'s preset volume; re-cache and resume if it is playing."""
        try:
            if name not in SOUNDS:
                name = self._default
            vol = int(pct)
        except Exception:
            vol = self._volume
        vol = max(0, min(100, vol))

        with self._lock:
            self._volumes[name] = vol
            playing = self._playing

        try:
            path = self._ensure_gain_file(name, vol)
        except Exception:
            self._logger().exception(
                "set_sound_volume: could not cache %s @ %d", name, vol
            )
            return

        if playing == name:
            try:
                with self._lock:
                    if self._playing == name:
                        winsound.PlaySound(None, _SND_PURGE)
                        winsound.PlaySound(
                            path,
                            _SND_FILENAME | _SND_ASYNC | _SND_LOOP,
                        )
                self._logger().info(
                    "preset volume for %s set to %d%% (resumed)", name, vol
                )
            except Exception:
                self._logger().exception("set_sound_volume: resume failed")

    # -- fades ---------------------------------------------------------------

    def fade_out(self, seconds=1.5):
        """Fade the playing sound out then stop (one-shot, non-looping)."""
        try:
            with self._lock:
                name = self._playing
                if name is None:
                    return
                vol = self.volume_for(name)

            path = self._ensure_fade_out_file(name, vol, seconds)

            with self._lock:
                if self._playing != name:
                    return
                winsound.PlaySound(None, _SND_PURGE)
                winsound.PlaySound(path, _SND_FILENAME | _SND_ASYNC)
                self._playing = None
            self._logger().info("fade_out %s @ %d%% (%.2fs)", name, vol, seconds)
        except Exception:
            self._logger().exception("fade_out failed")

    def fade_in(self, name=None, seconds=1.5):
        """Fade a sound in from silence, then hand off to the seamless loop."""
        try:
            if name is None or name not in SOUNDS:
                name = self._default
            with self._lock:
                vol = self.volume_for(name)
                self._fade_gen += 1
                gen = self._fade_gen

            path = self._ensure_fade_in_file(name, vol, seconds)

            with self._lock:
                if gen != self._fade_gen:
                    return
                winsound.PlaySound(None, _SND_PURGE)
                winsound.PlaySound(path, _SND_FILENAME | _SND_ASYNC)
                self._playing = name

            loop_seconds = SAMPLE_COUNT / float(SAMPLE_RATE)
            timer = threading.Timer(
                loop_seconds, self._fade_in_handoff, args=(name, vol, gen)
            )
            timer.daemon = True
            timer.start()
            self._logger().info("fade_in %s @ %d%% (%.2fs)", name, vol, seconds)
        except Exception:
            self._logger().exception("fade_in failed")

    def _fade_in_handoff(self, name, vol, gen):
        """Switch the fade-in lead-in to the seamless looping file."""
        try:
            with self._lock:
                if gen != self._fade_gen or self._playing != name:
                    return
            path = self._ensure_gain_file(name, vol)
            with self._lock:
                if gen != self._fade_gen or self._playing != name:
                    return
                winsound.PlaySound(None, _SND_PURGE)
                winsound.PlaySound(
                    path,
                    _SND_FILENAME | _SND_ASYNC | _SND_LOOP,
                )
                self._playing = name
            self._logger().info("fade_in handoff -> loop %s @ %d%%", name, vol)
        except Exception:
            self._logger().exception("fade_in handoff failed")

    # -- alert chime ---------------------------------------------------------

    def _chime_path(self):
        path = os.path.join(self.data_dir, "chime.wav")
        if not os.path.exists(path):
            os.makedirs(self.data_dir, exist_ok=True)
            _write_wav(path, _synth_chime())
        return path

    def chime(self):
        """Play a one-shot chime; resume any looping soundscape afterwards."""
        try:
            with self._lock:
                resume = self._playing
                self._playing = None
            winsound.PlaySound(self._chime_path(), _SND_FILENAME | _SND_ASYNC)
            if resume is not None:
                timer = threading.Timer(1.4, self._chime_resume, args=(resume,))
                timer.daemon = True
                timer.start()
            self._logger().info("chime played (resume=%s)", resume)
        except Exception:
            self._logger().exception("chime failed")

    def _chime_resume(self, name):
        try:
            with self._lock:
                if self._playing is not None:
                    return
            self.play(name)
        except Exception:
            self._logger().exception("chime resume failed")


# --- Smoke test --------------------------------------------------------------

if __name__ == "__main__":
    import time

    _base = Path(__file__).resolve().parent
    _cfg = {"audio": {"default": "rain", "volume": 60}}
    _engine = SoundEngine(_cfg, str(_base))
    _engine.ensure_assets()
    print("assets ready in", _engine.data_dir)
    _engine.play("rain")
    print("playing:", _engine.is_playing())
    time.sleep(2.0)
    _engine.stop()
    print("playing after stop:", _engine.is_playing())
    print("smoke ok")
