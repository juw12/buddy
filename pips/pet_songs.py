"""Pips trick songs (v2.22): music for Sing, Dance and the other show moves.

* Every animal has its own little song, made from the animal itself (its voice, its
  family, its pace) - and when it does "Sing" it really sings the tune in its own
  voice: the cat meows it, the dog woofs it, the frog ribbits it...
* Every type has a theme: Land = Meadow Hoedown, Water = Ocean Calypso, Sky = Cloud Waltz.
* Plus songs any pet can pick: Happy Pop, Disco Night, 8-bit Adventure, Lullaby, Lo-fi Chill
  (and any other animal's song).

Each move plays its own arrangement of the chosen song: Sing -> the pet sings the tune,
dance moves -> the full band (the pet joins in on the beat), Yoga -> a slow soft version,
Rain dance -> + rain, show moves -> a short jingle.

Pure Python (no extra packages), rendered once and cached as small WAV files.
"""
from __future__ import annotations

import ctypes
import hashlib
import logging
import math
import random
import threading
import time
from pathlib import Path

import pet_sounds as ps

logger = logging.getLogger(__name__)

SR = ps.SR
SONG_VERSION = 1
CACHE_DIR = ps.CACHE_ROOT / f"songs_v{SONG_VERSION}"

# ---------------------------------------------------------------------------
# What you can choose (stored in the pet settings as "trick_song")
# ---------------------------------------------------------------------------
TYPE_SONGS = ("land", "water", "air")                 # Meadow Hoedown / Ocean Calypso / Cloud Waltz
GENERIC_SONGS = ("pop", "disco", "chip", "lullaby", "lofi")
CHOICES = ("own",) + TYPE_SONGS + GENERIC_SONGS + ("random", "off")   # + "sp:<species>"
DEFAULT_CHOICE = "own"
ICONS = {
    "own": "\U0001F43E", "land": "\U0001F33F", "water": "\U0001F30A", "air": "\u2601",
    "pop": "\U0001F3A4", "disco": "\U0001F483", "chip": "\U0001F47E", "lullaby": "\U0001F319",
    "lofi": "\U0001F3A7", "random": "\U0001F3B2", "off": "\U0001F507", "animal": "\U0001F43E",
}

# Which moves get music, and how the song is arranged for them.
MOVE_STYLE = {
    "sing": "sing",
    "dance": "dance", "moonwalk": "dance", "breakdance": "dance", "zoomies": "dance",
    "spin": "dance", "cartwheel": "dance",
    "yoga": "calm",
    "rain_dance": "rain",
    "hearts": "show", "juggle": "show", "magic": "show", "confetti": "show",
    "fireworks": "show", "kungfu": "show",
}
STYLES = ("sing", "dance", "calm", "rain", "show")
# With music on, these moves last a whole song instead of a few seconds.
SONG_SECONDS = {"sing": 8.0, "dance": 8.0, "rain_dance": 8.0, "yoga": 8.0, "moonwalk": 6.0, "breakdance": 6.0}
MAX_SECONDS = 10.0


def style_for(move: str):
    return MOVE_STYLE.get(move)


def seconds_for(move: str, default: float) -> float:
    return float(SONG_SECONDS.get(move, default))


def is_choice(choice) -> bool:
    if choice in CHOICES:
        return True
    return isinstance(choice, str) and choice.startswith("sp:") and choice[3:] in ps.VOICES


def resolve(choice, species: str, category: str = "land", rng=None):
    """The song id to play for a setting value (None = no music)."""
    if choice == "off":
        return None
    if choice == "random":
        pool = list(GENERIC_SONGS) + list(TYPE_SONGS) + ["sp:" + species]
        return (rng or random).choice(pool)
    if choice in TYPE_SONGS or choice in GENERIC_SONGS:
        return choice
    if isinstance(choice, str) and choice.startswith("sp:") and choice[3:] in ps.VOICES:
        return choice
    return "sp:" + (species if species in ps.VOICES else "cat")      # "own" (and anything unknown)


def type_song(category: str) -> str:
    return category if category in TYPE_SONGS else "land"


# ---------------------------------------------------------------------------
# Music theory
# ---------------------------------------------------------------------------
MAJOR = (0, 2, 4, 5, 7, 9, 11)
MINOR = (0, 2, 3, 5, 7, 8, 10)
DORIAN = (0, 2, 3, 5, 7, 9, 10)
MIXOLYDIAN = (0, 2, 4, 5, 7, 9, 10)
LYDIAN = (0, 2, 4, 6, 7, 9, 11)


def _mel(text: str):
    """'0:1 2:0.5 r:1 | ...' -> [(degree or None, beats), ...]"""
    notes = []
    for token in text.split():
        if token == "|":
            continue
        deg, beats = token.split(":")
        notes.append((None if deg == "r" else int(deg), float(beats)))
    return notes


def _midi(scale, root, degree):
    octave, idx = divmod(degree, 7)
    return root + 12 * octave + scale[idx]


def _hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12.0)


def _triad(r):
    return (r, r + 2, r + 4)


# Composed songs. Degrees count up the scale from the root (7 = an octave up).
SONGS = {
    "pop": dict(
        bpm=120, meter=4, root=60, scale=MAJOR, lead="lead", chord_style="pad", bass="eighths", drums="pop",
        chords=(_triad(0), _triad(4), _triad(5), _triad(3)),
        melody=_mel("2:1 4:0.5 4:0.5 7:1 4:1 | 6:1 8:0.5 6:0.5 4:1.5 r:0.5 | "
                    "5:1 7:0.5 5:0.5 9:1 7:1 | 3:0.5 5:0.5 7:1 6:0.5 5:0.5 4:1"),
        fx=()),
    "disco": dict(
        bpm=124, meter=4, root=57, scale=DORIAN, lead="strings", chord_style="stab", bass="octave", drums="disco",
        chords=(_triad(0), _triad(3), _triad(0), _triad(3)),
        melody=_mel("r:0.5 4:0.5 4:0.5 2:0.5 4:1 7:1 | 5:0.5 5:0.5 3:0.5 5:0.5 7:1 r:1 | "
                    "r:0.5 4:0.5 4:0.5 2:0.5 4:0.5 6:0.5 7:1 | 8:0.5 7:0.5 5:0.5 3:0.5 4:2"),
        fx=()),
    "chip": dict(
        bpm=140, meter=4, root=60, scale=MAJOR, lead="square", chord_style="arp", bass="octave", drums="chip",
        chords=(_triad(0), _triad(5), _triad(3), _triad(4)),
        melody=_mel("0:0.5 2:0.5 4:0.5 7:0.5 4:0.5 7:0.5 9:1 | 7:0.5 5:0.5 2:0.5 5:0.5 7:1 r:1 | "
                    "3:0.5 5:0.5 7:0.5 10:0.5 9:0.5 7:0.5 5:1 | 6:0.5 8:0.5 11:0.5 8:0.5 6:1 4:1"),
        fx=()),
    "lullaby": dict(
        bpm=76, meter=3, root=65, scale=MAJOR, lead="musicbox", chord_style="pad", bass="sub", drums="none",
        chords=(_triad(0), _triad(3), _triad(0), _triad(4)),
        melody=_mel("2:2 4:1 | 5:1.5 4:0.5 3:1 | 2:1 4:1 7:1 | 8:1 6:1 4:1"),
        fx=("chimes",)),
    "lofi": dict(
        bpm=82, meter=4, root=60, scale=MAJOR, lead="keys", chord_style="keys", bass="sub", drums="lofi",
        chords=((1, 3, 5, 7), (4, 6, 8, 10), (0, 2, 4, 6), (5, 7, 9, 11)),
        melody=_mel("r:1 5:0.5 7:0.5 8:1.5 7:0.5 | 6:1 4:1 3:1 r:1 | "
                    "r:0.5 2:0.5 4:0.5 6:0.5 9:1.5 r:0.5 | 7:1 5:1 4:1 2:1"),
        fx=("crackle",), swing=True),
    # type themes
    "land": dict(
        bpm=132, meter=4, root=55, scale=MAJOR, lead="pluck", chord_style="strum", bass="boomchick", drums="pop",
        chords=(_triad(0), _triad(3), _triad(0), _triad(4)),
        melody=_mel("0:0.5 2:0.5 4:0.5 2:0.5 4:0.5 7:0.5 4:1 | 3:0.5 5:0.5 7:0.5 5:0.5 3:1 5:1 | "
                    "4:0.5 4:0.5 2:0.5 4:0.5 7:1 9:1 | 8:0.5 6:0.5 4:0.5 6:0.5 4:1 1:1"),
        fx=()),
    "water": dict(
        bpm=108, meter=4, root=60, scale=MAJOR, lead="steel", chord_style="pad", bass="sub", drums="calypso",
        chords=(_triad(0), _triad(3), _triad(4), _triad(0)),
        melody=_mel("0:0.75 2:0.75 4:0.5 7:1 4:1 | 5:0.75 3:0.75 5:0.5 7:1 5:1 | "
                    "6:0.75 4:0.75 6:0.5 8:1 6:1 | 7:0.5 6:0.5 4:0.5 2:0.5 0:2"),
        fx=("bubbles",)),
    "air": dict(
        bpm=100, meter=3, root=62, scale=MAJOR, lead="flute", chord_style="harp", bass="sub", drums="waltz",
        chords=(_triad(0), _triad(3), _triad(4), _triad(0)),
        melody=_mel("4:1 7:1 9:1 | 10:2 7:1 | 8:1 6:1 4:1 | 7:2 r:1"),
        fx=("wind", "chimes")),
}

# How each animal family flavours its own song.
FAMILY_FEEL = {
    "mammal": dict(scale=MAJOR, bpm=(110, 130), lead="pluck", chord_style="strum", bass="boomchick", drums="pop",
                   progs=((0, 4, 5, 3), (0, 3, 4, 0), (0, 5, 3, 4)), pentatonic=True, fx=()),
    "bird": dict(scale=MAJOR, bpm=(124, 142), lead="flute", chord_style="harp", bass="sub", drums="shaker",
                 progs=((0, 3, 4, 0), (0, 4, 3, 4)), pentatonic=False, fx=("trill",)),
    "water": dict(scale=DORIAN, bpm=(96, 112), lead="steel", chord_style="pad", bass="sub", drums="calypso",
                  progs=((0, 3, 0, 6), (0, 6, 5, 6)), pentatonic=False, fx=("bubbles",)),
    "bug": dict(scale=MIXOLYDIAN, bpm=(132, 150), lead="square", chord_style="arp", bass="octave", drums="chip",
                progs=((0, 6, 3, 0), (0, 3, 6, 4)), pentatonic=False, fx=()),
    "magic": dict(scale=LYDIAN, bpm=(96, 116), lead="bell", chord_style="pad", bass="sub", drums="soft",
                  progs=((0, 1, 0, 4), (0, 1, 5, 4)), pentatonic=False, fx=("chimes",)),
    "reptile": dict(scale=MINOR, bpm=(90, 108), lead="kalimba", chord_style="pad", bass="sub", drums="bongo",
                    progs=((0, 5, 6, 0), (0, 3, 6, 0)), pentatonic=True, fx=()),
}
RHYTHMS = {
    4: ((1, 1, 1, 1), (0.5, 0.5, 1, 1, 1), (1, 0.5, 0.5, 1, 1), (1.5, 0.5, 1, 1),
        (0.5, 0.5, 0.5, 0.5, 1, 1), (1, 1, 2), (0.5, 0.5, 1, 2), (1, 0.5, 0.5, 2)),
    3: ((1, 1, 1), (2, 1), (1, 0.5, 0.5, 1), (1.5, 0.5, 1)),
}


def animal_song(species: str) -> dict:
    """The animal's own song: same animal -> always the same tune."""
    voice = ps.VOICES.get(species) or ps.VOICES["cat"]
    family = ps.family_of(species)
    feel = FAMILY_FEEL.get(family, FAMILY_FEEL["mammal"])
    rng = random.Random(int(hashlib.md5(f"song:{species}".encode()).hexdigest()[:8], 16))
    meter = 3 if (family in ("mammal", "magic", "bird") and rng.random() < 0.22) else 4
    bpm = rng.uniform(*feel["bpm"])
    if voice.get("dur", 0.3) < 0.15:
        bpm += 8                      # quick little callers get a quicker song
    if voice.get("f0", 400) < 200:
        bpm -= 10                     # big low voices take it slow
    prog = rng.choice(feel["progs"])
    scale = feel["scale"]
    chords = tuple(_triad(r) for r in prog)
    avoid = {3, 6} if feel["pentatonic"] else set()
    rhythms = RHYTHMS[meter]
    motif = rng.choice(rhythms)
    answer = rng.choice(rhythms)
    melody = []
    prev = rng.choice((4, 7, 2))
    for bar in range(4):
        rhythm = motif if bar in (0, 2) else answer
        chord = chords[bar]
        beat = 0.0
        for i, beats in enumerate(rhythm):
            last = bar == 3 and i == len(rhythm) - 1
            if last:
                deg = 7 if prev >= 4 else 0             # home
            elif beat == int(beat):                     # strong beat: a chord tone
                tones = [c + 7 * o for c in chord for o in (0, 1) if 0 <= c + 7 * o <= 10]
                deg = min(tones, key=lambda c: (abs(c - prev), rng.random()))
                if rng.random() < 0.25:
                    deg = rng.choice(tones)
            else:                                       # passing note
                step = rng.choice((-2, -1, -1, 1, 1, 2))
                deg = max(0, min(10, prev + step))
                if deg % 7 in avoid:
                    deg = max(0, min(10, deg + (1 if step > 0 else -1)))
            if bar == 2 and i < len(rhythm) - 1 and rng.random() < 0.5:
                deg = min(10, deg + 2)                  # the third bar climbs a little
            rest = (not last) and i > 0 and rng.random() < 0.08
            melody.append((None if rest else deg, float(beats)))
            prev = deg
            beat += beats
    return dict(
        bpm=round(bpm), meter=meter, root=rng.choice((57, 58, 60, 62, 64)), scale=scale, lead=feel["lead"],
        chord_style="waltz" if meter == 3 and feel["chord_style"] in ("strum", "pad") else feel["chord_style"],
        bass=feel["bass"] if meter == 4 else "sub", drums=feel["drums"] if meter == 4 else "waltz",
        chords=chords, melody=melody, fx=feel["fx"], family=family)


def song_spec(song_id: str) -> dict:
    if isinstance(song_id, str) and song_id.startswith("sp:"):
        return animal_song(song_id[3:])
    return SONGS.get(song_id) or SONGS["pop"]


# ---------------------------------------------------------------------------
# Instruments (wavetable oscillators + additive partials, all cheap per sample)
# ---------------------------------------------------------------------------
_TN = 2048
_TABLES = {}
_HARM_BUCKETS = (1, 2, 3, 4, 6, 8, 12, 16)


def _table(kind: str, harmonics: int):
    key = (kind, harmonics)
    tab = _TABLES.get(key)
    if tab is not None:
        return tab
    tab = [0.0] * _TN
    for h in range(1, harmonics + 1):
        if kind == "saw":
            a = 1.0 / h
        elif kind == "square":
            a = 1.0 / h if h % 2 else 0.0
        elif kind == "tri":
            a = ((-1) ** ((h - 1) // 2)) / (h * h) if h % 2 else 0.0
        else:
            a = 1.0 if h == 1 else 0.0
        if a:
            w = 2 * math.pi * h / _TN
            for i in range(_TN):
                tab[i] += a * math.sin(w * i)
    peak = max(abs(x) for x in tab) or 1.0
    tab = [x / peak for x in tab]
    _TABLES[key] = tab
    return tab


def _bandlimited(kind, freq):
    allowed = max(1, int(9000 / max(30.0, freq)))
    h = max(b for b in _HARM_BUCKETS if b <= allowed)
    return _table(kind, h)


def _osc(kind, freq, dur, amp=1.0, attack=0.01, decay=0.0, sustain=1.0, release=0.05, vib=0.0,
         vib_rate=5.5, vib_delay=0.15):
    n = max(1, int(dur * SR))
    tab = _bandlimited(kind, freq)
    inc = freq * _TN / SR
    out = [0.0] * n
    att = max(1, int(attack * SR))
    rel = max(1, int(min(release, dur * 0.5) * SR))
    dk = math.exp(-1.0 / (decay * SR)) if decay > 0 else 1.0
    d = 1.0
    ph = 0.0
    mask = _TN - 1
    cur_inc = inc
    vd = int(vib_delay * SR)
    for i in range(n):
        if vib and i >= vd and (i & 63) == 0:
            cur_inc = inc * (1.0 + vib * math.sin(2 * math.pi * vib_rate * i / SR))
        ph += cur_inc
        d *= dk
        env = (sustain + (1.0 - sustain) * d) * amp
        if i < att:
            env *= i / att
        r = n - i
        if r < rel:
            env *= r / rel
        out[i] = tab[int(ph) & mask] * env
    return out


def _partials(freq, dur, amp, partials, attack=0.003, release=0.04):
    """Sum of sines, each with its own decay: bells, steel drum, kalimba, keys."""
    n = max(1, int(dur * SR))
    out = [0.0] * n
    tab = _table("sine", 1)
    mask = _TN - 1
    att = max(1, int(attack * SR))
    rel = max(1, int(min(release, dur * 0.5) * SR))
    for ratio, gain, decay in partials:
        f = freq * ratio
        if f >= SR * 0.45:
            continue
        inc = f * _TN / SR
        dk = math.exp(-decay / SR)
        d = gain * amp
        ph = 0.0
        for i in range(n):
            ph += inc
            d *= dk
            out[i] += tab[int(ph) & mask] * d
    for i in range(min(att, n)):
        out[i] *= i / att
    for i in range(max(0, n - rel), n):
        out[i] *= (n - i) / rel
    return out


def _add(base, layer, gain=1.0):
    for i in range(min(len(base), len(layer))):
        base[i] += layer[i] * gain
    return base


def _instrument(name, freq, dur, amp=1.0, rng=None):
    """One note of an instrument."""
    if name == "pluck":       # guitar / banjo: bright attack fading into a soft body
        body = _osc("saw", freq, dur, amp * 0.5, attack=0.003, decay=0.35, sustain=0.0, release=0.05)
        snap = _osc("saw", freq, min(dur, 0.18), amp * 0.5, attack=0.002, decay=0.06, sustain=0.0)
        return _add(body, snap)
    if name == "kalimba":
        return _partials(freq, dur, amp, ((1, 1.0, 5.0), (5.4, 0.25, 22.0), (2.0, 0.12, 9.0)))
    if name == "steel":       # steel drum
        return _partials(freq, dur, amp, ((1, 1.0, 3.5), (2.0, 0.55, 5.0), (3.0, 0.22, 7.0), (4.07, 0.12, 9.0)),
                         attack=0.004)
    if name == "bell":
        return _partials(freq, dur, amp, ((1, 1.0, 2.2), (2.76, 0.45, 4.0), (5.4, 0.22, 7.0), (8.93, 0.1, 10.0)))
    if name == "musicbox":
        return _partials(freq * 2, dur, amp, ((1, 1.0, 3.2), (3.0, 0.18, 9.0), (5.2, 0.08, 14.0)))
    if name == "keys":        # soft electric piano
        return _partials(freq, dur, amp, ((1, 1.0, 1.8), (2.0, 0.3, 3.5), (3.0, 0.08, 6.0)), attack=0.006)
    if name == "flute":
        tone = _osc("tri", freq, dur, amp * 0.9, attack=0.06, decay=0.0, sustain=1.0, release=0.08, vib=0.006)
        if rng is not None:
            breath = ps._noise_burst(rng, min(dur, 0.12), cutoff=0.5, amp=amp * 0.06, decay=25.0, highpass=True)
            _add(tone, breath)
        return tone
    if name == "square":
        return _osc("square", freq, dur, amp * 0.55, attack=0.004, decay=0.25, sustain=0.6, release=0.03)
    if name == "strings":
        return _osc("saw", freq, dur, amp * 0.5, attack=0.012, decay=0.3, sustain=0.5, release=0.06)
    if name == "pad":
        return _osc("tri", freq, dur, amp, attack=min(0.25, dur * 0.3), decay=0.0, sustain=1.0, release=0.25)
    if name == "bass":
        return _osc("saw", freq, dur, amp * 0.7, attack=0.004, decay=0.18, sustain=0.35, release=0.04)
    if name == "sub":
        return _osc("tri", freq, dur, amp, attack=0.02, decay=0.0, sustain=1.0, release=0.12)
    # "lead" (default): a bright but friendly saw
    return _osc("saw", freq, dur, amp * 0.45, attack=0.008, decay=0.2, sustain=0.65, release=0.05, vib=0.004)


# ---------------------------------------------------------------------------
# Drums
# ---------------------------------------------------------------------------
_DRUMS = {}


def _drum(name):
    hit = _DRUMS.get(name)
    if hit is not None:
        return hit
    rng = random.Random(f"drum:{name}")
    if name in ("kick", "chip_kick"):
        n = int(0.28 * SR)
        hit = []
        ph = 0.0
        for i in range(n):
            tt = i / SR
            f = 48 + 110 * math.exp(-tt * 28)
            ph += 2 * math.pi * f / SR
            wave = math.sin(ph) if name == "kick" else (1.0 if math.sin(ph) > 0 else -1.0) * 0.6
            hit.append(wave * math.exp(-tt * 11))
    elif name in ("snare", "chip_snare"):
        noise = ps._noise_burst(rng, 0.18, cutoff=0.8 if name == "snare" else 0.95, amp=0.45, decay=22.0,
                                highpass=name == "snare")
        tone = _osc("sine", 190, 0.1, 0.45, attack=0.001, decay=0.04, sustain=0.0)
        hit = _add(noise, tone)
    elif name == "clap":
        hit = [0.0] * int(0.2 * SR)
        for k, off in enumerate((0.0, 0.011, 0.022)):
            burst = ps._noise_burst(rng, 0.16 if k == 2 else 0.02, cutoff=0.7, amp=0.4, decay=30.0, highpass=True)
            start = int(off * SR)
            for i, s in enumerate(burst):
                if start + i < len(hit):
                    hit[start + i] += s
    elif name in ("hat", "chip_hat"):
        hit = ps._noise_burst(rng, 0.05, cutoff=0.95, amp=0.3, decay=90.0, highpass=True)
    elif name == "ohat":
        hit = ps._noise_burst(rng, 0.2, cutoff=0.95, amp=0.25, decay=18.0, highpass=True)
    elif name == "shaker":
        hit = ps._noise_burst(rng, 0.07, cutoff=0.6, amp=0.22, decay=45.0, highpass=True)
    elif name in ("bongo_hi", "bongo_lo"):
        base = 330 if name == "bongo_hi" else 220
        hit = []
        ph = 0.0
        for i in range(int(0.16 * SR)):
            tt = i / SR
            ph += 2 * math.pi * base * (1 + 0.25 * math.exp(-tt * 40)) / SR
            hit.append(math.sin(ph) * math.exp(-tt * 22) * 0.7)
    else:
        hit = [0.0]
    _DRUMS[name] = hit
    return hit


def _hats(gain=0.3, drum="hat", steps=range(0, 16, 2)):
    return [(s, drum, gain) for s in steps]


DRUM_PATTERNS = {   # 16th-note steps in a 4/4 bar (12 in a 3/4 bar)
    "pop": [(0, "kick", 1.0), (8, "kick", 0.9), (10, "kick", 0.55), (4, "snare", 0.8), (12, "snare", 0.8)]
    + _hats(0.3),
    "disco": [(0, "kick", 1.0), (4, "kick", 1.0), (8, "kick", 1.0), (12, "kick", 1.0), (4, "clap", 0.7),
              (12, "clap", 0.7)] + _hats(0.35, "ohat", range(2, 16, 4)) + _hats(0.18, "hat", range(1, 16, 2)),
    "chip": [(0, "chip_kick", 0.9), (8, "chip_kick", 0.9), (4, "chip_snare", 0.6), (12, "chip_snare", 0.6)]
    + _hats(0.25, "chip_hat", range(2, 16, 4)),
    "calypso": [(0, "kick", 0.8), (12, "kick", 0.6), (6, "bongo_lo", 0.7), (3, "bongo_hi", 0.6),
                (10, "bongo_hi", 0.6), (14, "bongo_hi", 0.5)] + _hats(0.3, "shaker"),
    "shaker": [(0, "kick", 0.55), (8, "kick", 0.5)] + _hats(0.35, "shaker"),
    "bongo": [(0, "bongo_hi", 0.7), (6, "bongo_hi", 0.6), (8, "bongo_hi", 0.7), (14, "bongo_hi", 0.5),
              (3, "bongo_lo", 0.7), (11, "bongo_lo", 0.7), (4, "shaker", 0.3), (12, "shaker", 0.3)],
    "soft": [(0, "kick", 0.45), (4, "shaker", 0.3), (12, "shaker", 0.3), (8, "hat", 0.2)],
    "lofi": [(0, "kick", 0.8), (7, "kick", 0.55), (10, "kick", 0.7), (4, "snare", 0.55), (12, "snare", 0.55)]
    + _hats(0.22),
    "waltz": [(0, "kick", 0.6), (4, "shaker", 0.3), (8, "shaker", 0.3)],
    "none": [],
}

# ---------------------------------------------------------------------------
# The pet's own voice singing / joining in
# ---------------------------------------------------------------------------
_SING_LOW, _SING_HIGH = 130.0, 1000.0


def _animal_note(rng, species, freq, dur, mood="happy"):
    """One sung note: the animal's own call, pitched to the tune and fitted to the note."""
    voice = dict(ps.VOICES.get(species) or ps.VOICES["cat"])
    voice["f0"] = freq
    emo = dict(ps.EMOTIONS.get(mood) or ps.EMOTIONS["happy"])
    emo.pop("special", None)
    emo["pitch"] = 1.0
    emo["tilt"] = emo.get("tilt", 0.0) * 0.4
    fit = max(0.06, dur * 0.95)
    emo["speed"] = max(1.0, voice.get("dur", 0.3) / fit)
    samples = ps._call(rng, voice, emo, pitch_jitter=0.0)
    limit = int(dur * 1.1 * SR)
    if len(samples) > limit:
        samples = samples[:limit]
        fade = min(len(samples), int(0.02 * SR))
        for i in range(fade):
            samples[-1 - i] *= i / fade
    peak = max((abs(s) for s in samples), default=0.0) or 1.0
    return [s / peak for s in samples]


def _sing_shift(spec, species):
    """Octave shift (in semitones) that moves the tune near the animal's own pitch."""
    voice = ps.VOICES.get(species) or ps.VOICES["cat"]
    target = max(_SING_LOW, min(_SING_HIGH, float(voice.get("f0", 400))))
    notes = [_hz(_midi(spec["scale"], spec["root"], d)) for d, _b in spec["melody"] if d is not None]
    mid = sorted(notes)[len(notes) // 2] if notes else 400.0
    octaves = round(math.log2(target / mid))
    return 12 * max(-3, min(3, octaves))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
CALM_LEAD = {"lead": "musicbox", "square": "musicbox", "strings": "flute", "pluck": "kalimba", "steel": "steel",
             "keys": "keys", "flute": "flute", "bell": "bell", "musicbox": "musicbox", "kalimba": "kalimba"}


def render(song_id: str, species: str, style: str, seconds: float, seed: int = 0) -> list:
    """Synthesize `seconds` of the song arranged for a move style. Returns float samples (-1..1)."""
    spec = song_spec(song_id)
    style = style if style in STYLES else "dance"
    seconds = max(1.0, min(MAX_SECONDS, float(seconds)))
    rng = random.Random(f"{song_id}:{species}:{style}:{seed}")
    total = int(seconds * SR)
    out = [0.0] * total

    tempo = {"calm": 0.62, "rain": 0.8}.get(style, 1.0)
    beat_s = 60.0 / (spec["bpm"] * tempo)
    meter = spec["meter"]
    bar_s = beat_s * meter
    scale, root = spec["scale"], spec["root"]
    swing = spec.get("swing", False)
    bars = int(math.ceil(seconds / bar_s)) + 1

    def place(layer, at_s, gain):
        start = int(at_s * SR)
        if start >= total or gain == 0:
            return
        end = min(total, start + len(layer))
        for i in range(start, end):
            out[i] += layer[i - start] * gain

    def swung(beat_pos):
        if swing and (beat_pos * 2) % 2 == 1:     # off-beat 8th: a little late
            return beat_pos + 1.0 / 6.0
        return beat_pos

    gains = dict(lead=0.55, chords=0.16, bass=0.32, drums=0.5, voice=0.0, calls=0.42)
    if style == "sing":
        gains.update(lead=0.16, drums=0.25, chords=0.13, voice=0.62, calls=0.0)
    elif style in ("calm", "rain"):
        gains.update(lead=0.42, drums=0.0, chords=0.15, bass=0.18, calls=0.0)
    lead_name = CALM_LEAD.get(spec["lead"], "musicbox") if style in ("calm", "rain") else spec["lead"]

    # -- melody (loops) --------------------------------------------------
    melody = spec["melody"]
    phrase_beats = sum(b for _d, b in melody) or meter * 4
    shift = _sing_shift(spec, species) if gains["voice"] else 0
    t_beat = 0.0
    loop = 0
    while t_beat * beat_s < seconds and loop < 8:
        pos = 0.0
        for deg, beats in melody:
            start_s = (t_beat + swung(pos)) * beat_s
            if start_s >= seconds:
                break
            dur_s = beats * beat_s
            if deg is not None:
                midi = _midi(scale, root, deg)
                note = _instrument(lead_name, _hz(midi), dur_s * 0.98, 1.0, rng)
                place(note, start_s, gains["lead"])
                if gains["voice"]:
                    sung = _animal_note(rng, species, _hz(midi + shift), dur_s)
                    place(sung, start_s, gains["voice"])
            pos += beats
        t_beat += phrase_beats
        loop += 1

    # -- chords + bass ---------------------------------------------------
    chords = spec["chords"]
    style_c = spec["chord_style"] if style not in ("calm", "rain") else "pad"
    for bar in range(bars):
        bar_start = bar * bar_s
        if bar_start >= seconds:
            break
        chord = chords[bar % len(chords)]
        tones = [_midi(scale, root - 12, d) for d in chord]
        bass_root = _midi(scale, root - 24, chord[0])
        if style_c == "pad":
            for m in tones[:4]:
                place(_instrument("pad", _hz(m), bar_s, 1.0), bar_start, gains["chords"])
        elif style_c == "keys":
            for k, m in enumerate(tones[:4]):
                place(_instrument("keys", _hz(m), bar_s, 1.0), bar_start + k * 0.018, gains["chords"] * 1.3)
        elif style_c == "strum":       # "chick" on the off-beats
            for b in range(1, meter, 2):
                for k, m in enumerate(tones):
                    place(_instrument("pluck", _hz(m), beat_s * 0.6, 1.0), bar_start + b * beat_s + k * 0.012,
                          gains["chords"] * 1.4)
        elif style_c == "waltz":       # oom-pah-pah
            for b in (1, 2):
                for m in tones:
                    place(_instrument("keys", _hz(m), beat_s * 0.8, 1.0), bar_start + b * beat_s,
                          gains["chords"] * 1.2)
        elif style_c in ("harp", "arp"):
            step = 0.5 if style_c == "harp" else 0.25
            seq = tones + [tones[0] + 12] + tones[1:][::-1]
            for k in range(int(meter / step)):
                m = seq[k % len(seq)]
                inst = "pluck" if style_c == "harp" else "square"
                place(_instrument(inst, _hz(m + 12), beat_s * step * 1.6, 1.0), bar_start + k * step * beat_s,
                      gains["chords"] * (1.1 if style_c == "harp" else 0.9))
        elif style_c == "stab":        # disco off-beat stabs
            for b in range(meter):
                for m in tones:
                    place(_instrument("strings", _hz(m + 12), beat_s * 0.3, 1.0), bar_start + (b + 0.5) * beat_s,
                          gains["chords"] * 0.9)

        bass = spec["bass"] if style not in ("calm", "rain") else "sub"
        fifth = _midi(scale, root - 24, chord[0] + 4)
        if bass == "eighths":
            for k in range(meter * 2):
                place(_instrument("bass", _hz(bass_root), beat_s * 0.45, 1.0), bar_start + k * beat_s / 2,
                      gains["bass"])
        elif bass == "octave":
            for k in range(meter * 2):
                m = bass_root + (12 if k % 2 else 0)
                place(_instrument("bass", _hz(m), beat_s * 0.4, 1.0), bar_start + k * beat_s / 2, gains["bass"])
        elif bass == "boomchick":
            for b in range(0, meter, 2):
                m = bass_root if b % 4 == 0 else fifth
                place(_instrument("bass", _hz(m), beat_s * 0.9, 1.0), bar_start + b * beat_s, gains["bass"])
        else:   # "sub": one long low note per bar
            place(_instrument("sub", _hz(bass_root), bar_s * 0.95, 1.0), bar_start, gains["bass"] * 0.9)

    # -- drums -----------------------------------------------------------
    if gains["drums"]:
        pattern = DRUM_PATTERNS.get(spec["drums"], [])
        steps = meter * 4
        for bar in range(bars):
            bar_start = bar * bar_s
            if bar_start >= seconds:
                break
            for step, name, g in pattern:
                if step >= steps:
                    continue
                pos = step / 4.0
                if swing and step % 4 == 2:
                    pos += 1.0 / 6.0
                place(_drum(name), bar_start + pos * beat_s, gains["drums"] * g)

    # -- the pet joins in (dance / show): its own call on the first beat of bars 1 and 3
    if gains["calls"]:
        for bar in range(0, bars, 2):
            at = bar * bar_s
            if at > seconds - 0.6:
                break
            chord = chords[bar % len(chords)]
            pitch = _hz(_midi(scale, root, chord[0]) + _sing_shift(spec, species))
            place(_animal_note(rng, species, pitch, min(0.6, beat_s * 1.5)), at + 0.02, gains["calls"])
    elif style in ("calm", "rain"):
        voice = ps.VOICES.get(species) or ps.VOICES["cat"]
        call = ps._call(rng, voice, dict(ps.EMOTIONS["content"]), pitch_jitter=0.0)
        peak = max((abs(s) for s in call), default=0.0) or 1.0
        place([s / peak for s in call], 0.15, 0.3)

    # -- extras ------------------------------------------------------------
    fx = set(spec.get("fx", ()))
    if style == "rain":
        rain = ps._noise_burst(rng, seconds, cutoff=0.55, amp=0.08, decay=0.0, highpass=True)
        place(rain, 0.0, 1.0)
        thunder = ps._noise_burst(rng, 1.6, cutoff=0.04, amp=0.9, decay=2.2)
        place(thunder, seconds * 0.45, 0.6)
    if "bubbles" in fx:
        for _ in range(int(seconds * 1.2)):
            f = rng.uniform(500, 1100)
            place(ps._glide(rng, f, f * 1.9, 0.07, amp=0.5), rng.uniform(0, seconds - 0.2), 0.25)
    if "chimes" in fx and style != "sing":
        for _ in range(int(seconds * 0.8)):
            deg = rng.choice((7, 9, 11, 14))
            place(_instrument("bell", _hz(_midi(scale, root + 12, deg % 7) + 12 * (deg // 7)), 1.2, 1.0),
                  rng.uniform(0, seconds - 0.4), 0.08)
    if "trill" in fx:
        for bar in range(1, bars, 2):
            at = bar * bar_s - beat_s * 0.5
            if 0 < at < seconds - 0.3:
                f = _hz(_midi(scale, root + 24, 4))
                for k in range(4):
                    place(_osc("sine", f * (1.0 if k % 2 == 0 else 1.12), 0.05, 0.5, attack=0.004, release=0.01),
                          at + k * 0.055, 0.18)
    if "crackle" in fx:
        for _ in range(int(seconds * 10)):
            place(ps._noise_burst(rng, 0.004, cutoff=0.9, amp=0.5, decay=400.0, highpass=True),
                  rng.uniform(0, seconds), 0.12)
    if "wind" in fx:
        place(ps._noise_burst(rng, seconds, cutoff=0.03, amp=0.25, decay=0.15), 0.0, 0.35)
    if style == "show":       # big finish: the pet cheers on a final chord
        end = max(0.0, seconds - 0.9)
        last = chords[0]
        for m in last:
            place(_instrument(spec["lead"], _hz(_midi(scale, root, m)), 0.8, 1.0, rng), end, 0.18)
        call = ps._call(rng, ps.VOICES.get(species) or ps.VOICES["cat"], dict(ps.EMOTIONS["happy"]), 0.0)
        peak = max((abs(s) for s in call), default=0.0) or 1.0
        place([s / peak for s in call], end + 0.05, 0.45)

    # -- master: soft limiter, fade in / out --------------------------------
    peak = max((abs(s) for s in out), default=0.0)
    drive = 1.0 / peak if peak > 0.9 else 1.0
    fade_in = int(0.01 * SR)
    fade_out = int(min(0.45, seconds * 0.15) * SR)
    tanh = math.tanh
    for i in range(total):
        s = tanh(out[i] * drive * 1.15) * 0.9
        if i < fade_in:
            s *= i / fade_in
        r = total - i
        if r < fade_out:
            s *= r / fade_out
        out[i] = s
    return out


def clip_name(song_id, species, style, seconds, vol_step) -> str:
    safe = song_id.replace(":", "-")
    return f"{safe}_{species}_{style}_{int(round(seconds * 10))}_v{vol_step}.wav"


def clip_path(song_id: str, species: str, style: str, seconds: float, volume: float) -> Path:
    vol_step = int(round(max(0.0, min(1.0, volume)) * 10))
    path = CACHE_DIR / clip_name(song_id, species, style, seconds, vol_step)
    if not path.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        ps._write_wav(path, render(song_id, species, style, seconds), vol_step / 10)
    return path


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------
class SongPlayer:
    """Plays one trick song at a time (on its own channel, next to the pet voice and soundscapes)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._token = 0
        self._warm_lock = threading.Lock()
        try:
            self._mci = ctypes.windll.winmm.mciSendStringW
        except Exception:  # not Windows
            self._mci = None

    @property
    def available(self) -> bool:
        return self._mci is not None

    def play(self, song_id: str, species: str, style: str, seconds: float, volume: float = 0.6) -> bool:
        if not self.available or volume <= 0 or not song_id:
            return False
        with self._lock:
            self._token += 1
            token = self._token
        threading.Thread(target=self._worker, args=(token, song_id, species, style, seconds, volume),
                         daemon=True).start()
        return True

    def stop(self):
        with self._lock:
            self._token += 1

    def _worker(self, token, song_id, species, style, seconds, volume):
        alias = f"pipssong{token}"
        started = time.monotonic()
        try:
            path = clip_path(song_id, species, style, seconds, volume)
            late = time.monotonic() - started
            if token != self._token or late > seconds * 0.6:
                return          # stopped meanwhile (or took too long to make: skip this once)
            buf = ctypes.create_unicode_buffer(128)
            if self._mci(f'open "{path}" type waveaudio alias {alias}', buf, 127, 0) != 0:
                logger.debug("MCI could not open %s", path)
                return
            try:
                if late > 0.05:   # catch up with the dance if making the song took a moment
                    self._mci(f"set {alias} time format milliseconds", buf, 127, 0)
                    self._mci(f"play {alias} from {int(late * 1000)}", buf, 127, 0)
                else:
                    self._mci(f"play {alias}", buf, 127, 0)
                deadline = time.monotonic() + seconds + 2.0
                while time.monotonic() < deadline:
                    time.sleep(0.05)
                    if token != self._token:
                        self._mci(f"stop {alias}", buf, 127, 0)
                        break
                    self._mci(f"status {alias} mode", buf, 127, 0)
                    if buf.value and buf.value != "playing":
                        break
            finally:
                self._mci(f"close {alias}", buf, 127, 0)
        except Exception as exc:
            logger.debug("trick song failed: %s", exc)

    def prewarm(self, jobs, volume: float):
        """Make songs ahead of time (quietly, one by one) so tricks start with their music."""
        jobs = list(jobs)
        if not jobs:
            return

        def work():
            if not self._warm_lock.acquire(blocking=False):
                return
            try:
                for song_id, species, style, seconds in jobs:
                    try:
                        clip_path(song_id, species, style, seconds, volume)
                    except Exception as exc:
                        logger.debug("song prewarm failed: %s", exc)
                    time.sleep(0.4)
            finally:
                self._warm_lock.release()

        threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":   # listening test: python pet_songs.py cat sing own
    import sys
    sp = sys.argv[1] if len(sys.argv) > 1 else "cat"
    st = sys.argv[2] if len(sys.argv) > 2 else "dance"
    ch = sys.argv[3] if len(sys.argv) > 3 else "own"
    player = SongPlayer()
    player.play(resolve(ch, sp), sp, st, 8.0, 0.7)
    time.sleep(10)
