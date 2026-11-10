"""Buddy's words: your personal dictionary, spoken shortcuts and dictation clean-up.

Pure Python (no downloads, no network), so it runs the same on every PC and in the tests.

"My words" (Settings -> Voice) is plain text, one entry per line:
    SRM                         a word Buddy should always spell this way
    eGreen
    my sign off => Best regards, Juwen      a spoken shortcut: say the left side, get the right side
    # lines starting with # are notes

What it fixes after speech recognition:
  * your words: "s r m", "S.R.M." and "srm" become "SRM"; "e green" becomes "eGreen"; a near miss of a long
    word ("Zyris" for "ZIRIS") is corrected too,
  * spoken shortcuts expand ("my sign off" -> "Best regards, Juwen"),
  * filler sounds go ("um", "uh", "er"), and a stutter ("the the") is said once,
  * corrections you make while talking: "at three, no wait, at four" -> "at four";
    "send it to John, sorry, Mary" -> "send it to Mary"; "... scratch that" drops the sentence before it.
"""
import difflib
import re

SNIPPET_ARROWS = re.compile(r"\s*(?:=>|->|→)\s*")
FILLERS = ("um", "umm", "uh", "uhh", "uhm", "er", "erm", "hmm", "mm")
FILLERS_ZH = ("嗯", "呃", "额")
# repeated twice on purpose in normal speech ("I know that that works"), so never collapsed
REPEAT_OK = {"that", "had", "is", "do", "very", "so", "no", "bye", "ha", "really", "go"}
# say one of these and the words just before it are replaced by what follows
CORRECTION = r"(?:no wait|wait no|no no|sorry|i mean|no sorry|actually no|no actually|correction|or rather|rather)"
SCRATCH = r"(?:scratch that|delete that|forget that|strike that)"
SOUND_ALIKE = ({"s", "z", "c"}, {"c", "k", "q"}, {"f", "p"}, {"i", "e", "y"}, {"j", "g"}, {"v", "w", "b"})
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")


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
    return re.sub(r"[^0-9a-z]", "", text.lower())


def _spelled(term):
    """Regex for a term said or written with any spacing: 'SRM' ~ 's r m' ~ 'S.R.M.'; 'eGreen' ~ 'e green'."""
    chars = _squash(term)
    if not chars:
        return None
    body = r"[\s.\-]*".join(re.escape(ch) for ch in chars)
    return re.compile(r"(?<![0-9A-Za-z])" + body + r"\.?(?![0-9A-Za-z])", re.IGNORECASE)


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


def apply_terms(text, terms):
    """Spell every term in `terms` the way it is written in 'My words'."""
    for term in sorted(terms, key=len, reverse=True):      # longest first: "Spec Green" before "Green"
        pattern = _spelled(term)
        if pattern is None:
            continue
        text = pattern.sub(lambda _m, t=term: t, text)
        squashed_len = len(_squash(term))
        words = len(term.split())
        if squashed_len < 5 or words > 3:
            continue
        # near misses, word by word (n words and n+1 words for a term the recognizer split in two)
        tokens = list(WORD.finditer(text))
        replaced = []
        for size in (words, words + 1):
            for i in range(len(tokens) - size + 1):
                start, end = tokens[i].start(), tokens[i + size - 1].end()
                chunk = text[start:end]
                if chunk == term or _squash(chunk) == _squash(term):
                    continue
                if any(start < r_end and end > r_start for r_start, r_end in replaced):
                    continue
                if _near_miss(chunk, term):
                    replaced.append((start, end))
        for start, end in sorted(replaced, reverse=True):
            text = text[:start] + term + text[end:]
    return text


def apply_snippets(text, snippets):
    """Expand spoken shortcuts. The spoken side matches whole words, any case, with or without a full stop."""
    for spoken, replacement in sorted(snippets, key=lambda pair: len(pair[0]), reverse=True):
        words = [re.escape(w) for w in re.findall(r"[0-9A-Za-z']+", spoken)]
        if not words:
            continue
        pattern = re.compile(r"(?<![0-9A-Za-z])" + r"[\s,.\-]+".join(words) + r"(?![0-9A-Za-z])[.!?]?", re.IGNORECASE)
        text = pattern.sub(lambda _m, r=replacement: r, text)
    return text


# --------------------------------------------------------------------------- clean-up
def remove_fillers(text):
    # "how do I, uh, export" -> "how do I export": the pause commas around a filler go with it
    pattern = r"(?:,\s*)?(?<![0-9A-Za-z'])(?:" + "|".join(FILLERS) + r")(?![0-9A-Za-z'])[,.]?\s*"
    text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
    for filler in FILLERS_ZH:
        text = re.sub(re.escape(filler) + r"[，,。]?", "", text)
    return text


def remove_stutters(text):
    def collapse(match):
        word = match.group(1)
        return match.group(0) if word.lower() in REPEAT_OK else word
    return re.sub(r"\b([A-Za-z']+)(?:[\s,]+\1\b)+", collapse, text, flags=re.IGNORECASE)


def apply_corrections(text):
    """Self-corrections said out loud. 'at three, no wait, at four' -> 'at four'."""
    # "... scratch that ..." drops what was said just before it: the clause it ends, or, when it starts a
    # sentence of its own ("... today. Scratch that."), the whole sentence before it
    scratch = re.compile(r"[,;]?\s*\b" + SCRATCH + r"\b[,.!?]?\s*", re.IGNORECASE)
    for _ in range(5):
        match = scratch.search(text)
        if not match:
            break
        before, after = text[:match.start()].rstrip(), text[match.end():]
        if before.endswith((".", "!", "?")):
            before = before[:-1]
        cut = max(before.rfind(ch) for ch in ".!?\n") + 1
        text = (before[:cut] + " " + after).strip()
    # "X, <correction>, Y": the repair Y replaces the end of X. A comma (or dash) before the marker is
    # required for the softer markers, so "I mean it" or "sorry for the delay" are left alone.
    marker = re.compile(r"(?:(?:,|\s-|\s—)\s*" + CORRECTION + r"|\s(?:no wait|wait no|actually no|no actually))"
                        r"[,.]?\s+", re.IGNORECASE)
    for _ in range(5):
        match = marker.search(text)
        if not match:
            break
        before, after = text[:match.start()], text[match.end():]
        repair = WORD.findall(after)
        if not repair:
            text = before + after
            continue
        sentence_start = max(before.rfind(ch) for ch in ".!?\n") + 1
        clause = before[sentence_start:]
        clause_words = list(WORD.finditer(clause))
        first = repair[0].lower()
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
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"([,;:])(?=[^\s\d])", r"\1 ", text)
    text = re.sub(r",\s*([.!?])", r"\1", text)
    text = re.sub(r"^[\s,.;:]+", "", text)
    text = re.sub(r"([.!?])\s*[,.]+", r"\1", text)
    text = text.strip()
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    # sentence starts after cleanup
    text = re.sub(r"([.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    return text


def clean(text, fillers=True, corrections=True):
    if fillers:
        text = remove_fillers(text)
        text = remove_stutters(text)
    if corrections:
        text = apply_corrections(text)
    return tidy(text)


def process(text, words_text="", mode="dictate", tidy_up=True):
    """Everything after speech recognition. mode 'dictate' (typed into an app) or 'ask' (a question for Buddy)."""
    if not text:
        return ""
    terms, snippets = parse_words(words_text)
    if mode == "dictate" and snippets:
        text = apply_snippets(text, snippets)
    if terms:
        text = apply_terms(text, terms)
    if tidy_up:
        text = clean(text, fillers=True, corrections=True)
    return text.strip()


EDIT_VERBS = ("make", "rewrite", "rephrase", "translate", "fix", "correct", "shorten", "summarize", "summarise",
              "simplify", "expand", "turn", "change", "convert", "format", "reword", "polish", "proofread",
              "improve", "tidy", "clean up", "put", "write this", "reply to", "answer this", "bullet")
EDIT_VERBS_ZH = ("翻译", "改", "缩短", "总结", "润色", "改写", "简化", "扩写")


def is_edit_command(text):
    """'make this shorter', 'translate to Chinese', '翻译成英文' -> True: an instruction for highlighted text,
    not words to type over it."""
    said = re.sub(r"^[\s,.]*(please|can you|could you|buddy|hey buddy)[\s,]*", "", (text or "").strip().lower())
    if not said or len(said.split()) > 25:
        return False
    if said.startswith(EDIT_VERBS_ZH):
        return True
    return any(said == verb or said.startswith(verb + " ") or said.startswith(verb + ",") for verb in EDIT_VERBS)
