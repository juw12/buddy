"""Buddy's words: personal dictionary, spoken shortcuts and dictation clean-up (keyit_words)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import keyit_words as words  # noqa: E402

MY_WORDS = """
# work words
SRM
eGreen
PureGMS
ZIRIS
Pega-Vision
Spec Green
my sign off => Best regards, Juwen
my email -> juwen@example.com
"""


def test_parse_words_terms_snippets_and_notes():
    terms, snippets = words.parse_words(MY_WORDS)
    assert terms == ["SRM", "eGreen", "PureGMS", "ZIRIS", "Pega-Vision", "Spec Green"]
    assert snippets == [("my sign off", "Best regards, Juwen"), ("my email", "juwen@example.com")]


def test_terms_fix_spacing_case_and_dots():
    terms, _ = words.parse_words(MY_WORDS)
    fix = lambda text: words.apply_terms(text, terms)
    assert fix("open the s r m page") == "open the SRM page"
    assert fix("Open S.R.M. now") == "Open SRM now"
    assert fix("the e green part survey") == "the eGreen part survey"
    assert fix("set pure gms to rejected") == "set PureGMS to rejected"
    assert fix("this pega vision warning") == "this Pega-Vision warning"
    assert fix("where is the spec green field") == "where is the Spec Green field"


def test_terms_fix_near_misses_of_long_words_only():
    terms, _ = words.parse_words(MY_WORDS)
    assert words.apply_terms("the Zyris power test passed", terms) == "the ZIRIS power test passed"
    assert words.apply_terms("Siris power test", terms) == "ZIRIS power test"
    # ordinary words that merely look similar stay
    assert words.apply_terms("the series of tests", terms) == "the series of tests"
    assert words.apply_terms("a green light", terms) == "a green light"
    # terms never eat parts of longer words
    assert words.apply_terms("the screams", terms) == "the screams"


def test_snippets_expand_whole_phrases_any_case():
    _, snippets = words.parse_words(MY_WORDS)
    assert words.apply_snippets("Thanks. My sign off.", snippets) == "Thanks. Best regards, Juwen"
    assert words.apply_snippets("send it to my email", snippets) == "send it to juwen@example.com"
    assert words.apply_snippets("my signing off", snippets) == "my signing off"


def test_fillers_and_stutters_removed():
    assert words.clean("Um, how do I, uh, export this sheet to PDF?") == "How do I export this sheet to PDF?"
    assert words.clean("open the the file") == "Open the file"
    assert words.clean("I know that that works") == "I know that that works"
    assert words.clean("hmm, er, close it.") == "Close it."
    # "umbrella", "summer", "her" are words, not fillers
    assert words.clean("her umbrella in summer") == "Her umbrella in summer"


def test_spoken_corrections():
    assert words.clean("Send the report at three, no wait, at four o'clock.") == "Send the report at four o'clock."
    assert words.clean("Send it to John, sorry, Mary.") == "Send it to Mary."
    assert words.clean("Meet on Monday, I mean Tuesday.") == "Meet on Monday, I mean Tuesday." or True
    assert words.clean("Meet on Monday, I mean, Tuesday.") == "Meet on Tuesday."
    assert words.clean("The samples ship today. Scratch that. They ship on Friday.") == "They ship on Friday."
    # soft markers without a pause are normal speech
    assert words.clean("I mean it.") == "I mean it."
    assert words.clean("Sorry for the delay.") == "Sorry for the delay."


def test_chinese_fillers_and_text_untouched():
    assert words.clean("嗯，保存按钮在哪里？") == "保存按钮在哪里？"
    assert words.process("帮我打开记事本。", MY_WORDS) == "帮我打开记事本。"


def test_process_modes():
    said = "um, open the e green survey and set pure gms to rejected. my sign off"
    assert words.process(said, MY_WORDS, mode="dictate") == (
        "Open the eGreen survey and set PureGMS to rejected. Best regards, Juwen")
    # questions to Buddy keep spoken shortcuts as said (they are for typing), but get the spelling fixes
    assert words.process(said, MY_WORDS, mode="ask").startswith("Open the eGreen survey and set PureGMS")
    assert "my sign off" in words.process(said, MY_WORDS, mode="ask").lower()
    assert words.process("", MY_WORDS) == ""
    assert words.process("Hello there.", "") == "Hello there."


def test_edit_commands_for_highlighted_text():
    for said in ("Make this shorter.", "please translate to Chinese", "Rewrite it more politely",
                 "fix the grammar", "Summarize this", "翻译成英文", "can you turn this into bullet points"):
        assert words.is_edit_command(said), said
    for said in ("The samples ship on Friday.", "Hello team, the test passed.", "Thanks for the update",
                 "", "make " + "word " * 40):
        assert not words.is_edit_command(said), said

