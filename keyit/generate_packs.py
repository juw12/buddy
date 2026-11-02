"""
Synthesize placeholder mechanical-keyboard sound packs in Mechvibes format.

Creates:
  packs/synth-thock/   -> "multi" format  (one WAV per sound, defines -> filename)
  packs/synth-clicky/  -> "single" format (one sprite WAV, defines -> [start_ms, duration_ms])

These are synthetic stand-ins so the app works out of the box. For realistic
sound, drop real Mechvibes packs (or your own recordings) into packs/.
"""
import json
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100
PACKS_DIR = Path(__file__).parent / "packs"

# Mechvibes / iohook key codes (Windows scan code set 1, extended keys = 0xE000 | scan)
ALPHANUMERIC_CODES = (
    list(range(2, 14))      # 1..0 - =
    + list(range(16, 28))   # Q..P [ ]
    + list(range(30, 42))   # A..L ; ' `
    + list(range(43, 54))   # \ Z..M , . /
    + list(range(59, 69))   # F1..F10
    + [1, 15, 58, 87, 88]   # Esc, Tab, CapsLock, F11, F12
    + [3663, 3665, 3655, 3657, 3666, 3667]           # End PgDn Home PgUp Ins Del
    + [57416, 57419, 57421, 57424]                   # arrows
)
SPACE_CODES = [57]
ENTER_CODES = [28, 3612]
BACKSPACE_CODES = [14]
MODIFIER_CODES = [29, 42, 54, 56, 3613, 3640, 3675, 3676]  # Ctrl Shift Alt Win (L/R)


def seconds(duration):
    return np.arange(int(SAMPLE_RATE * duration)) / SAMPLE_RATE


def damped_tone(t, frequency, decay, pitch_drop=0.0):
    """Sine with exponential decay; optional downward pitch glide (thock body)."""
    instantaneous = frequency * (1.0 - pitch_drop * (1.0 - np.exp(-t / 0.01)))
    phase = 2 * np.pi * np.cumsum(instantaneous) / SAMPLE_RATE
    return np.sin(phase) * np.exp(-t / decay)


def transient(t, rng, decay, brightness):
    """Short noise burst; brightness 0..1 blends from low-passed to high-passed noise."""
    noise = rng.standard_normal(t.size)
    low = np.convolve(noise, np.ones(6) / 6, mode="same")
    high = np.diff(noise, prepend=0.0)
    return (brightness * high + (1 - brightness) * low) * np.exp(-t / decay)


def delayed(signal, delay_s):
    shift = int(delay_s * SAMPLE_RATE)
    return np.concatenate([np.zeros(shift), signal[: signal.size - shift]])


def finish(signal, peak=0.8):
    """Normalize, add 0.3 ms fade-in and 5 ms fade-out to avoid clicks/pops."""
    signal = signal - signal.mean()
    signal *= peak / (np.abs(signal).max() + 1e-9)
    fade_in = int(0.0003 * SAMPLE_RATE)
    fade_out = int(0.005 * SAMPLE_RATE)
    signal[:fade_in] *= np.linspace(0, 1, fade_in)
    signal[-fade_out:] *= np.linspace(1, 0, fade_out)
    return signal


def keystroke(rng, style, kind, release=False):
    """
    style: 'thock' (lubed linear) or 'clicky' (click-jacket switch)
    kind:  'alpha', 'space', 'enter', 'backspace', 'modifier'
    """
    big_key = kind in ("space", "enter", "backspace")
    duration = 0.16 if big_key else 0.09
    if release:
        duration *= 0.6
    t = seconds(duration)
    jitter = lambda value, spread=0.08: value * rng.uniform(1 - spread, 1 + spread)

    if style == "thock":
        body_hz = jitter(150 if big_key else 230)
        ping_hz = [jitter(1900), jitter(3100)]
        click_brightness = 0.25
    else:
        body_hz = jitter(260 if big_key else 380)
        ping_hz = [jitter(3800), jitter(5600)]
        click_brightness = 0.85

    if release:
        # Upstroke: stem returning to top housing - lighter, higher, shorter
        sound = 0.6 * transient(t, rng, 0.0015, click_brightness)
        sound += 0.35 * damped_tone(t, body_hz * 1.6, 0.010)
        if style == "clicky":
            sound += 0.7 * transient(t, rng, 0.0008, 1.0)  # click jacket snaps back
        return finish(sound, peak=0.5)

    # Downstroke: actuation (click/leaf) then bottom-out a few ms later
    bottom_out_delay = jitter(0.006 if style == "thock" else 0.009, 0.2)
    sound = np.zeros_like(t)
    if style == "clicky":
        sound += 0.9 * transient(t, rng, 0.0007, 1.0)  # sharp click jacket
        sound += 0.25 * damped_tone(t, jitter(6500), 0.003)
    else:
        sound += 0.25 * transient(t, rng, 0.001, click_brightness)

    impact = 0.8 * transient(t, rng, 0.002, click_brightness)
    impact += 1.0 * damped_tone(t, body_hz, 0.035 if big_key else 0.020, pitch_drop=0.15)
    impact += 0.2 * sum(damped_tone(t, hz, 0.006) for hz in ping_hz)
    if big_key:
        # Stabilizer rattle: a couple of small secondary impacts
        for rattle_delay in (0.012, 0.021):
            impact += 0.25 * delayed(transient(t, rng, 0.0015, 0.6), jitter(rattle_delay, 0.15))
    sound += delayed(impact, bottom_out_delay)
    return finish(sound, peak=0.85 if big_key else 0.8)


def write_wav(path, mono_signal):
    pcm = np.clip(mono_signal * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm.tobytes())


def key_groups():
    return {
        "alpha": ALPHANUMERIC_CODES,
        "space": SPACE_CODES,
        "enter": ENTER_CODES,
        "backspace": BACKSPACE_CODES,
        "modifier": MODIFIER_CODES,
    }


def build_multi_pack(name, style, seed, alpha_variants=6):
    """Mechvibes 'multi' pack: defines map keycode -> sound file."""
    rng = np.random.default_rng(seed)
    pack_dir = PACKS_DIR / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    defines, defines_up = {}, {}

    for kind, codes in key_groups().items():
        variant_count = alpha_variants if kind == "alpha" else 1
        down_files, up_files = [], []
        for index in range(variant_count):
            down_name = f"{kind}_{index}.wav"
            up_name = f"{kind}_{index}_up.wav"
            write_wav(pack_dir / down_name, keystroke(rng, style, kind))
            write_wav(pack_dir / up_name, keystroke(rng, style, kind, release=True))
            down_files.append(down_name)
            up_files.append(up_name)
        for position, code in enumerate(codes):
            defines[str(code)] = down_files[position % variant_count]
            defines_up[str(code)] = up_files[position % variant_count]

    config = {
        "id": f"keyit-{name}",
        "name": f"KeyIt {name.replace('-', ' ').title()} (synthetic)",
        "key_define_type": "multi",
        "includes_numpad": False,
        "sound": "alpha_0.wav",
        "defines": defines,
        # Extension (ignored by Mechvibes): separate key-release sounds
        "defines_up": defines_up,
    }
    (pack_dir / "config.json").write_text(json.dumps(config, indent=2))
    return pack_dir


def build_single_pack(name, style, seed, alpha_variants=6):
    """Mechvibes 'single' pack: one sprite file, defines map keycode -> [start_ms, duration_ms]."""
    rng = np.random.default_rng(seed)
    pack_dir = PACKS_DIR / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    gap = np.zeros(int(0.05 * SAMPLE_RATE))
    pieces, cursor_ms = [], 0.0
    defines, defines_up = {}, {}

    def append(signal):
        nonlocal cursor_ms
        start_ms = cursor_ms
        duration_ms = 1000.0 * signal.size / SAMPLE_RATE
        pieces.extend([signal, gap])
        cursor_ms += duration_ms + 1000.0 * gap.size / SAMPLE_RATE
        return [round(start_ms, 3), round(duration_ms, 3)]

    for kind, codes in key_groups().items():
        variant_count = alpha_variants if kind == "alpha" else 1
        down_slots = [append(keystroke(rng, style, kind)) for _ in range(variant_count)]
        up_slots = [append(keystroke(rng, style, kind, release=True)) for _ in range(variant_count)]
        for position, code in enumerate(codes):
            defines[str(code)] = down_slots[position % variant_count]
            defines_up[str(code)] = up_slots[position % variant_count]

    write_wav(pack_dir / "sound.wav", np.concatenate(pieces))
    config = {
        "id": f"keyit-{name}",
        "name": f"KeyIt {name.replace('-', ' ').title()} (synthetic)",
        "key_define_type": "single",
        "includes_numpad": False,
        "sound": "sound.wav",
        "defines": defines,
        "defines_up": defines_up,
    }
    (pack_dir / "config.json").write_text(json.dumps(config, indent=2))
    return pack_dir


if __name__ == "__main__":
    print("wrote", build_multi_pack("synth-thock", "thock", seed=1))
    print("wrote", build_single_pack("synth-clicky", "clicky", seed=2))


