"""Buddy's words: your personal dictionary, spoken shortcuts and dictation clean-up, in the language you speak.

Pure Python (no downloads, no network), so it runs the same on every PC and in the tests.

"My words" (Settings -> Voice) is plain text, one entry per line:
    SRM                         a word Buddy should always spell this way
    eGreen
    台積電                       works in any script: 台积电 is typed the way you wrote it
    my sign off => Best regards, Juwen      a spoken shortcut: say the left side, get the right side
    # lines starting with # are notes

What it fixes after speech recognition:
  * your words: "s r m", "S.R.M." and "srm" become "SRM"; "e green" becomes "eGreen"; a near miss of a long
    word ("Zyris" for "ZIRIS") is corrected, and so is a made-up word that sounds like one of yours
    ("Bizaira's" for "the ZIRIS") - but never a word that is in the dictionary ("series", "Sirius", "not
    paid") unless the dictionary says it exactly like yours ("speck green" for "Spec Green"); for the rest
    add a line of your own: peg vision => Pega-Vision,
  * spoken shortcuts expand ("my sign off" -> "Best regards, Juwen"),
  * filler sounds go ("um", "uh", "er"; "ehm"; "äh"; "euh"; 嗯; えーと), and a stutter ("the the") is said once,
  * corrections you make while talking: "at three, no wait, at four" -> "at four";
    "jam tiga, eh salah, jam empat" -> "jam empat"; "三点开会，不对，四点开会" -> "四点开会";
    "... scratch that" drops the sentence before it.

Every rule belongs to a language (the one picked in Settings -> Voice), because a filler in one language is
a word in another: "er" is noise in English and "he" in German; "anak anak" is not a stutter in Indonesian.
Fillers: English, Indonesian, Malay, Chinese, Japanese, Korean, Thai, Vietnamese, German, Spanish, French.
Spoken corrections: English, Indonesian, Malay, Chinese, German, Spanish, French.
"""
import difflib
import functools
import importlib.util
import json
import re
import unicodedata
from pathlib import Path

import keyit_script

SNIPPET_ARROWS = re.compile(r"\s*(?:=>|->|→)\s*")
# Latin letters with their accents (é, ü, ñ, ơ, ...) and digits. Chinese, Thai, Korean are not in here, so
# "打开SRM系统" still has a word boundary on both sides of SRM.
LATIN = "0-9A-Za-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F\u0300-\u036F\u1E00-\u1EFF"
EDGE = "[" + LATIN + "'’]"
WORD = re.compile("[" + LATIN + "][" + LATIN + "'’\\-]*")
SENTENCE_END = ".!?\n。！？"
FILLERS = ("um", "umm", "uh", "uhh", "uhm", "er", "erm", "hmm", "mm")
FILLERS_ZH = ("嗯", "呃", "额")
# repeated twice on purpose in normal speech ("I know that that works"), so never collapsed
REPEAT_OK = {"that", "had", "is", "do", "very", "so", "no", "bye", "ha", "really", "go"}
SOUND_ALIKE = ({"s", "z", "c"}, {"c", "k", "q"}, {"f", "p"}, {"i", "e", "y"}, {"j", "g"}, {"v", "w", "b"})
STRESS = str.maketrans("", "", "ˈˌ")       # stress marks in the pronouncing dictionary
# after ", sorry," these start an apology or a new thought, not a repair: "I can't come, sorry, I'm busy"
NOT_A_REPAIR = {"i", "i'm", "i'll", "i've", "i'd", "we", "we're", "we'll", "we've", "it's", "that's", "but",
                "for", "about", "to", "if", "my", "you", "can", "could", "please"}

# What is cleaned up, per language.
#   fillers        sounds that are never words: always removed
#   hesitations    also real words ("eh", "anu"): removed only when a pause follows ("eh, ...")
#   guard          ... unless the hesitation starts a correction ("eh salah")
#   soft           say it after a comma and the words before it are replaced by what follows
#   bare           the same, comma or not
#   scratch        drops the sentence before it;  scratch_alone: only when said as a sentence of its own
RULES = {
    "en": {
        "fillers": FILLERS,
        "soft": ("no wait", "wait no", "no no", "sorry", "i mean", "no sorry", "actually no", "no actually",
                 "correction", "or rather", "rather"),
        "bare": ("no wait", "wait no", "actually no", "no actually"),
        "scratch": ("scratch that", "strike that"),
        "scratch_alone": ("delete that", "forget that"),
        "stutters": True,
    },
    "id": {
        "fillers": ("ehm", "em", "emm", "eee", "hmm", "mm", "um", "umm", "uh", "uhm"),
        "hesitations": ("eh", "anu"),
        "guard": ("salah", "bukan", "maaf", "silap"),
        "soft": ("maksud saya", "maksudku", "maksud aku", "ralat", "koreksi", "pembetulan"),
        "bare": ("eh,? salah", "eh,? bukan", "eh,? maaf", "eh,? silap"),
        "scratch_alone": ("hapus itu", "coret itu", "batalkan itu", "lupakan itu", "padam itu"),
    },
    "de": {
        "fillers": ("äh", "ähm", "öh", "öhm", "hm", "hmm"),
        "soft": ("ich meine", "nein warte", "korrektur"),
        "scratch_alone": ("streich das", "streiche das", "vergiss das", "lösch das", "lösche das"),
    },
    "es": {
        "fillers": ("em", "ehm", "mmm", "hmm"),
        "hesitations": ("eh",),
        "soft": ("quiero decir", "no espera", "mejor dicho", "corrección"),
        "scratch_alone": ("borra eso", "olvida eso", "tacha eso"),
    },
    "fr": {
        "fillers": ("euh", "euhm", "heu", "hum", "hmm"),
        "soft": ("je veux dire", "non attends", "non attendez", "correction"),
        "scratch_alone": ("efface ça", "oublie ça", "supprime ça"),
    },
    "vi": {"hesitations": ("ờ", "ờm", "ừm", "ùm", "um", "uhm", "hmm")},
    # a Latin-script language Buddy has no rules for: only sounds that are words in none of them
    "xx": {"fillers": ("hmm", "mm", "mmm", "uhm", "ehm", "äh", "ähm", "euh")},
}
RULES["ms"] = RULES["id"]
# fillers with no spaces around them: only at the start of a clause
SOUNDS = {
    "ja": re.compile(r"(?:^|(?<=[、。！？!?\s]))(?:えーっと|えーと|えっと|ええと|うーん|あのー|あのう|えー)[、,，]?\s*"),
    "ko": re.compile(r"(?:^|(?<=[\s,.!?]))(?:음|어)\s*(?:,|\.{3}|…)\s*"),
    "th": re.compile(r"(?:^|(?<=[\s,.!?]))(?:เอิ่ม|เอ่อ|อืม|อ่า)(?=[\s,.!?…]|$)[,.…]*\s*"),
}
ZH_PUNCT = "，,、；;。.！!？?…"
ZH_MARKERS = ("我的意思是", "我说错了", "说错了", "我是说", "错了", "不对", "不是", "更正")
ZH_AFTER_STOP = ("我说错了", "说错了", "不对", "更正")
ZH_MARKER = re.compile(r"(?:[，,]\s*(?:%s)|[。.]\s*(?:%s))[，,]\s*" % ("|".join(ZH_MARKERS), "|".join(ZH_AFTER_STOP)))
ZH_SCRATCH = re.compile(r"(?:^|(?<=[，,。.！!？?\s]))\s*(?:刚才那句不要|上一句不要|删掉上一句|删除上一句|删掉这句|删除这句"
                        r"|划掉这句|这句不要)(?=[，,。.！!？?\s]|$)[，,。.！!？?]?\s*")
KNOWN = {"en", "id", "ms", "zh", "ja", "ko", "th", "vi", "de", "es", "fr"}
ALIASES = {"yue": "zh", "cmn": "zh", "in": "id"}
# languages whose made-up words are checked against the English dictionary (Latin words inside Chinese,
# Japanese, Korean or Thai text are almost always English). Off elsewhere: "serius" is a word in Indonesian.
SOUND_MATCH = {None, "en", "zh", "ja", "ko", "th"}
# auto-detect without a language from the speech model: a few of the commonest words decide
COMMON = {
    "en": frozenset(("the and is are to of in that it you for with this was have be on at as not but they we "
                     "he she will would can from by or an my your please").split()),
    "id": frozenset(("yang dan di itu ini untuk dengan tidak saya ada akan dari pada juga ke kami kita mereka "
                     "sudah belum bisa atau karena tolong kirim hari sebelum dalam adalah oleh lebih harus dia "
                     "anda kamu aku tapi jika kalau").split()),
    "de": frozenset(("der die das und ist nicht ich mit ein eine für auf den zu sie es dem im von auch sich wir "
                     "aber wie bitte werden wird haben hat noch nur vor nach").split()),
    "es": frozenset(("el la los las que y es en un una por con para no se su al lo como más pero del favor "
                     "antes está son muy").split()),
    "fr": frozenset(("le la les et est en un une que pas pour dans je ce des du au vous nous avec sur qui il "
                     "elle sont merci avant mais plus l j qu n").split()),
}
_BARE = {"đ": "d", "Đ": "D", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L"}


# --------------------------------------------------------------------------- language and letters
def _language(language, text=""):
    """'id-ID' -> 'id', 'zh-TW' -> 'zh'. None -> None: English rules plus Chinese fillers, as before 1.3.
    'auto' or a language without rules: decided by the text itself."""
    if language is None:
        return None
    code = str(language).lower().replace("_", "-").split("-")[0]
    code = ALIASES.get(code, code)
    return code if code in KNOWN else _guess(text)


def _guess(text):
    lowered = text.lower()
    if re.search("[\u3040-\u30ff]", text):
        return "ja"
    if re.search("[\uac00-\ud7af\u1100-\u11ff]", text):
        return "ko"
    if re.search("[\u0e00-\u0e7f]", text):
        return "th"
    if re.search("[\u4e00-\u9fff]", text):
        return "zh"
    if re.search("[ăơưđ\u1ea0-\u1ef9]", lowered):
        return "vi"
    words = re.findall(r"[^\W\d_]+", lowered)
    votes = sorted(((sum(word in common for word in words), code) for code, common in COMMON.items()), reverse=True)
    return votes[0][1] if votes[0][0] >= 2 and votes[0][0] > votes[1][0] else "xx"


def _plain(text):
    """The same text without accents, character for character (same length): 'Müller' -> 'Muller'."""
    if text.isascii():
        return text
    out = []
    for char in text:
        base = _BARE.get(char)
        if base is None:
            parts = unicodedata.normalize("NFD", char)
            marks = len(parts) > 1 and all(unicodedata.combining(part) for part in parts[1:])
            base = parts[0] if marks and parts[0].isascii() else char
        out.append(base)
    return "".join(out)


def _norm(text):
    """For comparing text in any script: no accents, lower case, Simplified characters - same length."""
    text = _plain(keyit_script.fold(text))
    lowered = text.lower()
    return lowered if len(lowered) == len(text) else "".join(c.lower() if len(c.lower()) == 1 else c for c in text)


def _other_script(text):
    """True when `text` has letters outside the Latin alphabet (Chinese, Thai, Korean, Cyrillic, ...)."""
    return any(char.isalpha() and not re.match("[" + LATIN + "]", char) for char in text)


def _phrases(items):
    return "|".join(item.replace(" ", r"\s+") for item in sorted(items, key=len, reverse=True))


def _last_end(text):
    """Where the last sentence of `text` starts."""
    return max(text.rfind(mark) for mark in SENTENCE_END) + 1


def _swap(text, spans, replacement):
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


# --------------------------------------------------------------------------- "My words"
def parse_words(text):
    """'My words' text -> (terms, snippets). terms: ['SRM', ...]; snippets: [('my sign off', 'Best...'), ...]."""
    terms, snippets = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = SNIPPET_ARROWS.split(line, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            snippets.append((parts[0].strip(), parts[1].strip().replace("\\n", "\n")))
        else:
            terms.append(line)
    return terms, snippets


def _squash(text):
    return re.sub(r"[^0-9a-z]", "", _plain(text).lower())


def _spelled(term):
    """Regex for a term said or written with any spacing: 'SRM' ~ 's r m' ~ 'S.R.M.'; 'eGreen' ~ 'e green'.
    It is run on text without accents, so 'Müller' also finds 'Muller'."""
    chars = _squash(term)
    if not chars:
        return None
    body = "[\\s.\\-\u0300-\u036F]*".join(re.escape(ch) for ch in chars)
    return re.compile("(?<![" + LATIN + "])" + body + "(?![" + LATIN + "])", re.IGNORECASE)


def _respell(text, pattern, term):
    plain = _plain(text)
    out, position = [], 0
    for match in pattern.finditer(plain):
        end = match.end()
        # "S.R.M." - the last dot belongs to the abbreviation, unless it also ends the sentence
        if "." in match.group(0) and plain[end:end + 1] == "." and re.match(r"\.\s*[^\W\d_]", plain[end:]):
            follows = re.match(r"\.\s*(\S)", plain[end:]).group(1)
            if not follows.isupper():
                end += 1
        out.append(text[position:match.start()] + term)
        position = end
    return "".join(out) + text[position:]


def _written(text, term):
    """A term in another script (台積電, 東京, ไทย): typed the way it is written in 'My words', whichever
    script the speech model chose for it."""
    needle = _norm(term)
    if not needle.strip():
        return text
    first = "(?<![" + LATIN + "])" if re.match("[" + LATIN + "]", needle[0]) else ""
    last = "(?![" + LATIN + "])" if re.match("[" + LATIN + "]", needle[-1]) else ""
    pattern = re.compile(first + r"\s*".join(re.escape(part) for part in needle.split()) + last)
    return _swap(text, [match.span() for match in pattern.finditer(_norm(text))], term)


def _sounds_alike(a, b):
    if a == b:
        return True
    return any(a in group and b in group for group in SOUND_ALIKE)


def _near_miss(candidate, term):
    """A recognizer slip of a long word: same length +-1, same first sound, at most one or two letters off."""
    a, b = _squash(candidate), _squash(term)
    if len(b) < 5 or not a or abs(len(a) - len(b)) > 1 or not _sounds_alike(a[0], b[0]):
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= (0.8 if len(b) >= 7 else 0.79)


def _only_looks_alike(chunk, term, mine):
    """True when `chunk` passes the letters test but was not a slip of `term`:

    - the term's own plural or possessive ("ZIRIS's results", "two notepads"): the s was said;
    - good English that is only spelled like it: "pure gems" for "PureGMS", "not paid" for "Notepad", "vendor
      moving" for "Vendor Mapping". Real words are taken for the term only when the dictionary says them exactly
      like it ("speck green" for "Spec Green"). One sound off is how plain speech differs too ("please note Pat"),
      and a made-up term has no entry to compare with, so those stay as written.
    A chunk with a made-up word or spelled-out letters ("zirus", "pure GM") is still judged by its letters alone.
    Without the dictionary only the first check runs."""
    if _squash(re.sub("['’]?s$", "", chunk, flags=re.IGNORECASE)) == _squash(term):
        return True
    vocabulary = _vocabulary()
    if vocabulary is None:
        return False
    for piece in WORD.findall(chunk):
        word = _plain(piece).lower()
        spelled_out = not re.search("[aeiouy]", word) and word not in vocabulary       # "GM", not "PM" or "Mr"
        if spelled_out or not _known(piece, vocabulary, mine):
            return False
    heard = _said(chunk)
    return heard is None or heard != _said(term)


def apply_terms(text, terms, language=None):
    """Spell every term in `terms` the way it is written in 'My words'."""
    by_sound = _language(language, text) in SOUND_MATCH
    mine = {_squash(other) for other in terms}
    for term in sorted(terms, key=len, reverse=True):      # longest first: "Spec Green" before "Green"
        if _other_script(term):
            text = _written(text, term)
            continue
        pattern = _spelled(term)
        if pattern is None:
            continue
        text = _respell(text, pattern, term)
        squashed = _squash(term)
        words = len(term.split())
        if len(squashed) < 5 or words > 3:
            continue
        # near misses, word by word (n words and n+1 words for a term the recognizer split in two)
        tokens = list(WORD.finditer(text))
        replaced = []
        for size in (words, words + 1):
            for i in range(len(tokens) - size + 1):
                start, end = tokens[i].start(), tokens[i + size - 1].end()
                chunk = text[start:end]
                if chunk == term or _squash(chunk) == squashed:
                    continue
                if any(start < r_end and end > r_start for r_start, r_end in replaced):
                    continue
                if _near_miss(chunk, term) and not _only_looks_alike(chunk, term, mine):
                    replaced.append((start, end))
        if by_sound:
            replaced += _heard_as(text, tokens, term, terms, replaced)
        text = _swap(text, replaced, term)
    return text


# --------------------------------------------------------------------------- words that only sound right
def _sound(text):
    """How a word roughly sounds: 'ZIRIS' -> 'saras', "Bizaira's" -> 'pasaras', 'Cyrus' -> 'saras'.
    Vowels all count as one sound; b/p, d/t, f/v, s/z, g/k are not told apart; h is kept."""
    letters = re.sub("[^a-z]", "", _plain(text).lower())
    for pair, sound in (("ph", "f"), ("ck", "k"), ("sh", "S"), ("ch", "S"), ("th", "t"), ("qu", "kw"), ("rh", "r"),
                        ("wh", "w"), ("gh", ""), ("kh", "k")):
        letters = letters.replace(pair, sound)
    out = []
    for index, char in enumerate(letters):
        soft = letters[index + 1:index + 2] in ("e", "i", "y")
        if char in "aeiouyw":
            sound = "a"
        elif char == "x":
            sound = "s" if index == 0 else "ks"
        elif char == "c":
            sound = "s" if soft else "k"
        elif char == "g":
            sound = "j" if soft else "k"
        else:
            sound = {"b": "p", "v": "f", "d": "t", "q": "k", "z": "s"}.get(char, char)
        if not out or out[-1] != sound[0]:
            out.append(sound)
    return "".join(out)


def _first_vowel(text, skip=0):
    """front (i, e, ai, ...), back (anything with o or u) or open (a): 'Ziris' is front, 'Soros' is back."""
    groups = re.findall("[aeiouyw]+", re.sub("[^a-z]", "", _plain(text).lower()))
    if len(groups) <= skip:
        return ""
    group = groups[skip]
    if set(group) & set("ouw"):
        return "back"
    return "open" if set(group) == {"a"} else "front"


def _lexicon_folder():
    try:
        spec = importlib.util.find_spec("misaki")
    except (ImportError, ValueError):
        return None
    for location in (spec.submodule_search_locations or []) if spec else []:
        folder = Path(location) / "data"
        if (folder / "us_gold.json").exists() and (folder / "us_silver.json").exists():
            return folder
    return None


@functools.lru_cache(maxsize=None)
def _lexicon():
    """Every English word Buddy can pronounce -> how it is said, stress left out: 'speck' -> 'spɛk'.
    It is the dictionary that ships with Buddy's voice; None when that is not installed."""
    folder = _lexicon_folder()
    if folder is None:
        return None
    try:
        lists = []
        for name in ("us_silver.json", "us_gold.json"):                  # gold is the checked list, so it wins
            with open(folder / name, encoding="utf-8") as handle:
                lists.append(json.load(handle))
    except (OSError, ValueError):
        return None
    said = {}
    for as_written in (False, True):                                      # "read" wins over "Read"
        for entries in lists:
            for word, sound in entries.items():
                if as_written and word != word.lower():
                    continue
                if isinstance(sound, dict):                               # one entry per part of speech
                    sound = sound.get("DEFAULT") or next(iter(sound.values()), None)
                sound = sound.translate(STRESS) if isinstance(sound, str) else None
                if sound or word.lower() not in said:
                    said[word.lower()] = sound
    return said


@functools.lru_cache(maxsize=None)
def _vocabulary():
    """Every English word Buddy can pronounce, lower case. None when the dictionary is not installed: then
    nothing is ever replaced by sound alone."""
    lexicon = _lexicon()
    return None if lexicon is None else frozenset(lexicon)


def _said(text):
    """How the dictionary says `text`: 'speck green' -> 'spɛkɡɹin'. None when a word is not in it."""
    lexicon = _lexicon() or {}
    words = [word.strip("'") for word in re.split(r"[\s-]+", _plain(text).lower().replace("’", "'"))]
    sounds = [lexicon.get(word) for word in words if word]
    return "".join(sounds) if sounds and all(sounds) else None


def _known(token, vocabulary, mine):
    """True for a real word: in the dictionary (also as plural, past tense, ...), a number, or in 'My words'."""
    word = _plain(token).lower().replace("’", "'")
    if word.endswith("'s"):
        word = word[:-2]
    word = word.strip("'-")
    if len(word) <= 2 or any(char.isdigit() for char in word) or _squash(word) in mine:
        return True
    if "-" in word:
        return all(_known(part, vocabulary, mine) for part in word.split("-") if part)
    if word in vocabulary:
        return True
    for suffix, endings in (("ies", ("y",)), ("es", ("", "e")), ("s", ("",)), ("ed", ("", "e")), ("d", ("",)),
                            ("ing", ("", "e")), ("ly", ("",)), ("er", ("", "e")), ("est", ("", "e"))):
        stem = word[:-len(suffix)]
        if not word.endswith(suffix) or len(stem) < 2:
            continue
        if any(stem + ending in vocabulary for ending in endings):
            return True
        if len(stem) > 2 and stem[-1] == stem[-2] and stem[:-1] in vocabulary:      # stopped -> stop
            return True
    return False


def _heard_as(text, tokens, term, terms, taken):
    """Made-up words that sound like `term`: "Bizaira's" for "the ZIRIS". The speech model writes what it
    hears; when that is not a word at all and sounds like one of yours, yours was meant.

    Never a real word: every candidate must contain a word that is in no dictionary. Two shapes count -
    the same sounds ("Cyriss"), or the same sounds after one extra syllable in a capitalised word (the model
    glued "the" onto the name). The first vowel must be of the same kind, so "Soros" is not "ZIRIS".
    """
    key = _sound(term)
    if len(_squash(term)) < 5 or sum(char != "a" for char in key) < 3:
        return []
    vocabulary = _vocabulary()
    if vocabulary is None:
        return []
    mine = {_squash(other) for other in terms}
    shape = _first_vowel(term)
    found = []
    for size in range(1, len(term.split()) + 2):
        for i in range(len(tokens) - size + 1):
            start, end = tokens[i].start(), tokens[i + size - 1].end()
            if any(start < r_end and end > r_start for r_start, r_end in taken + found):
                continue
            if any(text[tokens[j].end():tokens[j + 1].start()].strip() for j in range(i, i + size - 1)):
                continue                                    # only words said in one breath
            pieces = [token.group(0) for token in tokens[i:i + size]]
            chunk = "".join(pieces)
            heard = _sound(chunk)
            if _squash(chunk) == _squash(term):
                continue
            glued = 0
            if heard != key:
                if size > 1 or not chunk[0].isupper() or not re.fullmatch("[^a]?a" + re.escape(key), heard):
                    continue
                glued = 1
            if _first_vowel(chunk, glued) != shape:
                continue
            if all(_known(piece, vocabulary, mine) for piece in pieces):
                continue
            found.append((start, end))
    return found


def apply_snippets(text, snippets):
    """Expand spoken shortcuts. The spoken side matches whole words, any case, with or without a full stop."""
    for spoken, replacement in sorted(snippets, key=lambda pair: len(pair[0]), reverse=True):
        said = _norm(spoken)
        if _other_script(spoken):
            units = re.findall(r"[0-9a-z']+|[^\W_]", said)
            joiner, stop = r"[\s,，、.\-]*", "[。.!！?？]?"
        else:
            units = re.findall(r"[0-9a-zß']+", said)
            joiner, stop = r"[\s,.\-]+", "[.!?]?"
        if not units:
            continue
        first = "(?<![" + LATIN + "])" if re.match("[" + LATIN + "]", units[0][0]) else ""
        last = "(?![" + LATIN + "])" if re.match("[" + LATIN + "]", units[-1][-1]) else ""
        pattern = re.compile(first + joiner.join(re.escape(unit) for unit in units) + last + stop)
        text = _swap(text, [match.span() for match in pattern.finditer(_norm(text))], replacement)
    return text


# --------------------------------------------------------------------------- clean-up
@functools.lru_cache(maxsize=None)
def _filler_patterns(code):
    rules = RULES.get(code or "en", {})
    always, paused, guard = rules.get("fillers"), rules.get("hesitations"), rules.get("guard")
    lead = r"(?:,\s*)?(?<!" + EDGE + ")("
    return (
        re.compile(lead + _phrases(always) + ")(?!" + EDGE + r")[,.]?\s*", re.IGNORECASE) if always else None,
        re.compile(lead + _phrases(paused) + ")(?!" + EDGE + r")\s*(?:,|\.{3}|…)\s*", re.IGNORECASE) if paused else None,
        re.compile(r"\s*,?\s*(?:" + _phrases(guard) + r")\b", re.IGNORECASE) if guard else None,
    )


def _fillers_zh(text):
    text = re.sub("嗯[，,。]?", "", text)
    text = re.sub("(?<!打)呃(?!逆)[，,。]?", "", text)                    # not 打呃, 呃逆 (hiccups)
    # 额 is also "amount" (金额, 额度): a filler only when it stands alone between pauses
    return re.sub("(?:^|(?<=[，,。！？!?；;\\s]))[额額]+[，,。…]+", "", text)


def remove_fillers(text, language=None):
    code = _language(language, text)
    if code == "zh":
        return _fillers_zh(text)
    if code in SOUNDS:
        return SOUNDS[code].sub("", text)
    always, paused, guard = _filler_patterns(code)

    def drop(match):
        word = match.group(1)
        if len(word) > 1 and word.isupper():
            return match.group(0)                           # "ER 1.2", "UM": an abbreviation, not a sound
        before = match.string[:match.start(1)].rstrip()
        if word.lower() in ("mm", "m") and before[-1:].isdigit():
            return match.group(0)                           # "5 mm"
        if guard and guard.match(match.string, match.end(1)):
            return match.group(0)                           # "eh salah": the start of a correction
        return " "

    # "how do I, uh, export" -> "how do I export": the pause commas around a filler go with it
    if always:
        text = always.sub(drop, text)
    if paused:
        text = paused.sub(drop, text)
    return _fillers_zh(text) if code is None else text


def remove_stutters(text, language=None):
    """'open the the file' -> 'open the file'. English only: 'anak anak' and 'sí sí' are said on purpose."""
    if not RULES.get(_language(language, text) or "en", {}).get("stutters"):
        return text

    def collapse(match):
        word = match.group(1)
        return match.group(0) if word.lower() in REPEAT_OK else word
    return re.sub(r"\b([A-Za-z']+)(?:[\s,]+\1\b)+", collapse, text, flags=re.IGNORECASE)


@functools.lru_cache(maxsize=None)
def _correction_patterns(code):
    rules = RULES.get(code or "en", {})
    scratch = []
    if rules.get("scratch"):
        scratch.append(r"[,;]?\s*\b(?:" + _phrases(rules["scratch"]) + ")")
    if rules.get("scratch_alone"):
        scratch.append(r"(?:^|(?<=[,;.!?\n]))\s*(?:" + _phrases(rules["scratch_alone"]) + ")")
    marker = []
    if rules.get("soft"):
        marker.append(r"(?:,|\s-|\s—)\s*(?P<soft>" + _phrases(rules["soft"]) + ")")
    if rules.get("bare"):
        marker.append(r"[,;]?\s+(?:" + _phrases(rules["bare"]) + ")")
    return (
        # a command of its own, so "scratch that itch" and "delete that file" are left alone
        re.compile("(?:" + "|".join(scratch) + r")\b(?=\s*(?:[,.!?;]|$))[,.!?;]?\s*", re.IGNORECASE) if scratch else None,
        re.compile("(?:" + "|".join(marker) + r")[,.]?\s+", re.IGNORECASE) if marker else None,
    )


def _corrections_zh(text):
    for _ in range(5):
        match = ZH_SCRATCH.search(keyit_script.fold(text))
        if not match:
            break
        before, after = text[:match.start()].rstrip("，, \t"), text[match.end():]
        if before.endswith(tuple(SENTENCE_END)):
            before = before[:-1]
        text = before[:_last_end(before)] + after
    for _ in range(5):
        match = ZH_MARKER.search(keyit_script.fold(text))
        if not match:
            break
        text = _repair_zh(text[:match.start()], text[match.end():])
    return text


def _repair_zh(before, after):
    """三点开会，不对，四点开会 -> 四点开会. Chinese has no spaces, so the slip is found by its characters."""
    start = _last_end(before)
    clause = before[start:]
    said = re.match("[^" + re.escape(ZH_PUNCT) + r"\s]+", after)
    repair = said.group(0) if said else ""
    if not repair or not clause:
        return before + after
    heard, fixed = keyit_script.fold(clause), keyit_script.fold(repair)
    for size in (2, 1):                                     # the slip usually starts like the repair
        at = heard.rfind(fixed[:size], max(0, len(clause) - 12)) if len(fixed) >= size else -1
        if at >= 0:
            return before[:start + at] + after
    at = heard.rfind(fixed[-2:]) if len(fixed) >= 2 else -1
    if at >= 0 and at + 2 >= len(repair):                   # ... or ends like it: 明天去上海，不对，后天去
        return before[:start + at + 2 - len(repair)] + repair + clause[at + 2:] + after[len(repair):]
    return before[:start + max(0, len(clause) - len(repair))] + after      # else: as many characters as said


def apply_corrections(text, language=None):
    """Self-corrections said out loud. 'at three, no wait, at four' -> 'at four'."""
    code = _language(language, text)
    if code == "zh":
        return _corrections_zh(text)
    scratch, marker = _correction_patterns(code)
    # "... scratch that ..." drops what was said just before it: the clause it ends, or, when it starts a
    # sentence of its own ("... today. Scratch that."), the whole sentence before it
    for _ in range(5):
        match = scratch.search(text) if scratch else None
        if not match:
            break
        before, after = text[:match.start()].rstrip(), text[match.end():]
        if before.endswith(tuple(SENTENCE_END)):
            before = before[:-1]
        text = (before[:_last_end(before)] + " " + after).strip()
    # "X, <correction>, Y": the repair Y replaces the end of X. A comma (or dash) before the marker is
    # required for the softer markers, so "I mean it" or "sorry for the delay" are left alone.
    position = 0
    for _ in range(5):
        match = marker.search(text, position) if marker else None
        if not match:
            break
        before, after = text[:match.start()], text[match.end():]
        repair = WORD.findall(after)
        if not repair:
            text = before + after
            continue
        first = repair[0].lower().replace("’", "'")
        if (match.group("soft") or "").lower().endswith("sorry") and first in NOT_A_REPAIR:
            position = match.end()                          # an apology, not a correction
            continue
        sentence_start = _last_end(before)
        if not before[sentence_start:].strip():             # "It works. No wait, it doesn't.": the slip is
            before = before.rstrip()                        # the sentence before the marker
            if before.endswith(tuple(SENTENCE_END)):
                before = before[:-1]
            sentence_start = _last_end(before)
        clause = before[sentence_start:]
        clause_words = list(WORD.finditer(clause))
        cut = None
        for word in reversed(clause_words[-8:]):            # the reparandum usually starts like the repair
            if word.group(0).lower() == first:
                cut = sentence_start + word.start()
                break
        if cut is None and clause_words:
            cut = sentence_start + clause_words[-1].start()     # else: the last word was the slip
        if cut is None:
            cut = len(before)
        text = before[:cut] + after
    return text


def tidy(text):
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([,.!?;:，。、；：！？])", r"\1", text)
    text = re.sub(r"([,;:])(?=[^\s\d/\u2e80-\uffff])", r"\1 ", text)       # not in 10:30, https://, 你好,世界
    text = re.sub(r",\s*([.!?])", r"\1", text)
    text = re.sub(r"^[\s,.;:，。、；：]+", "", text)
    text = re.sub(r"([.!?])\s*[,.]+", r"\1", text)
    text = re.sub(r"[，、；：]+([。！？])", r"\1", text)
    text = re.sub(r"([，。、；：！？])[，、]+", r"\1", text)
    text = text.strip()

    def capital(letter):
        upper = letter.upper()
        return upper if len(upper) == 1 else letter

    def sentence(match):
        before, rest = match.string[:match.start()], match.string[match.start(2):]
        if re.search(r"(?:^|[^" + LATIN + r"])[A-Za-z]\.[A-Za-z]$", before) or re.match("[a-z]+[A-Z]", rest):
            return match.group(0)                           # "e.g. the", "z.B. eine"; "eGreen", "iPhone"
        return match.group(1) + capital(match.group(2))

    if text and text[0].islower() and not re.match("[a-z]+[A-Z]", text):
        text = capital(text[0]) + text[1:]
    # sentence starts after cleanup
    return re.sub(r"([.!?]\s+)([^\W\d_])", sentence, text)


def clean(text, fillers=True, corrections=True, language=None):
    if fillers:
        text = remove_fillers(text, language)
        text = remove_stutters(text, language)
    if corrections:
        text = apply_corrections(text, language)
    return tidy(text)


def process(text, words_text="", mode="dictate", tidy_up=True, language=None):
    """Everything after speech recognition. mode 'dictate' (typed into an app) or 'ask' (a question for Buddy).
    language: the language being spoken ('en-US', 'id-ID', 'zh-TW', 'auto', ...); None behaves like English."""
    if not text:
        return ""
    terms, snippets = parse_words(words_text)
    if mode == "dictate" and snippets:
        text = apply_snippets(text, snippets)
    if terms:
        text = apply_terms(text, terms, language)
    if tidy_up:
        text = clean(text, fillers=True, corrections=True, language=language)
    return text.strip()


# --------------------------------------------------------------------------- "make this shorter"
EDIT_VERBS = ("make", "rewrite", "rephrase", "translate", "fix", "correct", "shorten", "summarize", "summarise",
              "simplify", "expand", "turn", "change", "convert", "format", "reword", "polish", "proofread",
              "improve", "tidy", "clean up", "put", "write this", "reply to", "answer this", "bullet",
              # Indonesian / Malay
              # Only words that open an instruction: "buat" alone is also "for", "singkat" is "brief".
              "terjemahkan", "ubah", "ganti", "perbaiki", "koreksi", "ringkas", "ringkaskan",
              "singkatkan", "persingkat", "perpendek", "pendekkan", "sederhanakan", "perluas", "kembangkan",
              "tulis ulang", "rapikan", "perhalus", "jadikan", "buat ini", "buat jadi", "buat lebih",
              "bikin ini", "bikin jadi", "bikin lebih", "balas", "jawab",
              # German, Spanish, French ("resume" alone is an English verb, "Mach 2" is a speed)
              "mach das", "mach es", "mach den", "mach die", "mach daraus", "mache", "übersetze", "übersetz",
              "korrigiere", "kürze", "fasse", "formuliere", "schreibe",
              "haz", "traduce", "corrige", "resume esto", "resume este", "resume el", "resume la", "resúmelo",
              "acorta", "reescribe", "mejora", "simplifica", "cambia",
              "traduis", "résume", "raccourcis", "réécris", "reformule", "améliore", "simplifie")
# "改天再说" is "another day", so 改 needs its complement.
EDIT_VERBS_ZH = ("翻译", "改成", "改为", "改得", "改一下", "改写", "改短", "改正", "改进", "缩短", "总结", "润色",
                 "简化", "扩写", "修正", "纠正", "精简", "重写", "整理一下", "整理成", "变成", "换成", "回复")
EDIT_POLITE = ("please", "can you", "could you", "hey buddy", "buddy", "tolong", "mohon", "coba", "bisakah kamu",
               "bisa kamu", "bitte", "kannst du", "por favor", "puedes", "s'il te plaît", "s'il vous plaît",
               "peux-tu", "请帮我", "帮我", "请", "麻烦", "能不能", "可以")
EDIT_JA = re.compile(r"(?:翻訳|要約|短く|直し|修正|書き直|言い換え|丁寧).*(?:して|ください|下さい|くれ|お願い|お願いします)$")
EDIT_KO = re.compile(r"(?:번역|요약|짧게|고쳐|수정|다듬|바꿔).*(?:줘|주세요|해|해요)$")


def is_edit_command(text):
    """'make this shorter', 'terjemahkan ke bahasa Inggris', '翻譯成英文' -> True: an instruction for the
    highlighted text, not words to type over it."""
    said = (text or "").strip().lower()
    polite = r"^[\s,，.]*(?:" + "|".join(re.escape(word) for word in EDIT_POLITE) + r")[\s,，]*"
    said = re.sub(polite, "", said)
    if not said or len(said.split()) > 25 or (" " not in said and len(said) > 60):
        return False
    bare = said.rstrip("。.!！?？ ")
    if EDIT_JA.search(bare) or EDIT_KO.search(bare):
        return True
    simplified = keyit_script.fold(said)
    if simplified.startswith(EDIT_VERBS_ZH) or re.match("把.{1,12}(?:" + "|".join(EDIT_VERBS_ZH) + ")", simplified):
        return True
    return any(said == verb or said.startswith(verb + " ") or said.startswith(verb + ",") for verb in EDIT_VERBS)
