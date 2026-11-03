"""
Small synthesized sound effects: mouse clicks and Buddy's wake-up / got-it chimes.

Everything is generated with numpy at start-up (a few ms), so no extra audio files ship with the app.
Signals are float arrays in -1..1 at SAMPLE_RATE; keyit_core turns them into pygame sounds.
"""
import numpy as np

SAMPLE_RATE = 44100
VARIANTS_PER_SOUND = 4

# Mouse buttons (keyit_core uses these names for its negative "mouse codes")
BUTTON_PITCH = {"left": 1.0, "right": 0.92, "middle": 0.8, "side": 0.88}


def seconds(duration):
    return np.arange(int(SAMPLE_RATE * duration)) / SAMPLE_RATE


def damped_tone(t, frequency, decay):
    return np.sin(2 * np.pi * frequency * t) * np.exp(-t / decay)


def noise_tick(t, rng, decay):
    """Bright, very short noise burst - the 'snap' of a micro-switch."""
    noise = np.diff(rng.standard_normal(t.size), prepend=0.0)
    return noise * np.exp(-t / decay)


def finish(signal, peak):
    """Normalize to `peak`, with tiny fades so nothing pops."""
    signal = signal - signal.mean()
    fade_in = max(1, int(0.0002 * SAMPLE_RATE))
    fade_out = max(1, int(0.004 * SAMPLE_RATE))
    signal[:fade_in] *= np.linspace(0, 1, fade_in)
    signal[-fade_out:] *= np.linspace(1, 0, fade_out)
    return signal * (peak / (np.abs(signal).max() + 1e-9))   # level after fades, so every variant matches


def mouse_click(rng, button, release=False):
    """A crisp mouse micro-switch: snap + bright ping + a little plastic body. Release is lighter."""
    pitch = BUTTON_PITCH[button] * rng.uniform(0.97, 1.03)
    t = seconds(0.03 if release else 0.04)
    snap = noise_tick(t, rng, decay=0.0006 if release else 0.0008)
    ping = damped_tone(t, 4300 * pitch * (1.15 if release else 1.0), 0.0016) * 0.55
    body = damped_tone(t, 1150 * pitch, 0.0035) * (0.2 if release else 0.4)
    thump = 0 if release else damped_tone(t, 230, 0.005) * 0.18
    return finish(snap + ping + body + thump, peak=0.32 if release else 0.5)


def bell(t, frequency, decay):
    """Soft bell: fundamental plus quiet overtones."""
    tone = damped_tone(t, frequency, decay)
    tone += 0.28 * damped_tone(t, 2 * frequency, decay * 0.6)
    tone += 0.08 * damped_tone(t, 3 * frequency, decay * 0.4)
    attack = np.minimum(1.0, t / 0.004)                  # 4 ms attack, no click
    return tone * attack


def two_notes(first_hz, second_hz, gap_s, decay, peak):
    t = seconds(gap_s + decay * 5)
    signal = bell(t, first_hz, decay)
    shift = int(gap_s * SAMPLE_RATE)
    second = np.zeros_like(t)
    second[shift:] = bell(t[: t.size - shift], second_hz, decay)
    return finish(signal * 0.8 + second, peak)


def buddy_wake():
    """Rising G5 -> D6: 'I'm listening'."""
    return two_notes(784.0, 1174.7, gap_s=0.07, decay=0.06, peak=0.5)


def buddy_got_it():
    """Short falling D6 -> A5: 'got it, thinking'."""
    return two_notes(1174.7, 880.0, gap_s=0.05, decay=0.04, peak=0.35)


def all_effects(seed=7):
    """{name: [float arrays]} - several variants per mouse sound so repeated clicks sound natural."""
    rng = np.random.default_rng(seed)
    effects = {}
    for button in BUTTON_PITCH:
        effects[f"mouse_{button}_down"] = [mouse_click(rng, button) for _ in range(VARIANTS_PER_SOUND)]
        effects[f"mouse_{button}_up"] = [mouse_click(rng, button, release=True) for _ in range(VARIANTS_PER_SOUND)]
    effects["buddy_wake"] = [buddy_wake()]
    effects["buddy_got_it"] = [buddy_got_it()]
    return effects
