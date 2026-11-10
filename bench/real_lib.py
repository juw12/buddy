"""Real human recordings for the Buddy voice benchmark: which clips, how text is compared, added noise.

The clips are FLEURS dev recordings (Google, CC-BY 4.0): people reading sentences aloud on their own
devices. fetch_fleurs.py puts them in real/<FLEURS code>/ with a manifest.json; they are not committed.
"""
import json
import struct
import unicodedata
from functools import lru_cache
from pathlib import Path

import numpy as np

BENCH = Path(__file__).resolve().parent
OPENCC = BENCH.parent / "keyit" / "opencc"

# FLEURS folder -> (name, Buddy's speech-language setting, what one error is counted in)
LANGUAGES = {
    "en_us": ("English", "en-US", "word"),
    "id_id": ("Indonesian", "id-ID", "word"),
    "cmn_hans_cn": ("Chinese (Mandarin)", "zh-CN", "char"),
    "yue_hant_hk": ("Cantonese", "yue", "char"),
    "ms_my": ("Malay", "ms-MY", "word"),
    "ja_jp": ("Japanese", "ja-JP", "char"),
    "ko_kr": ("Korean", "ko-KR", "char"),
    "th_th": ("Thai", "th-TH", "char"),
    "vi_vn": ("Vietnamese", "vi-VN", "word"),
    "de_de": ("German", "de-DE", "word"),
    "es_419": ("Spanish", "es-ES", "word"),
    "fr_fr": ("French", "fr-FR", "word"),
}
ENGLISH_NUMBERS = dict(zip(
    "0 1 2 3 4 5 6 7 8 9 10 11 12".split(),
    "zero one two three four five six seven eight nine ten eleven twelve".split(),
))


def load(path):
    """(mono float32 audio in -1..1, sample rate) from a .wav file: 16-bit PCM or float (FLEURS is float)."""
    data = Path(path).read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"not a wav file: {path}")
    kind = channels = rate = bits = samples = None
    position = 12
    while position + 8 <= len(data):
        name = data[position:position + 4]
        size = struct.unpack("<I", data[position + 4:position + 8])[0]
        body = data[position + 8:position + 8 + size]
        if name == b"fmt ":
            kind, channels, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
            if kind == 0xFFFE and len(body) >= 26:          # "extensible": the real format follows
                kind = struct.unpack("<H", body[24:26])[0]
        elif name == b"data":
            samples = body
        position += 8 + size + (size & 1)
    formats = {(1, 16): ("<i2", 32768.0), (1, 32): ("<i4", 2147483648.0), (3, 32): ("<f4", 1.0), (3, 64): ("<f8", 1.0)}
    if samples is None or (kind, bits) not in formats:
        raise ValueError(f"unsupported wav (format {kind}, {bits} bit): {path}")
    dtype, scale = formats[(kind, bits)]
    width = np.dtype(dtype).itemsize * channels
    audio = np.frombuffer(samples[:len(samples) // width * width], dtype=dtype).reshape(-1, channels)
    return (audio.mean(axis=1) / scale).astype(np.float32), rate


def clips(real_dir, language, limit=None):
    """[(wav path, what was said)] for one FLEURS language folder."""
    folder = Path(real_dir) / language
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    return [(folder / item["file"], item["text"]) for item in manifest[:limit]]


@lru_cache(maxsize=1)
def simplified():
    """Traditional -> Simplified, one character at a time, so Chinese scores the same in either script."""
    table = {}
    for line in (OPENCC / "STCharacters.txt").read_text(encoding="utf-8").splitlines():
        simple, _, traditional = line.partition("\t")
        for form in traditional.split():
            table.setdefault(form, simple)
    for line in (OPENCC / "TWVariants.txt").read_text(encoding="utf-8").splitlines():
        standard, _, taiwan = line.partition("\t")
        for form in taiwan.split():
            table.setdefault(form, table.get(standard, standard))
    return table


def _is_text(character):
    return unicodedata.category(character)[0] in "LMN"      # letters, marks (Thai vowels and tones), digits


def tokens(text, language):
    """The units an error is counted in: words, or characters for scripts written without spaces.
    Case, punctuation and (for Chinese) the script are not errors."""
    unit = LANGUAGES[language][2]
    text = unicodedata.normalize("NFKC", text).lower()
    if unit == "char":
        table = simplified() if language in ("cmn_hans_cn", "yue_hant_hk") else {}
        return [table.get(character, character) for character in text if _is_text(character)]
    text = text.replace("\u2019", "'")
    cleaned = "".join(character if _is_text(character) or character == "'" else " " for character in text)
    words = [word.strip("'") for word in cleaned.split()]
    if language == "en_us":
        words = [ENGLISH_NUMBERS.get(word, word) for word in words]
    return [word for word in words if word]


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, token_a in enumerate(a, 1):
        current = [i]
        for j, token_b in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (token_a != token_b)))
        previous = current
    return previous[-1]


def errors(reference, heard, language):
    """(wrong units, units in the reference) for one clip."""
    wanted = tokens(reference, language)
    got = tokens(heard, language)
    return edit_distance(wanted, got), max(1, len(wanted))


def add_noise(audio, snr_db, seed):
    """The recording plus pink noise (an office hum) at the given speech-to-noise ratio. Seeded: repeatable."""
    generator = np.random.default_rng(seed)
    spectrum = np.fft.rfft(generator.standard_normal(len(audio) + 1))
    spectrum[1:] /= np.sqrt(np.arange(1, len(spectrum)))
    pink = np.fft.irfft(spectrum)[:len(audio)]
    pink /= np.sqrt(np.mean(pink ** 2))
    level = np.sqrt(np.mean(audio ** 2) / 10 ** (snr_db / 10))
    return np.clip(audio + pink * level, -1, 1).astype(np.float32)
