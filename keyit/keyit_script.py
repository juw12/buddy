"""Chinese script conversion for dictation: Simplified <-> Traditional (Taiwan).

Speech models write Mandarin in Simplified characters. Pick "Chinese 繁體 (Taiwan)" in Settings -> Voice and
Buddy types 這裡的系統 instead of 这里的系统. Pure Python: the tables are the OpenCC dictionaries in keyit/opencc
(Apache-2.0), read on first use, so nothing is loaded until Chinese is typed.

    to_traditional("头发和发现")  -> "頭髮和發現"      phrases first (longest match), then single characters
    to_simplified("頭髮和發現")   -> "头发和发现"
    fold("說錯了")               -> "说错了"          character by character, same length: for matching only

Script only, not vocabulary: 软件 becomes 軟件, not 軟體.
"""
import functools
from pathlib import Path

TABLES = Path(__file__).parent / "opencc"
# Taiwan writes 台灣, 平台, 舞台; the formal 臺 is the dictionary default, so it is folded to the everyday form
EVERYDAY_TW = {"臺": "台"}
# characters that are Traditional forms of another character AND ordinary Simplified characters themselves
# (著名, 乾坤, 覆盖, 瞭望, 慰藉): never rewritten one by one, only inside a known phrase (乾淨 -> 干净)
KEEP_IN_SIMPLIFIED = frozenset("著乾覆瞭藉彷吒甯麴榘薹")
SIMPLIFIED = ("zh-cn", "zh-sg", "zh-hans")
TRADITIONAL = ("zh-tw", "zh-hk", "zh-mo", "zh-hant")


def _rows(name):
    path = TABLES / name
    if not path.exists():
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            source, _, targets = line.rstrip("\r\n").partition("\t")
            targets = [target for target in targets.split(" ") if target]
            if source and targets and not source.startswith("#"):
                yield source, targets


def _common(char):
    """True for an everyday Simplified character (GB2312), used to pick between two possible sources."""
    try:
        char.encode("gb2312")
        return True
    except UnicodeEncodeError:
        return False


def _longest(phrases):
    """{first character: length of the longest phrase starting with it} - bounds the search at each position."""
    longest = {}
    for phrase in phrases:
        if len(phrase) > longest.get(phrase[0], 0):
            longest[phrase[0]] = len(phrase)
    return longest


@functools.lru_cache(maxsize=None)
def _tables():
    """(to Traditional, to Simplified); each is (phrases, longest phrase by first character, characters,
    a final character-for-character pass: the forms Taiwan writes, 裏 -> 裡, 着 -> 著, 臺 -> 台)."""
    variants = {source: targets[0] for source, targets in _rows("TWVariants.txt")}
    local = {source: EVERYDAY_TW.get(target, target) for source, target in variants.items()}
    local.update(EVERYDAY_TW)
    taiwan = str.maketrans(local)

    chars, back_chars = {}, {}
    for simplified, candidates in _rows("STCharacters.txt"):
        chars[simplified] = candidates[0]
        for candidate in candidates:
            if candidate == simplified or candidate in KEEP_IN_SIMPLIFIED:
                continue
            known = back_chars.get(candidate)
            if known is None or (not _common(known) and _common(simplified)):
                back_chars[candidate] = simplified
    for standard, written in local.items():
        target = back_chars.get(standard, standard)
        if written != target and written not in chars and written not in KEEP_IN_SIMPLIFIED:
            back_chars.setdefault(written, target)

    phrases, back_phrases = {}, {}
    for simplified, candidates in _rows("STPhrases.txt"):
        phrases[simplified] = candidates[0]
        for candidate in candidates:
            back_phrases.setdefault(candidate, simplified)
            back_phrases.setdefault(candidate.translate(taiwan), simplified)
    return (phrases, _longest(phrases), chars, taiwan), (back_phrases, _longest(back_phrases), back_chars, None)


def _has_cjk(text):
    return any(char >= "\u2e80" for char in text)


def _convert(text, table):
    phrases, longest, chars, last_pass = table
    out = []
    position, end = 0, len(text)
    while position < end:
        char = text[position]
        for size in range(min(longest.get(char, 0), end - position), 1, -1):
            target = phrases.get(text[position:position + size])
            if target is not None:
                out.append(target)
                position += size
                break
        else:
            out.append(chars.get(char, char))
            position += 1
    converted = "".join(out)
    return converted.translate(last_pass) if last_pass else converted


def to_traditional(text):
    """Simplified -> Traditional as written in Taiwan. Text already in Traditional stays as it is."""
    if not text or not _has_cjk(text):
        return text
    return _convert(text, _tables()[0])


def to_simplified(text):
    """Traditional -> Simplified. Text already in Simplified stays as it is."""
    if not text or not _has_cjk(text):
        return text
    return _convert(text, _tables()[1])


def fold(text):
    """Simplified form of every character, one for one (same length): 說錯了 and 说错了 compare equal."""
    if not text or not _has_cjk(text):
        return text
    chars = _tables()[1][2]
    return "".join(chars.get(char, char) for char in text)


def convert(text, language):
    """The script the chosen language is written in: zh-TW -> Traditional, zh-CN -> Simplified.

    Anything else (Cantonese, Japanese, auto-detect, ...) is left exactly as the speech model wrote it.
    """
    code = (language or "").lower().replace("_", "-")
    if code.startswith(TRADITIONAL):
        return to_traditional(text)
    if code.startswith(SIMPLIFIED):
        return to_simplified(text)
    return text
