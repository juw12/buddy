"""
Record your own keyboard with the microphone and turn it into a KeyIt / Mechvibes pack.

How it works
  1. The mic is recorded while the keyboard hook timestamps every key down/up.
  2. Key timestamps are mapped onto the audio timeline. The unknown mic latency is
     estimated as the median offset between key events and detected sound onsets.
  3. Each event is cut at its detected onset (with a short pre-roll), trimmed before
     the next keystroke, and the most typical take per key is kept.
  4. All clips share one gain (relative loudness is preserved) and are written as a
     "multi" pack. Keys you didn't press borrow a recorded letter sound, so the pack
     also works in Mechvibes.

Only the per-key clips are saved; the full raw recording is discarded.
"""
import json
import re
import time
import wave
from collections import defaultdict
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100
PRE_ROLL_S = 0.003
MAX_DOWN_S = 0.16
MAX_UP_S = 0.11
MIN_CLIP_S = 0.025
NEXT_EVENT_GUARD_S = 0.002
COARSE_WINDOW_S = (-0.06, 0.25)     # where to look for a sound when latency is unknown
FINE_WINDOW_S = (-0.015, 0.03)      # where to look once latency is known
ISOLATION_S = 0.3                   # events this far from neighbours are used to estimate latency
TARGET_PEAK = 0.9

IMPORTANT_KEYS = {57: "Space", 28: "Enter", 14: "Backspace", 42: "Shift"}
STANDARD_CODES = (
    list(range(1, 15)) + list(range(15, 29)) + list(range(29, 59)) + list(range(59, 69))
    + [87, 88, 3612, 3613, 3640, 3655, 3657, 3663, 3665, 3666, 3667, 3675, 3676,
       57416, 57419, 57421, 57424]
)
LETTER_CODES = list(range(16, 26)) + list(range(30, 39)) + list(range(44, 51))


# --------------------------------------------------------------------------- analysis
def envelope(audio, sample_rate=SAMPLE_RATE, window_s=0.001):
    width = max(1, int(window_s * sample_rate))
    return np.convolve(np.abs(audio), np.ones(width) / width, mode="same")


def find_onset(env, start, stop, threshold, sample_rate=SAMPLE_RATE):
    """First upward threshold crossing in env[start:stop], refined back to the attack start."""
    start = max(1, start)
    stop = min(len(env), stop)
    if stop <= start:
        return None
    segment = env[start - 1:stop]
    rising = np.flatnonzero((segment[1:] > threshold) & (segment[:-1] <= threshold))
    if not rising.size:
        return None
    index = start + rising[0]
    limit = max(0, index - int(0.003 * sample_rate))
    while index > limit and env[index - 1] > threshold * 0.5:
        index -= 1
    return index


def slice_keystrokes(audio, events, audio_start_time, sample_rate=SAMPLE_RATE):
    """
    audio:            mono float32 recording
    events:           [(code, is_down, time_s), ...] on the same clock as audio_start_time
    audio_start_time: clock time of audio sample 0
    Returns ({(code, is_down): [clip, ...]}, info)
    """
    audio = np.asarray(audio, dtype=np.float32)
    audio = audio - np.mean(audio)
    env = envelope(audio, sample_rate)
    noise_floor = float(np.percentile(env, 20))
    threshold = max(noise_floor * 5, float(env.max()) * 0.03, 1e-4)
    ordered = sorted(events, key=lambda event: event[2])
    expected = [int((t - audio_start_time) * sample_rate) for _, _, t in ordered]

    # Pass 1: estimate mic latency from isolated key-downs
    times = [t for _, _, t in ordered]
    isolated = [
        i for i, (_, is_down, t) in enumerate(ordered)
        if is_down
        and (i == 0 or t - times[i - 1] > ISOLATION_S)
    ]
    candidates = isolated if len(isolated) >= 3 else [i for i, e in enumerate(ordered) if e[1]]
    shifts = []
    for i in candidates:
        onset = find_onset(
            env,
            expected[i] + int(COARSE_WINDOW_S[0] * sample_rate),
            expected[i] + int(COARSE_WINDOW_S[1] * sample_rate),
            threshold,
            sample_rate,
        )
        if onset is not None:
            shifts.append(onset - expected[i])
    shift = int(np.median(shifts)) if shifts else 0

    # Pass 2: precise onset for every event
    onsets = []
    for i, (_, is_down, _) in enumerate(ordered):
        target = expected[i] + shift
        onset = find_onset(
            env,
            target + int(FINE_WINDOW_S[0] * sample_rate),
            target + int(FINE_WINDOW_S[1] * sample_rate),
            threshold if is_down else threshold * 0.6,
            sample_rate,
        )
        onsets.append(onset if onset is not None else target)

    clips = defaultdict(list)
    skipped = 0
    for i, (code, is_down, _) in enumerate(ordered):
        begin = onsets[i] - int(PRE_ROLL_S * sample_rate)
        end = begin + int((MAX_DOWN_S if is_down else MAX_UP_S) * sample_rate)
        if i + 1 < len(ordered):
            end = min(end, onsets[i + 1] - int(NEXT_EVENT_GUARD_S * sample_rate))
        if begin < 0 or end > len(audio) or end - begin < MIN_CLIP_S * sample_rate:
            skipped += 1
            continue
        clips[(code, is_down)].append(audio[begin:end].copy())

    info = {
        "events": len(ordered),
        "clips": sum(len(v) for v in clips.values()),
        "skipped": skipped,
        "latency_ms": 1000.0 * shift / sample_rate,
        "latency_samples_used": len(shifts),
        "noise_floor": noise_floor,
        "onsets": onsets,
    }
    return dict(clips), info


def pick_typical(clips):
    """Choose the take whose peak is the median of all takes (robust against flukes)."""
    peaks = [float(np.abs(c).max()) for c in clips]
    order = np.argsort(peaks)
    return clips[order[len(order) // 2]]


def shape_clip(clip, sample_rate=SAMPLE_RATE):
    clip = clip - np.mean(clip[: max(1, int(PRE_ROLL_S * sample_rate))])
    fade_in = max(1, int(0.0005 * sample_rate))
    fade_out = min(int(0.008 * sample_rate), clip.size // 3)
    clip[:fade_in] *= np.linspace(0, 1, fade_in)
    if fade_out > 0:
        clip[-fade_out:] *= np.linspace(1, 0, fade_out)
    return clip


def write_wav(path, mono, sample_rate=SAMPLE_RATE):
    pcm = np.clip(mono * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())


def unique_pack_dir(root, name, reserved_names=()):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "my-keyboard"
    candidate = Path(root) / slug
    counter = 2
    while candidate.exists() or candidate.name in reserved_names:
        candidate = Path(root) / f"{slug}-{counter}"
        counter += 1
    return candidate


def build_pack(clips, name, packs_root, sample_rate=SAMPLE_RATE, reserved_names=()):
    """Write a 'multi' pack from slice_keystrokes() output. Returns (pack_dir, report)."""
    chosen = {key: shape_clip(pick_typical(takes), sample_rate) for key, takes in clips.items()}
    down_codes = sorted(code for code, is_down in chosen if is_down)
    if not down_codes:
        raise ValueError("No usable key-down sounds were captured. Type a bit slower and closer to the mic.")
    gain = TARGET_PEAK / max(float(np.abs(c).max()) for c in chosen.values())

    pack_dir = unique_pack_dir(packs_root, name, reserved_names)
    pack_dir.mkdir(parents=True)
    defines, defines_up = {}, {}
    for (code, is_down), clip in sorted(chosen.items()):
        filename = f"k{code}.wav" if is_down else f"k{code}_up.wav"
        write_wav(pack_dir / filename, clip * gain, sample_rate)
        (defines if is_down else defines_up)[str(code)] = filename

    # Fill unrecorded keys with recorded letter sounds (round-robin) for Mechvibes compatibility
    for mapping in (defines, defines_up):
        recorded = [str(c) for c in LETTER_CODES if str(c) in mapping] or list(mapping.keys())
        if not recorded:
            continue
        donors = [mapping[c] for c in recorded]
        for position, code in enumerate(c for c in STANDARD_CODES if str(c) not in mapping):
            mapping[str(code)] = donors[position % len(donors)]

    config = {
        "id": f"keyit-{pack_dir.name}-{int(time.time())}",
        "name": name,
        "key_define_type": "multi",
        "includes_numpad": False,
        "sound": defines[str(down_codes[0])],
        "defines": defines,
        "defines_up": defines_up,
        "keyit": {"recorded": True, "recorded_down_keys": len(down_codes)},
    }
    (pack_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    report = {
        "pack_dir": str(pack_dir),
        "recorded_down_keys": len(down_codes),
        "recorded_up_keys": sum(1 for _, is_down in chosen if not is_down),
        "missing_important": [label for code, label in IMPORTANT_KEYS.items() if (code, True) not in chosen],
    }
    return pack_dir, report


# --------------------------------------------------------------------------- live capture
class MicCapture:
    """Records the default microphone; maps sample 0 onto time.perf_counter()."""

    def __init__(self, sample_rate=SAMPLE_RATE, device=None):
        import sounddevice as sd

        self.sample_rate = sample_rate
        self.blocks = []
        self.frames = 0
        self.anchors = []
        self.level = 0.0
        self.stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            device=device,
            latency="low",
            callback=self._callback,
        )
        self.input_latency = float(self.stream.latency)

    def _callback(self, indata, frames, time_info, status):
        now = time.perf_counter()
        self.blocks.append(indata[:, 0].copy())
        self.frames += frames
        # The newest sample was captured about `input_latency` ago
        self.anchors.append(now - self.input_latency - self.frames / self.sample_rate)
        self.level = float(np.abs(indata).max())

    def start(self):
        self.stream.start()

    def stop(self):
        self.stream.stop()
        self.stream.close()
        audio = np.concatenate(self.blocks) if self.blocks else np.zeros(0, dtype=np.float32)
        # Callbacks only run late, never early -> a low percentile is the best estimate
        start_time = float(np.percentile(self.anchors, 10)) if self.anchors else time.perf_counter()
        return audio, start_time


class RecordingSession:
    """Ties the keyboard hook and the mic together. Mutes KeyIt playback while recording."""

    def __init__(self, hook, player):
        self.hook = hook
        self.player = player
        self.events = []
        self.mic = None
        self.started_at = None

    def _on_key(self, code, is_down, event_time):
        self.events.append((code, is_down, event_time))

    def start(self):
        self.events = []
        self.mic = MicCapture()
        self.player.suppressed = True
        self.mic.start()
        # Only listen to keys once audio is actually flowing, so every event has audio
        deadline = time.perf_counter() + 1.0
        while self.mic.frames == 0 and time.perf_counter() < deadline:
            time.sleep(0.005)
        self.hook.observers.append(self._on_key)
        self.started_at = time.perf_counter()

    def stop(self):
        try:
            audio, audio_start = self.mic.stop()
        finally:
            if self._on_key in self.hook.observers:
                self.hook.observers.remove(self._on_key)
            self.player.suppressed = False
        return audio, audio_start, list(self.events)

    def key_count(self):
        return sum(1 for _, is_down, _ in self.events if is_down)

    def recorded_codes(self):
        return {code for code, is_down, _ in self.events if is_down}
