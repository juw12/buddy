"""
pet_sounds.py - little voices for the desktop pet.

Every animal has its own voice and every voice changes with the pet's mood and with what just
happened (tickled, petted, eating, dizzy, sleepy, grumpy ...).

v2.21: all 51 animals have their own call now (before, many of the newer ones just meowed like
the cat). Calls can glide through vowel "formants", so a cow really moos, a sheep baas, a duck
quacks, a monkey goes ooh-ooh-aah-aah and a lion roars. Petting, eating and sleeping also sound
different per family: birds trill, peck and coo; sea animals blub, gulp and blow bubbles; bugs
hum; magical pets sparkle.

All sounds are synthesised here with the standard library - no audio files.
Clips are cached as small WAV files in %TEMP% and played through the Windows
MCI API, so they mix with (and never interrupt) the soundscape that audio.py
plays through winsound.
"""
from __future__ import annotations

import ctypes
import hashlib
import logging
import math
import random
import struct
import tempfile
import threading
import time
import wave
from pathlib import Path

logger = logging.getLogger("pet_sounds")

SR = 22050
SYNTH_VERSION = 2     # bump when the sound of any clip changes (old cached clips are ignored)
CACHE_ROOT = Path(tempfile.gettempdir()) / "pips_pet_sounds"
CACHE_DIR = CACHE_ROOT / f"v{SYNTH_VERSION}"
VARIANTS = 3          # a few takes per sound so it never feels like a loop
MIN_GAP_S = 0.35      # never stack sounds faster than this
MAX_PLAYING = 2
MAX_CLIP_S = 2.6      # longest clip we ever make

# ---------------------------------------------------------------------------
# Voices.  f0 = base pitch (Hz), timbre, call shape, note length (s), noise mix,
# vibrato depth, an optional amplitude-modulation "texture" (rate Hz, depth),
# optional vowels the call glides through (formants) and fscale (formant scale:
# > 1 = smaller animal / brighter, < 1 = bigger animal / darker).
VOICES = {
    # land
    "cat":       dict(f0=560, timbre="saw", shape="meow", dur=0.48, noise=0.03, vib=0.02,
                      vowels=("i", "a", "u"), fscale=1.35),
    "dog":       dict(f0=300, timbre="saw", shape="woof", dur=0.15, noise=0.22, vib=0.0,
                      vowels=("u", "a"), fscale=1.05),
    "bunny":     dict(f0=1500, timbre="sine", shape="thump_squeak", dur=0.07, noise=0.0, vib=0.0),
    "bear":      dict(f0=110, timbre="saw", shape="growl", dur=0.55, noise=0.25, vib=0.01, am=(28, 0.45),
                      vowels=("uh", "o"), fscale=0.8),
    "panda":     dict(f0=430, timbre="saw", shape="bleat", dur=0.34, noise=0.03, vib=0.06,
                      vowels=("e", "ae"), fscale=1.1),
    "fox":       dict(f0=1050, timbre="saw", shape="gekker", dur=0.065, noise=0.14, vib=0.0,
                      vowels=("i", "a"), fscale=1.45),
    "hamster":   dict(f0=2300, timbre="sine", shape="squeak3", dur=0.045, noise=0.0, vib=0.0),
    "pig":       dict(f0=150, timbre="saw", shape="oink", dur=0.22, noise=0.12, vib=0.0, am=(34, 0.5),
                      vowels=("o", "u"), fscale=1.0),
    "sprout":    dict(f0=700, timbre="sine", shape="boop", dur=0.12, noise=0.0, vib=0.0),
    "lion":      dict(f0=105, timbre="saw", shape="roar", dur=0.95, noise=0.32, vib=0.01, am=(19, 0.3),
                      vowels=("a", "o", "u"), fscale=0.75),
    "tiger":     dict(f0=165, timbre="saw", shape="snarl", dur=0.5, noise=0.35, vib=0.0, am=(36, 0.5),
                      vowels=("uh", "a"), fscale=0.85),
    "unicorn":   dict(f0=520, timbre="saw", shape="neigh", dur=0.75, noise=0.06, vib=0.07,
                      vowels=("i", "e", "a"), fscale=1.15),
    "deer":      dict(f0=880, timbre="saw", shape="mew", dur=0.22, noise=0.04, vib=0.02,
                      vowels=("i", "e"), fscale=1.3),
    "sheep":     dict(f0=290, timbre="saw", shape="baa", dur=0.62, noise=0.05, vib=0.0, am=(8, 0.55),
                      vowels=("ae", "a"), fscale=1.05),
    "cow":       dict(f0=125, timbre="saw", shape="moo", dur=0.95, noise=0.04, vib=0.008,
                      vowels=("u", "o", "uh"), fscale=0.85),
    "raccoon":   dict(f0=1600, timbre="tri", shape="chitter", dur=0.03, noise=0.08, vib=0.0),
    "red_panda": dict(f0=1350, timbre="tri", shape="twitter", dur=0.06, noise=0.05, vib=0.0),
    "monkey":    dict(f0=380, timbre="saw", shape="ooh_aah", dur=0.12, noise=0.05, vib=0.0,
                      vowels=("u", "a"), fscale=1.2),
    "hedgehog":  dict(f0=1700, timbre="sine", shape="snuffle", dur=0.05, noise=0.0, vib=0.0),
    "dino":      dict(f0=78, timbre="saw", shape="stomp_roar", dur=0.9, noise=0.3, vib=0.01, am=(16, 0.35),
                      vowels=("o", "a", "u"), fscale=0.65),
    "squirrel":  dict(f0=1150, timbre="square", shape="chatter", dur=0.035, noise=0.1, vib=0.0),
    "mouse":     dict(f0=2900, timbre="sine", shape="eek", dur=0.07, noise=0.0, vib=0.0),
    "koala":     dict(f0=82, timbre="saw", shape="bellow", dur=0.7, noise=0.4, vib=0.0, am=(30, 0.6),
                      vowels=("o", "uh"), fscale=0.8),
    # water
    "fish":      dict(f0=480, timbre="sine", shape="bubble", dur=0.06, noise=0.0, vib=0.0),
    "whale":     dict(f0=170, timbre="tri", shape="moan", dur=0.95, noise=0.02, vib=0.015),
    "octopus":   dict(f0=300, timbre="sine", shape="blorp", dur=0.18, noise=0.0, vib=0.03),
    "axolotl":   dict(f0=1250, timbre="tri", shape="chirp", dur=0.10, noise=0.0, vib=0.0),
    "penguin":   dict(f0=420, timbre="saw", shape="bray", dur=0.2, noise=0.1, vib=0.0, am=(22, 0.3),
                      vowels=("e", "a"), fscale=1.05),
    "turtle":    dict(f0=150, timbre="tri", shape="turtle_hum", dur=0.32, noise=0.02, vib=0.01),
    "frog":      dict(f0=250, timbre="square", shape="ribbit", dur=0.22, noise=0.04, vib=0.0, am=(42, 0.75)),
    "jellyfish": dict(f0=1180, timbre="bell", shape="chime", dur=0.55, noise=0.0, vib=0.0),
    "shark":     dict(f0=82, timbre="saw", shape="jaws", dur=0.22, noise=0.08, vib=0.0,
                      vowels=("o", "u"), fscale=0.8),
    "dolphin":   dict(f0=2600, timbre="sine", shape="whistle", dur=0.4, noise=0.0, vib=0.0),
    "seal":      dict(f0=250, timbre="saw", shape="arf", dur=0.12, noise=0.2, vib=0.0, am=(33, 0.4),
                      vowels=("o", "u"), fscale=0.95),
    "otter":     dict(f0=1900, timbre="tri", shape="otter_chirp", dur=0.08, noise=0.03, vib=0.04),
    "puffer":    dict(f0=300, timbre="sine", shape="puff", dur=0.4, noise=0.0, vib=0.0),
    "duck":      dict(f0=260, timbre="saw", shape="quack", dur=0.15, noise=0.1, vib=0.0,
                      vowels=("ae", "a"), fscale=1.25),
    "crab":      dict(f0=2400, timbre="sine", shape="clack", dur=0.02, noise=0.0, vib=0.0),
    # sky
    "bird":      dict(f0=2500, timbre="sine", shape="tweet", dur=0.07, noise=0.0, vib=0.0),
    "owl":       dict(f0=410, timbre="sine", shape="hoot", dur=0.26, noise=0.02, vib=0.0),
    "bat":       dict(f0=3600, timbre="sine", shape="echo_click", dur=0.05, noise=0.0, vib=0.0),
    "bee":       dict(f0=230, timbre="saw", shape="buzz", dur=0.42, noise=0.03, vib=0.01, am=(58, 0.4)),
    "butterfly": dict(f0=2000, timbre="bell", shape="sparkle", dur=0.09, noise=0.0, vib=0.0),
    "dragon":    dict(f0=150, timbre="saw", shape="rawr", dur=0.5, noise=0.25, vib=0.02, am=(31, 0.35),
                      vowels=("a", "o"), fscale=0.85),
    "parrot":    dict(f0=900, timbre="saw", shape="squawk_whistle", dur=0.22, noise=0.3, vib=0.0,
                      vowels=("a", "ae"), fscale=1.4),
    "chick":     dict(f0=3200, timbre="sine", shape="peep", dur=0.09, noise=0.0, vib=0.0),
    "ladybug":   dict(f0=720, timbre="saw", shape="flutter", dur=0.22, noise=0.05, vib=0.0, am=(96, 0.6)),
    "phoenix":   dict(f0=1500, timbre="saw", shape="cry", dur=0.55, noise=0.2, vib=0.03,
                      vowels=("ee", "i", "e"), fscale=1.4),
    "pegasus":   dict(f0=480, timbre="saw", shape="wing_neigh", dur=0.7, noise=0.06, vib=0.07,
                      vowels=("i", "e", "a"), fscale=1.1),
    "ghost":     dict(f0=420, timbre="sine", shape="wooo", dur=1.0, noise=0.05, vib=0.0),
    "flamingo":  dict(f0=520, timbre="saw", shape="gabble", dur=0.13, noise=0.1, vib=0.0,
                      vowels=("a", "ae"), fscale=1.15),
}

# Families decide how petting / eating / sleeping sound.
FAMILY = {
    "bird": "bird", "owl": "bird", "parrot": "bird", "chick": "bird", "duck": "bird", "flamingo": "bird",
    "penguin": "bird",
    "fish": "water", "whale": "water", "octopus": "water", "axolotl": "water", "jellyfish": "water",
    "shark": "water", "dolphin": "water", "puffer": "water", "crab": "water", "seal": "water",
    "bee": "bug", "butterfly": "bug", "ladybug": "bug",
    "unicorn": "magic", "pegasus": "magic", "dragon": "magic", "ghost": "magic", "phoenix": "magic",
    "sprout": "magic",
    "frog": "reptile", "turtle": "reptile", "dino": "reptile",
}

# Pitch contour of one call, as multipliers of f0 spread over the note.
SHAPES = {
    "meow":    [0.82, 1.18, 1.05, 0.72],
    "woof":    [1.2, 1.0, 0.78],
    "bark":    [1.25, 1.0, 0.78],
    "squeak":  [1.0, 1.12],
    "thump_squeak": [1.0, 1.15],
    "squeak3": [1.0, 1.15],
    "growl":   [1.0, 1.08, 0.95],
    "bleat":   [1.0, 1.05, 0.98],
    "yip":     [0.85, 1.35],
    "gekker":  [0.9, 1.3, 1.0],
    "oink":    [1.05, 1.0, 0.86],
    "boop":    [1.3, 0.9],
    "bubble":  [0.7, 1.6],
    "moan":    [0.88, 1.28, 1.15, 0.8],
    "blorp":   [1.15, 0.78, 1.05],
    "chirp":   [0.9, 1.3],
    "honk":    [1.0, 1.04, 0.96],
    "hum":     [1.0, 1.02, 0.98],
    "turtle_hum": [1.0, 1.04, 0.96],
    "ribbit":  [1.0, 1.06, 0.92],
    "chime":   [1.0, 1.0],
    "tweet":   [0.8, 1.45],
    "hoot":    [1.04, 1.0],
    "buzz":    [1.0, 1.03, 0.99],
    "sparkle": [1.0, 1.1],
    "rawr":    [0.78, 1.25, 0.95],
    "roar":    [0.8, 1.2, 1.12, 0.7],
    "snarl":   [0.85, 1.3, 0.9],
    "neigh":   [1.35, 1.2, 1.0, 0.8, 0.65],
    "wing_neigh": [1.35, 1.2, 1.0, 0.8, 0.65],
    "mew":     [1.0, 1.25, 0.9],
    "baa":     [1.0, 1.08, 1.0, 0.92],
    "moo":     [0.9, 1.05, 1.1, 0.82],
    "chitter": [1.1, 0.9],
    "twitter": [0.9, 1.3],
    "ooh_aah": [0.95, 1.1],
    "snuffle": [1.0, 1.0],
    "stomp_roar": [0.8, 1.25, 1.1, 0.7],
    "chatter": [1.0, 0.85],
    "eek":     [0.9, 1.25],
    "bellow":  [1.0, 1.1, 0.85],
    "bray":    [1.0, 1.2],
    "jaws":    [1.0, 1.0],
    "whistle": [0.9, 1.5, 1.2],
    "arf":     [1.15, 1.0, 0.85],
    "otter_chirp": [0.9, 1.35, 1.1],
    "puff":    [0.6, 1.5],
    "quack":   [1.12, 1.0, 0.82],
    "clack":   [1.0, 1.0],
    "echo_click": [1.0, 0.55],
    "squawk_whistle": [0.85, 1.25, 1.1],
    "peep":    [1.15, 0.85],
    "flutter": [1.0, 1.02, 0.98],
    "cry":     [1.1, 1.25, 0.95, 0.7],
    "wooo":    [0.8, 1.0, 1.35, 1.05, 0.75],
    "gabble":  [0.95, 1.15, 0.9],
}

# Vowel formants (F1, F2, F3 in Hz) for the voiced calls.
VOWELS = {
    "i": (270, 2290, 3010),
    "ee": (300, 2600, 3300),
    "e": (530, 1840, 2480),
    "ae": (660, 1720, 2410),
    "a": (730, 1090, 2440),
    "o": (570, 840, 2410),
    "u": (300, 870, 2240),
    "uh": (520, 1190, 2390),
}

# How each mood / event colours the voice.
#   pitch: f0 multiplier, speed: >1 = shorter notes, reps: calls in a row,
#   tilt: bends the contour up (+) or down (-), gain, extra noise (harsh),
#   special: a dedicated generator (yawn, snore, giggle, purr, crunch, warble).
EMOTIONS = {
    "happy":    dict(pitch=1.22, speed=1.15, reps=2, tilt=0.22, gain=1.0),
    "content":  dict(pitch=1.08, speed=1.0, reps=1, tilt=0.08, gain=0.9),
    "okay":     dict(pitch=1.0, speed=1.0, reps=1, tilt=0.0, gain=0.8),
    "sad":      dict(pitch=0.86, speed=0.72, reps=1, tilt=-0.25, gain=0.7, vib=0.025),
    "very_sad": dict(pitch=0.76, speed=0.6, reps=2, tilt=-0.32, gain=0.65, vib=0.035),
    "tired":    dict(special="yawn", pitch=0.82, gain=0.7),
    "hungry":   dict(pitch=1.05, speed=1.1, reps=3, tilt=0.15, gain=0.85),
    "grumpy":   dict(pitch=0.78, speed=1.25, reps=1, tilt=-0.2, gain=0.9, noise=0.22),
    "sleeping": dict(special="snore", pitch=0.7, gain=0.45),
    # events
    "tickle":   dict(special="giggle", pitch=1.3, gain=0.95),
    "pet":      dict(special="purr", pitch=1.0, gain=0.8),
    "eat":      dict(special="crunch", pitch=1.1, gain=0.85),
    "dizzy":    dict(special="warble", pitch=1.0, gain=0.85),
    "angry":    dict(pitch=0.72, speed=1.35, reps=2, tilt=-0.15, gain=1.0, noise=0.3),
    "wake":     dict(pitch=1.05, speed=0.9, reps=1, tilt=0.18, gain=0.8),
    "greet":    dict(pitch=1.18, speed=1.1, reps=2, tilt=0.25, gain=0.95),
}

MOODS = ("happy", "content", "okay", "sad", "very_sad", "tired", "hungry", "grumpy", "sleeping")
EVENTS = ("tickle", "pet", "eat", "dizzy", "angry", "wake", "greet")


def family_of(species: str) -> str:
    return FAMILY.get(species, "mammal")


# ---------------------------------------------------------------- synthesis
def _wave(timbre: str, phase: float) -> float:
    if timbre == "sine":
        return math.sin(phase)
    if timbre == "tri":
        return (2 / math.pi) * math.asin(math.sin(phase))
    if timbre == "square":  # soft square: first odd harmonics
        return (math.sin(phase) + math.sin(3 * phase) / 3 + math.sin(5 * phase) / 5) * 0.85
    if timbre == "saw":     # soft saw: first harmonics
        return (math.sin(phase) - math.sin(2 * phase) / 2 + math.sin(3 * phase) / 3
                - math.sin(4 * phase) / 4) * 0.62
    if timbre == "nasal":   # narrow pulse-ish
        return (math.sin(phase) + 0.8 * math.sin(2 * phase) + 0.6 * math.sin(3 * phase)
                + 0.4 * math.sin(4 * phase)) * 0.38
    return math.sin(phase)


def _contour_at(points, x):
    if len(points) == 1:
        return points[0]
    pos = max(0.0, min(1.0, x)) * (len(points) - 1)
    i = min(int(pos), len(points) - 2)
    frac = pos - i
    return points[i] + (points[i + 1] - points[i]) * frac


def _note(rng, f0, dur, timbre, contour, vib=0.0, noise=0.0, am=None, attack=0.012,
          release=0.05, amp=1.0):
    n = max(1, int(dur * SR))
    out = [0.0] * n
    if timbre == "bell":
        partials = ((1.0, 1.0), (2.76, 0.5), (5.4, 0.25), (8.93, 0.12))
        for k in range(n):
            tt = k / SR
            env = math.exp(-tt * 7.0) * min(1.0, k / (0.004 * SR))
            f = f0 * _contour_at(contour, k / n)
            s = sum(a * math.sin(2 * math.pi * f * m * tt) for m, a in partials)
            out[k] = s * 0.5 * env * amp
        return out
    phase = 0.0
    lp = 0.0
    vib_rate = rng.uniform(5.5, 7.5)
    att = max(1, int(attack * SR))
    rel = max(1, int(min(release, dur * 0.6) * SR))
    for k in range(n):
        x = k / n
        f = f0 * _contour_at(contour, x)
        if vib:
            f *= 1.0 + vib * math.sin(2 * math.pi * vib_rate * k / SR)
        phase += 2 * math.pi * f / SR
        s = _wave(timbre, phase)
        if noise:
            lp += 0.35 * (rng.uniform(-1, 1) - lp)
            s = s * (1 - noise) + lp * noise * 2.2
        if am:
            rate, depth = am
            s *= 1 - depth * (0.5 + 0.5 * math.sin(2 * math.pi * rate * k / SR))
        env = 1.0
        if k < att:
            env = k / att
        elif k > n - rel:
            env = max(0.0, (n - k) / rel)
        out[k] = s * env * amp
    return out


def _blep(t, dt):
    """PolyBLEP correction for a band-limited sawtooth."""
    if t < dt:
        t /= dt
        return t + t - t * t - 1.0
    if t > 1.0 - dt:
        t = (t - 1.0) / dt
        return t * t + t + t + 1.0
    return 0.0


def _formants_at(vowels, x, fscale):
    if len(vowels) == 1:
        f = VOWELS.get(vowels[0], VOWELS["a"])
        return tuple(v * fscale for v in f)
    pos = max(0.0, min(1.0, x)) * (len(vowels) - 1)
    i = min(int(pos), len(vowels) - 2)
    frac = pos - i
    a = VOWELS.get(vowels[i], VOWELS["a"])
    b = VOWELS.get(vowels[i + 1], VOWELS["a"])
    return tuple((a[j] + (b[j] - a[j]) * frac) * fscale for j in range(3))


def _bandpass(freq, bandwidth):
    """RBJ band-pass biquad (constant 0 dB peak) -> (b0, b2, a1, a2), b1 = 0."""
    freq = max(60.0, min(freq, SR * 0.45))
    w0 = 2 * math.pi * freq / SR
    q = max(0.5, freq / max(30.0, bandwidth))
    alpha = math.sin(w0) / (2 * q)
    a0 = 1 + alpha
    return alpha / a0, -alpha / a0, -2 * math.cos(w0) / a0, (1 - alpha) / a0


def _voiced(rng, f0, dur, contour, vowels, fscale=1.0, vib=0.0, vib_rate=None, noise=0.0, am=None,
            attack=0.02, release=0.07, amp=1.0, nasal=0.0, sub=0.0):
    """A vocal-ish call: band-limited saw (+ breath) through a gliding 3-formant filter."""
    n = max(1, int(dur * SR))
    out = [0.0] * n
    phase = 0.0
    sub_phase = 0.0
    lp = 0.0
    rate = vib_rate or rng.uniform(5.5, 7.0)
    att = max(1, int(attack * SR))
    rel = max(1, int(min(release, dur * 0.6) * SR))
    gains = (1.0, 0.7, 0.35)
    widths = (90.0, 130.0, 190.0)
    states = [[0.0, 0.0, 0.0, 0.0] for _ in range(3)]
    coeffs = [(0.0, 0.0, 0.0, 0.0)] * 3
    dry = 0.08 + 0.35 * nasal
    for k in range(n):
        x = k / n
        if k % 32 == 0:
            freqs = _formants_at(vowels, x, fscale)
            coeffs = [_bandpass(freqs[j], widths[j] * fscale) for j in range(3)]
        f = f0 * _contour_at(contour, x)
        if vib:
            f *= 1.0 + vib * math.sin(2 * math.pi * rate * k / SR)
        dt = min(0.45, f / SR)
        phase += dt
        if phase >= 1.0:
            phase -= 1.0
        src = 2.0 * phase - 1.0 - _blep(phase, dt)
        if noise:
            lp += 0.5 * (rng.uniform(-1, 1) - lp)
            src = src * (1 - noise) + lp * noise * 2.5
        y = 0.0
        for j in range(3):
            b0, b2, a1, a2 = coeffs[j]
            st = states[j]
            yj = b0 * src + b2 * st[1] - a1 * st[2] - a2 * st[3]
            st[1] = st[0]
            st[0] = src
            st[3] = st[2]
            st[2] = yj
            y += yj * gains[j]
        s = y * 2.2 + src * dry
        if sub:
            sub_phase += 2 * math.pi * f * 0.5 / SR
            s += sub * math.sin(sub_phase)
        if am:
            am_rate, depth = am
            s *= 1 - depth * (0.5 + 0.5 * math.sin(2 * math.pi * am_rate * k / SR))
        env = 1.0
        if k < att:
            env = k / att
        elif k > n - rel:
            env = max(0.0, (n - k) / rel)
        out[k] = s * env * amp
    return out


def _silence(seconds):
    return [0.0] * int(max(0.0, seconds) * SR)


def _noise_burst(rng, dur, cutoff=0.25, amp=1.0, decay=30.0, highpass=False):
    n = int(dur * SR)
    lp = 0.0
    prev = 0.0
    out = []
    for k in range(n):
        lp += cutoff * (rng.uniform(-1, 1) - lp)
        s = lp
        if highpass:
            s, prev = lp - prev, lp
        out.append(s * amp * math.exp(-decay * k / SR) * 2.5)
    return out


def _glide(rng, f_start, f_end, dur, amp=1.0, attack=0.005, release=0.03, timbre="sine"):
    """A pure tone sliding (exponentially) from f_start to f_end."""
    n = max(1, int(dur * SR))
    out = []
    phase = 0.0
    att = max(1, int(attack * SR))
    rel = max(1, int(min(release, dur * 0.6) * SR))
    ratio = f_end / max(1.0, f_start)
    for k in range(n):
        f = f_start * ratio ** (k / n)
        phase += 2 * math.pi * f / SR
        env = min(1.0, k / att, (n - k) / rel)
        out.append(_wave(timbre, phase) * env * amp)
    return out


def _mix(base, layer, offset_s=0.0, gain=1.0):
    """Mix `layer` into `base` (in place, growing it) starting at offset_s."""
    start = int(offset_s * SR)
    need = start + len(layer)
    if need > len(base):
        base.extend([0.0] * (need - len(base)))
    for i, s in enumerate(layer):
        base[start + i] += s * gain
    return base


def _echo(samples, delay_s=0.16, gain=0.35, repeats=2):
    out = list(samples)
    for r in range(1, repeats + 1):
        _mix(out, samples, delay_s * r, gain ** r)
    return out


def _tilted(contour, tilt):
    if not tilt:
        return list(contour)
    last = len(contour) - 1 or 1
    return [c * (1 + tilt * (i / last)) for i, c in enumerate(contour)]


def _vox(rng, voice, f0, dur, contour, vib, noise, **extra):
    """Voiced call when the voice has vowels, plain oscillator otherwise."""
    vowels = voice.get("vowels")
    if vowels:
        return _voiced(rng, f0, dur, contour, vowels, voice.get("fscale", 1.0), vib=vib, noise=noise,
                       am=extra.pop("am", voice.get("am")), **extra)
    extra.pop("nasal", None)
    extra.pop("sub", None)
    extra.pop("vib_rate", None)
    return _note(rng, f0, dur, voice["timbre"], contour, vib, noise, extra.pop("am", voice.get("am")),
                 **extra)


def _call(rng, voice, emo, pitch_jitter=0.04):
    """One call of the animal coloured by the emotion."""
    f0 = voice["f0"] * emo.get("pitch", 1.0) * (1 + rng.uniform(-pitch_jitter, pitch_jitter))
    dur = voice["dur"] / emo.get("speed", 1.0)
    shape = voice["shape"]
    contour = _tilted(SHAPES.get(shape, [1.0]), emo.get("tilt", 0.0))
    vib = max(voice.get("vib", 0.0), emo.get("vib", 0.0))
    noise = min(0.6, voice.get("noise", 0.0) + emo.get("noise", 0.0))
    maker = _CALLS.get(shape)
    if maker is not None:
        return maker(rng, voice, f0, dur, contour, vib, noise)
    release = 0.12 if shape in ("moan", "hum", "growl", "buzz") else 0.05
    attack = 0.04 if shape in ("moan", "hum") else 0.012
    return _vox(rng, voice, f0, dur, contour, vib, noise, attack=attack, release=release)


# ---- calls with their own rhythm / layers ------------------------------------
def _c_ribbit(rng, voice, f0, dur, contour, vib, noise):       # two quick pulses
    half = _note(rng, f0, dur * 0.45, voice["timbre"], contour, vib, noise, voice.get("am"))
    return half + _silence(0.03) + _note(rng, f0 * 0.94, dur * 0.5, voice["timbre"], contour, vib,
                                          noise, voice.get("am"))


def _c_hoot(rng, voice, f0, dur, contour, vib, noise):         # hoo-hoo
    a = _note(rng, f0, dur * 0.5, voice["timbre"], contour, vib, noise, attack=0.03, release=0.08)
    b = _note(rng, f0 * 0.86, dur * 0.7, voice["timbre"], contour, vib, noise, attack=0.03, release=0.12)
    return a + _silence(0.07) + b


def _c_tweet(rng, voice, f0, dur, contour, vib, noise):        # trill of three chirps
    seq = []
    for i in range(3):
        seq += _note(rng, f0 * (1 + 0.08 * i), dur, "sine", contour, 0, 0, attack=0.004, release=0.02)
        seq += _silence(0.025)
    return seq


def _c_bubble(rng, voice, f0, dur, contour, vib, noise):       # blub-blub
    seq = []
    for _ in range(3):
        seq += _note(rng, f0 * rng.uniform(0.85, 1.25), dur, "sine", contour, 0, 0, attack=0.003,
                     release=0.03, amp=0.9)
        seq += _silence(rng.uniform(0.03, 0.07))
    return seq


def _c_sparkle(rng, voice, f0, dur, contour, vib, noise):      # twinkles
    seq = []
    for _ in range(4):
        seq += _note(rng, f0 * (1.0 + 0.12 * rng.randint(0, 4)), dur, "bell", contour)[: int(dur * SR)]
        seq += _silence(0.02)
    return seq


def _c_chime(rng, voice, f0, dur, contour, vib, noise):
    return _note(rng, f0, dur, "bell", contour)


def _c_moan(rng, voice, f0, dur, contour, vib, noise):         # whale song with an underwater echo
    song = _note(rng, f0, dur, voice["timbre"], contour, vib, noise, attack=0.06, release=0.15)
    return _echo(song, 0.22, 0.3, 2)


def _c_meow(rng, voice, f0, dur, contour, vib, noise):         # m-ee-ow
    return _voiced(rng, f0, dur, contour, voice["vowels"], voice["fscale"], vib=vib, noise=noise,
                   attack=0.03, release=0.12)


def _c_woof(rng, voice, f0, dur, contour, vib, noise):         # breathy onset + "wuf"
    puff = _noise_burst(rng, 0.03, cutoff=0.5, amp=0.5, decay=80)
    body = _voiced(rng, f0, dur, contour, voice["vowels"], voice["fscale"], noise=noise, attack=0.004,
                   release=0.05, nasal=0.2)
    return _mix(puff, body, 0.01)


def _c_thump_squeak(rng, voice, f0, dur, contour, vib, noise):  # hind-leg thump, then a squeak
    thump = _glide(rng, 110, 55, 0.09, amp=0.9, attack=0.002, release=0.06)
    squeak = _note(rng, f0, dur, "sine", contour, 0, 0, attack=0.004, release=0.03, amp=0.8)
    return thump + _silence(0.06) + squeak + _silence(0.03) + _note(rng, f0 * 1.1, dur * 0.8, "sine",
                                                                       contour, 0, 0, amp=0.6)


def _c_squeak3(rng, voice, f0, dur, contour, vib, noise):      # squeak-squeak-squeak + teeth chatter
    seq = []
    for i in range(3):
        seq += _note(rng, f0 * (1 + 0.06 * i), dur, "sine", contour, 0, 0, attack=0.003, release=0.02)
        seq += _silence(0.03)
    for _ in range(3):
        seq += _noise_burst(rng, 0.012, cutoff=0.9, amp=0.35, decay=300, highpass=True) + _silence(0.02)
    return seq


def _c_squeak(rng, voice, f0, dur, contour, vib, noise):
    return _note(rng, f0, dur, voice["timbre"], contour, 0, 0, attack=0.004, release=0.03)


def _c_gekker(rng, voice, f0, dur, contour, vib, noise):       # fox "ack-ack-ack"
    seq = []
    for i in range(4):
        seq += _voiced(rng, f0 * (1 + 0.05 * rng.uniform(-1, 1)), dur, contour, voice["vowels"],
                       voice["fscale"], noise=noise, attack=0.004, release=0.03, nasal=0.3)
        seq += _silence(0.04)
    return seq


def _c_roar(rng, voice, f0, dur, contour, vib, noise):         # big breathy roar + grunts
    roar = _voiced(rng, f0, dur, contour, voice["vowels"], voice["fscale"], vib=vib, noise=noise,
                   am=voice.get("am"), attack=0.12, release=0.3, sub=0.25)
    for i in range(2):
        roar += _silence(0.08)
        roar += _voiced(rng, f0 * 0.9, 0.16, [1.0, 0.85], ("uh", "u"), voice["fscale"], noise=noise,
                        attack=0.01, release=0.08, amp=0.55, sub=0.2)
    return roar


def _c_snarl(rng, voice, f0, dur, contour, vib, noise):        # growly rrr then a sharp AWR
    growl = _voiced(rng, f0 * 0.7, dur * 0.45, [1.0, 1.05], ("uh",), voice["fscale"], noise=noise + 0.1,
                    am=(42, 0.7), attack=0.05, release=0.04, amp=0.6)
    awr = _voiced(rng, f0, dur, contour, voice["vowels"], voice["fscale"], noise=noise, am=voice.get("am"),
                  attack=0.02, release=0.15, sub=0.15)
    return growl + awr


def _c_neigh(rng, voice, f0, dur, contour, vib, noise, wings=False):
    """Whinny: fast vibrato sliding down; unicorns end in a sparkle, pegasus starts with wing flaps."""
    seq = []
    if wings:
        for _ in range(2):
            seq += _noise_burst(rng, 0.11, cutoff=0.08, amp=0.6, decay=14) + _silence(0.04)
    whinny = _voiced(rng, f0, dur, contour, voice["vowels"], voice["fscale"], vib=max(vib, 0.06),
                     vib_rate=11.0, noise=noise, attack=0.03, release=0.2, nasal=0.25)
    seq += whinny
    seq += _voiced(rng, f0 * 0.55, 0.14, [1.0, 0.9], ("o", "u"), voice["fscale"], noise=0.4, attack=0.01,
                   release=0.08, amp=0.4)                          # little snort
    if not wings:
        for i in range(3):
            _mix(seq, _note(rng, 1800 * (1 + 0.25 * i), 0.25, "bell", [1.0, 1.0], amp=0.35),
                 dur * 0.6 + 0.07 * i)
    return seq


def _c_wing_neigh(rng, voice, f0, dur, contour, vib, noise):
    return _c_neigh(rng, voice, f0, dur, contour, vib, noise, wings=True)


def _c_mew(rng, voice, f0, dur, contour, vib, noise):          # fawn bleat, twice
    a = _voiced(rng, f0, dur, contour, voice["vowels"], voice["fscale"], vib=vib, noise=noise, attack=0.01,
                release=0.06, nasal=0.4)
    return a + _silence(0.07) + _voiced(rng, f0 * 1.06, dur * 0.8, contour, voice["vowels"],
                                        voice["fscale"], vib=vib, noise=noise, nasal=0.4, amp=0.8)


def _c_baa(rng, voice, f0, dur, contour, vib, noise):          # wobbly baa-aa-aa
    return _voiced(rng, f0, dur, contour, voice["vowels"], voice["fscale"], vib=0.03, vib_rate=8.0,
                   noise=noise, am=voice.get("am"), attack=0.03, release=0.18, nasal=0.3)


def _c_moo(rng, voice, f0, dur, contour, vib, noise):          # long mmm-OOO
    hum = _voiced(rng, f0 * 0.95, 0.18, [1.0, 1.02], ("u",), voice["fscale"] * 0.8, attack=0.05,
                  release=0.02, amp=0.6)
    moo = _voiced(rng, f0, dur, contour, voice["vowels"], voice["fscale"], vib=vib, noise=noise,
                  attack=0.04, release=0.3, nasal=0.35, sub=0.15)
    return hum + moo


def _c_chitter(rng, voice, f0, dur, contour, vib, noise):      # fast falling chitter-chitter
    seq = []
    for i in range(8):
        seq += _note(rng, f0 * (1.15 - 0.05 * i) * rng.uniform(0.92, 1.08), dur, voice["timbre"], contour,
                     0, noise, attack=0.002, release=0.012)
        seq += _silence(0.018)
    return seq


def _c_twitter(rng, voice, f0, dur, contour, vib, noise):      # huff + squealy twitter
    seq = _noise_burst(rng, 0.09, cutoff=0.15, amp=0.6, decay=25) + _silence(0.05)
    for i in range(4):
        seq += _note(rng, f0 * (1 + 0.1 * i), dur, voice["timbre"], contour, 0.03, noise, attack=0.004,
                     release=0.025)
        seq += _silence(0.035)
    return seq


def _c_ooh_aah(rng, voice, f0, dur, contour, vib, noise):      # ooh-ooh-aah-aah!
    seq = []
    for i, vowel in enumerate(("u", "u", "a", "a")):
        seq += _voiced(rng, f0 * (1 + 0.12 * i), dur * (1.0 if vowel == "u" else 1.3), contour, (vowel,),
                       voice["fscale"], noise=noise, attack=0.01, release=0.04)
        seq += _silence(0.045)
    return seq


def _c_snuffle(rng, voice, f0, dur, contour, vib, noise):      # sniff-sniff-sniff + tiny squeak
    seq = []
    for _ in range(5):
        seq += _noise_burst(rng, 0.045, cutoff=0.7, amp=0.45, decay=60, highpass=True)
        seq += _silence(rng.uniform(0.03, 0.06))
    return seq + _note(rng, f0, dur, "sine", contour, 0, 0, attack=0.004, release=0.03, amp=0.6)


def _c_stomp_roar(rng, voice, f0, dur, contour, vib, noise):   # stomp, stomp, ROAAR
    seq = []
    for _ in range(2):
        seq += _glide(rng, 70, 38, 0.13, amp=1.0, attack=0.002, release=0.09)
        seq += _noise_burst(rng, 0.05, cutoff=0.1, amp=0.4, decay=40)
        seq += _silence(0.1)
    seq += _voiced(rng, f0, dur, contour, voice["vowels"], voice["fscale"], vib=vib, noise=noise,
                   am=voice.get("am"), attack=0.1, release=0.3, sub=0.4)
    return seq


def _c_chatter(rng, voice, f0, dur, contour, vib, noise):      # chuk-chuk-chuk + trill
    seq = []
    for _ in range(5):
        seq += _note(rng, f0 * rng.uniform(0.95, 1.05), dur, voice["timbre"], contour, 0, noise,
                     attack=0.002, release=0.02)
        seq += _silence(0.045)
    seq += _note(rng, f0 * 1.4, 0.16, "tri", [1.0, 1.0], 0.0, 0.05, am=(45, 0.8), attack=0.01,
                 release=0.05, amp=0.7)
    return seq


def _c_eek(rng, voice, f0, dur, contour, vib, noise):          # eek! eek!
    a = _note(rng, f0, dur, "sine", contour, 0.02, 0, attack=0.003, release=0.03)
    return a + _silence(0.05) + _note(rng, f0 * 1.08, dur * 0.85, "sine", contour, 0.02, 0, attack=0.003,
                                      release=0.03, amp=0.85)


def _c_bellow(rng, voice, f0, dur, contour, vib, noise):       # koala: snory inhale + belchy bellow
    inhale = _voiced(rng, f0 * 1.3, dur * 0.5, [0.95, 1.05], ("o",), voice["fscale"], noise=0.6,
                     am=(26, 0.75), attack=0.08, release=0.05, amp=0.55)
    bellow = _voiced(rng, f0, dur, contour, voice["vowels"], voice["fscale"], noise=noise, am=voice.get("am"),
                     attack=0.03, release=0.2, sub=0.35)
    return inhale + _silence(0.04) + bellow


def _c_bray(rng, voice, f0, dur, contour, vib, noise):         # penguin: eh-AAH eh-AAH-aaah
    seq = []
    for i in range(3):
        seq += _voiced(rng, f0 * 0.85, dur * 0.6, [1.0, 1.05], ("e",), voice["fscale"], noise=noise,
                       attack=0.01, release=0.03, nasal=0.4, amp=0.7)
        seq += _voiced(rng, f0 * 1.15, dur * (1.0 if i < 2 else 2.0), contour, ("a",), voice["fscale"],
                       noise=noise, am=voice.get("am"), attack=0.01, release=0.06, nasal=0.4)
        seq += _silence(0.05)
    return seq


def _c_jaws(rng, voice, f0, dur, contour, vib, noise):         # dun-dun... dun-dun + chomp
    seq = []
    for i in range(4):
        pitch = f0 if i % 2 == 0 else f0 * 1.06
        seq += _voiced(rng, pitch, dur * (1.0 if i < 2 else 0.7), [1.0, 1.0], voice["vowels"],
                       voice["fscale"], noise=noise, attack=0.01, release=0.08, sub=0.5)
        seq += _silence(0.14 if i < 2 else 0.06)
    seq += _noise_burst(rng, 0.05, cutoff=0.9, amp=0.9, decay=90, highpass=True)
    return seq


def _c_whistle(rng, voice, f0, dur, contour, vib, noise):      # click train + whistle
    seq = []
    for _ in range(6):
        seq += _noise_burst(rng, 0.006, cutoff=0.95, amp=0.8, decay=600, highpass=True)
        seq += _silence(rng.uniform(0.018, 0.03))
    seq += _note(rng, f0, dur, "sine", contour, 0.01, 0, attack=0.02, release=0.08)
    return seq


def _c_arf(rng, voice, f0, dur, contour, vib, noise):          # gravelly ORF-orf ... + a clap of flippers
    seq = []
    for i in range(2):
        seq += _voiced(rng, f0 * (1 - 0.06 * i), dur * (1.3 if i == 0 else 1.0), contour, voice["vowels"],
                       voice["fscale"], noise=noise, am=voice.get("am"), attack=0.006, release=0.05, nasal=0.2,
                       sub=0.3)
        seq += _silence(0.12)
    for _ in range(2):
        seq += _noise_burst(rng, 0.03, cutoff=0.5, amp=0.6, decay=120) + _silence(0.07)
    return seq


def _c_otter_chirp(rng, voice, f0, dur, contour, vib, noise):  # bright squeaky chirps
    seq = []
    for i in range(3):
        seq += _note(rng, f0 * (1 + 0.05 * i), dur, voice["timbre"], contour, vib, noise, attack=0.004,
                     release=0.03)
        seq += _silence(0.04)
    return seq


def _c_puff(rng, voice, f0, dur, contour, vib, noise):         # inflating whoosh ... pop!
    whoosh = _noise_burst(rng, dur, cutoff=0.06, amp=0.5, decay=0.5)
    _mix(whoosh, _glide(rng, f0 * 0.6, f0 * 2.2, dur, amp=0.5, attack=0.05, release=0.02))
    return whoosh + _noise_burst(rng, 0.03, cutoff=0.9, amp=1.0, decay=120, highpass=True) + \
        _note(rng, 900, 0.06, "sine", [1.0, 0.6], amp=0.5)


def _c_quack(rng, voice, f0, dur, contour, vib, noise):        # QUACK-quack-quack (getting softer)
    seq = []
    for i in range(3):
        seq += _voiced(rng, f0 * (1 - 0.06 * i), dur * (1.0 - 0.12 * i), contour, voice["vowels"],
                       voice["fscale"], noise=noise, attack=0.003, release=0.04, nasal=0.85, amp=1.0 - 0.22 * i)
        seq += _silence(0.06)
    return seq


def _c_clack(rng, voice, f0, dur, contour, vib, noise):        # claw clicks + a tiny bubble
    seq = []
    for _ in range(4):
        seq += _noise_burst(rng, dur, cutoff=0.95, amp=0.9, decay=350, highpass=True)
        seq += _note(rng, f0 * rng.uniform(0.9, 1.1), 0.012, "sine", [1.0, 1.0], amp=0.4)
        seq += _silence(rng.uniform(0.05, 0.09))
    return seq + _note(rng, 520, 0.05, "sine", [0.7, 1.6], amp=0.5)


def _c_echo_click(rng, voice, f0, dur, contour, vib, noise):   # bat: ticks + downward chirps
    seq = []
    for _ in range(4):
        seq += _noise_burst(rng, 0.004, cutoff=0.95, amp=0.6, decay=800, highpass=True) + _silence(0.03)
    for i in range(2):
        seq += _glide(rng, f0, f0 * 0.55, dur, amp=0.8, attack=0.002, release=0.015)
        seq += _silence(0.05)
    return _echo(seq, 0.07, 0.3, 2)


def _c_squawk_whistle(rng, voice, f0, dur, contour, vib, noise):  # SQUAWK + wolf whistle
    squawk = _voiced(rng, f0, dur, contour, voice["vowels"], voice["fscale"], noise=noise, attack=0.01,
                     release=0.05, nasal=0.6)
    whistle = _glide(rng, 1200, 2600, 0.16, amp=0.7) + _silence(0.05) + \
        _glide(rng, 1300, 2900, 0.1, amp=0.7) + _glide(rng, 2900, 1100, 0.22, amp=0.7)
    return squawk + _silence(0.12) + whistle


def _c_peep(rng, voice, f0, dur, contour, vib, noise):         # peep peep peep (falling)
    seq = []
    for _ in range(3):
        seq += _glide(rng, f0 * rng.uniform(1.0, 1.08), f0 * 0.78, dur, amp=0.85, attack=0.004, release=0.02)
        seq += _silence(0.06)
    return seq


def _c_flutter(rng, voice, f0, dur, contour, vib, noise):      # wing whirr + little ticks
    whirr = _note(rng, f0, dur, voice["timbre"], contour, 0, noise, voice.get("am"), attack=0.03, release=0.08,
                  amp=0.7)
    for i in range(3):
        _mix(whirr, _note(rng, 3000 + 400 * i, 0.03, "bell", [1.0, 1.0], amp=0.4), 0.04 + 0.08 * i)
    return whirr


def _c_cry(rng, voice, f0, dur, contour, vib, noise):          # piercing cry + crackling fire
    cry = _voiced(rng, f0, dur, contour, voice["vowels"], voice["fscale"], vib=vib, noise=noise, attack=0.02,
                  release=0.18, nasal=0.4)
    for _ in range(9):
        _mix(cry, _noise_burst(rng, 0.008, cutoff=0.9, amp=0.5, decay=500, highpass=True),
             rng.uniform(0.0, dur + 0.1))
    return cry


def _c_wooo(rng, voice, f0, dur, contour, vib, noise):         # wooOOooo with a spooky echo
    n = int(dur * SR)
    out = []
    phase = 0.0
    lp = 0.0
    for k in range(n):
        x = k / n
        f = f0 * _contour_at(contour, x) * (1 + 0.06 * math.sin(2 * math.pi * 4.0 * k / SR))
        phase += 2 * math.pi * f / SR
        lp += 0.05 * (rng.uniform(-1, 1) - lp)
        env = math.sin(math.pi * x) ** 1.2
        out.append((math.sin(phase) * 0.85 + math.sin(phase * 2) * 0.12 + lp * 1.5 * noise) * env)
    return _echo(out, 0.2, 0.35, 2)


def _c_gabble(rng, voice, f0, dur, contour, vib, noise):       # honk-honk-honk (goose-like)
    seq = []
    for i in range(3):
        seq += _voiced(rng, f0 * (1 + 0.07 * i), dur, contour, voice["vowels"], voice["fscale"], noise=noise,
                       attack=0.008, release=0.04, nasal=0.65)
        seq += _silence(0.05)
    return seq


def _c_rawr(rng, voice, f0, dur, contour, vib, noise):         # big wing flap, RAWR, crackling fire
    seq = _noise_burst(rng, 0.14, cutoff=0.06, amp=0.7, decay=12) + _silence(0.05)
    seq += _voiced(rng, f0, dur, contour, voice["vowels"], voice["fscale"], vib=vib, noise=noise,
                   am=voice.get("am"), attack=0.03, release=0.12, sub=0.35)
    fire = _noise_burst(rng, 0.45, cutoff=0.12, amp=0.55, decay=5)
    for _ in range(6):
        _mix(fire, _noise_burst(rng, 0.008, cutoff=0.9, amp=0.5, decay=500, highpass=True), rng.uniform(0, 0.4))
    return seq + fire


def _c_turtle_hum(rng, voice, f0, dur, contour, vib, noise):   # slow "hmm..." then a hiss and a beak click
    hum = _note(rng, f0, dur, voice["timbre"], contour, vib, noise, attack=0.06, release=0.12)
    hiss = _noise_burst(rng, 0.16, cutoff=0.8, amp=0.4, decay=10, highpass=True)
    click = _noise_burst(rng, 0.01, cutoff=0.95, amp=0.7, decay=500, highpass=True)
    return hum + _silence(0.12) + hiss + _silence(0.05) + click


def _c_growl(rng, voice, f0, dur, contour, vib, noise):
    return _vox(rng, voice, f0, dur, contour, vib, noise, attack=0.04, release=0.15, sub=0.25)


_CALLS = {
    "ribbit": _c_ribbit, "hoot": _c_hoot, "tweet": _c_tweet, "bubble": _c_bubble, "sparkle": _c_sparkle,
    "chime": _c_chime, "moan": _c_moan, "meow": _c_meow, "woof": _c_woof, "thump_squeak": _c_thump_squeak,
    "squeak3": _c_squeak3, "squeak": _c_squeak, "gekker": _c_gekker, "roar": _c_roar, "snarl": _c_snarl,
    "neigh": _c_neigh, "wing_neigh": _c_wing_neigh, "mew": _c_mew, "baa": _c_baa, "moo": _c_moo,
    "chitter": _c_chitter, "twitter": _c_twitter, "ooh_aah": _c_ooh_aah, "snuffle": _c_snuffle,
    "stomp_roar": _c_stomp_roar, "chatter": _c_chatter, "eek": _c_eek, "bellow": _c_bellow, "bray": _c_bray,
    "jaws": _c_jaws, "whistle": _c_whistle, "arf": _c_arf, "otter_chirp": _c_otter_chirp, "puff": _c_puff,
    "quack": _c_quack, "clack": _c_clack, "echo_click": _c_echo_click, "squawk_whistle": _c_squawk_whistle,
    "peep": _c_peep, "flutter": _c_flutter, "cry": _c_cry, "wooo": _c_wooo, "gabble": _c_gabble,
    "rawr": _c_rawr, "growl": _c_growl, "turtle_hum": _c_turtle_hum,
}


# ---- mood / event specials, per family -----------------------------------------
def _bubbles(rng, count, low=380, high=900, amp=0.7):
    seq = []
    for _ in range(count):
        f = rng.uniform(low, high)
        seq += _note(rng, f, rng.uniform(0.04, 0.07), "sine", [0.7, 1.6], attack=0.003, release=0.03, amp=amp)
        seq += _silence(rng.uniform(0.04, 0.12))
    return seq


def _special(rng, voice, emo, family="mammal"):
    kind = emo["special"]
    f0 = voice["f0"] * emo.get("pitch", 1.0)
    timbre = voice["timbre"] if voice["timbre"] != "bell" else "sine"
    if kind == "giggle":        # quick rising hee-hee-hee
        seq = []
        steps = 4 if voice["dur"] > 0.08 else 5
        for i in range(steps):
            seq += _note(rng, f0 * (1 + 0.1 * i), min(0.09, voice["dur"] * 0.6 + 0.03), timbre,
                         [1.0, 1.15], 0, voice.get("noise", 0) * 0.5, attack=0.004, release=0.03)
            seq += _silence(0.035)
        return seq
    if kind == "purr":          # being petted
        content = _call(rng, voice, EMOTIONS["content"])
        if family == "bird":    # soft happy trill (chirr-r-r) + call
            trill = _note(rng, min(3000.0, max(900.0, f0 * 1.2)), 0.45, "tri", [1.0, 1.05, 0.98], 0, 0.02,
                          (32, 0.85), attack=0.03, release=0.1, amp=0.6)
            return trill + _silence(0.05) + content
        if family == "water":   # contented blub-blub
            return _bubbles(rng, 4) + content
        if family == "bug":     # happy shimmering hum
            hum = _note(rng, 260, 0.5, "saw", [1.0, 1.03, 1.0], 0, 0.02, (64, 0.35), attack=0.06,
                        release=0.15, amp=0.5)
            return hum + content
        if family == "magic":   # sparkly chime arpeggio
            seq = []
            for i, ratio in enumerate((1.0, 1.25, 1.5, 2.0)):
                seq += _note(rng, 1046 * ratio, 0.12, "bell", [1.0, 1.0], amp=0.55)[: int(0.1 * SR)]
            return seq + _silence(0.04) + content
        base = max(70.0, min(f0 * 0.25, 160.0))          # mammals & co: purr rumble
        rumble = _note(rng, base, 0.75, "saw", [1.0, 1.02, 0.98], 0, 0.35, (24, 0.8),
                       attack=0.08, release=0.2, amp=0.7)
        return rumble + content
    if kind == "crunch":        # eating
        happy = _call(rng, voice, EMOTIONS["happy"])
        seq = []
        if family == "bird":    # peck-peck-peck
            for _ in range(4):
                seq += _noise_burst(rng, 0.018, cutoff=0.8, amp=0.7, decay=220, highpass=True)
                seq += _note(rng, 1700, 0.012, "sine", [1.0, 1.0], amp=0.35)
                seq += _silence(0.07)
        elif family == "water":  # gulp! gulp!
            for _ in range(2):
                seq += _glide(rng, 520, 170, 0.12, amp=0.8, attack=0.005, release=0.04)
                seq += _bubbles(rng, 1, 600, 900, amp=0.5)
        elif family == "bug":    # tiny nibbles
            for _ in range(6):
                seq += _noise_burst(rng, 0.01, cutoff=0.9, amp=0.45, decay=400, highpass=True)
                seq += _silence(0.035)
        else:                    # crunch-crunch (magic pets crunch with a sparkle)
            for _ in range(3):
                seq += _noise_burst(rng, 0.07, cutoff=0.6, amp=0.8, decay=45)
                seq += _silence(0.06)
            if family == "magic":
                _mix(seq, _note(rng, 2093, 0.3, "bell", [1.0, 1.0], amp=0.4), 0.1)
        return seq + happy
    if kind == "warble":        # dizzy wobble
        if voice.get("vowels"):
            return _voiced(rng, f0, 0.7, [1.0, 1.3, 0.8, 1.25, 0.75, 1.1], voice["vowels"], voice["fscale"],
                           vib=0.09, noise=voice.get("noise", 0), attack=0.02, release=0.15)
        return _note(rng, f0, 0.7, timbre, [1.0, 1.3, 0.8, 1.25, 0.75, 1.1], 0.09,
                     voice.get("noise", 0), voice.get("am"), attack=0.02, release=0.15)
    if kind == "yawn":          # long, low, breathy glide down
        dur = max(0.7, voice["dur"] * 2.2)
        if voice.get("vowels"):
            return _voiced(rng, f0, dur, [1.0, 1.25, 1.1, 0.62], ("a", "o", "u"), voice["fscale"], vib=0.01,
                           noise=min(0.6, voice.get("noise", 0) + 0.3), attack=0.08, release=0.25)
        return _note(rng, f0, dur, timbre, [1.0, 1.25, 1.1, 0.62], 0.01,
                     min(0.6, voice.get("noise", 0) + 0.3), attack=0.08, release=0.25)
    if kind == "snore":         # sleeping
        if family == "water":   # slow bubbles drifting up
            return _bubbles(rng, 3, 300, 600, amp=0.5) + _silence(0.2) + _bubbles(rng, 2, 300, 500, amp=0.4)
        if family == "bird":    # sleepy little coos
            seq = []
            for _ in range(2):
                seq += _note(rng, max(300.0, min(f0 * 0.5, 900.0)), 0.3, "sine", [1.0, 0.9], 0, 0.05,
                             attack=0.08, release=0.15, amp=0.5)
                seq += _silence(0.35)
            return seq
        if family == "bug":     # faint drowsy hum
            return _note(rng, 190, 0.9, "saw", [1.0, 0.97, 0.92], 0, 0.05, (40, 0.5), attack=0.25,
                         release=0.4, amp=0.35)
        if family == "magic":   # soft dreamy chimes
            seq = []
            for ratio in (1.0, 0.75, 0.5):
                seq += _note(rng, 880 * ratio, 0.4, "bell", [1.0, 1.0], amp=0.4)[: int(0.35 * SR)]
            return seq
        seq = []                # two soft breaths, in and out
        for _ in range(2 if voice["dur"] < 0.3 else 1):
            inhale = _note(rng, max(60.0, min(f0 * 0.3, 140.0)), 0.55, "saw", [0.95, 1.05], 0, 0.7,
                           (18, 0.6), attack=0.2, release=0.25, amp=0.6)
            exhale = _noise_burst(rng, 0.5, cutoff=0.08, amp=0.35, decay=3)
            seq += inhale + exhale + _silence(0.15)
        return seq
    return _call(rng, voice, EMOTIONS["okay"])


def synthesize(species: str, kind: str, variant: int = 0) -> list:
    voice = VOICES.get(species) or VOICES["cat"]
    emo = EMOTIONS.get(kind) or EMOTIONS["okay"]
    seed = int(hashlib.md5(f"{species}:{kind}:{variant}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    if emo.get("special"):
        samples = _special(rng, voice, emo, family_of(species))
    else:
        samples = []
        for _ in range(emo.get("reps", 1)):
            if samples and len(samples) / SR > 1.1:   # keep long voices (whale, lion ...) short enough
                break
            samples += _call(rng, voice, emo)
            samples += _silence(rng.uniform(0.05, 0.11) / emo.get("speed", 1.0))
    samples = samples[: int(MAX_CLIP_S * SR)]
    fade = min(len(samples), int(0.02 * SR))
    for i in range(fade):                              # never end on a click
        samples[-1 - i] *= i / fade
    peak = max((abs(s) for s in samples), default=0.0) or 1.0
    gain = emo.get("gain", 1.0) / peak
    return [s * gain for s in samples] + _silence(0.03)


def _write_wav(path: Path, samples, volume: float):
    frames = bytearray()
    level = 0.75 * max(0.0, min(1.0, volume))
    for s in samples:
        frames += struct.pack("<h", int(max(-1.0, min(1.0, s * level)) * 32767))
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(frames))
    tmp.replace(path)


def _drop_old_cache():
    """Clips from older versions of the synth live loose in CACHE_ROOT: tidy them away."""
    try:
        for old in CACHE_ROOT.glob("*.wav"):
            old.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- playback
class PetVoice:
    """Plays pet sounds without blocking the UI and without stopping the soundscape."""

    def __init__(self):
        self._lock = threading.Lock()
        self._playing = 0
        self._last = 0.0
        self._counter = 0
        try:
            self._mci = ctypes.windll.winmm.mciSendStringW
        except Exception:  # not Windows
            self._mci = None
        threading.Thread(target=_drop_old_cache, daemon=True).start()

    @property
    def available(self) -> bool:
        return self._mci is not None

    def clip_path(self, species: str, kind: str, volume: float, variant: int | None = None) -> Path:
        if variant is None:
            variant = random.randrange(VARIANTS)
        vol_step = int(round(max(0.0, min(1.0, volume)) * 10))
        path = CACHE_DIR / f"{species}_{kind}_{variant}_v{vol_step}.wav"
        if not path.exists():
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _write_wav(path, synthesize(species, kind, variant), vol_step / 10)
        return path

    def play(self, species: str, kind: str, volume: float = 0.6) -> bool:
        if not self.available or volume <= 0:
            return False
        now = time.monotonic()
        with self._lock:
            if now - self._last < MIN_GAP_S or self._playing >= MAX_PLAYING:
                return False
            self._last = now
            self._playing += 1
            self._counter += 1
            alias = f"pipspet{self._counter}"
        threading.Thread(target=self._worker, args=(species, kind, volume, alias), daemon=True).start()
        return True

    def _worker(self, species, kind, volume, alias):
        try:
            path = self.clip_path(species, kind, volume)
            buf = ctypes.create_unicode_buffer(128)
            rc = self._mci(f'open "{path}" type waveaudio alias {alias}', buf, 127, 0)
            if rc == 0:
                self._mci(f"play {alias} wait", buf, 127, 0)
                self._mci(f"close {alias}", buf, 127, 0)
            else:
                logger.debug("MCI open failed (%s) for %s", rc, path)
        except Exception as exc:
            logger.debug("pet sound failed: %s", exc)
        finally:
            with self._lock:
                self._playing -= 1


if __name__ == "__main__":  # quick listening test: python pet_sounds.py cat
    import sys
    species = sys.argv[1] if len(sys.argv) > 1 else "cat"
    voice = PetVoice()
    for kind in MOODS + EVENTS:
        print(species, kind)
        voice._last = 0
        voice.play(species, kind, 0.7)
        time.sleep(1.8)
