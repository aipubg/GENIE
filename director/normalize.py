"""Hinglish command normalisation for the director input (director/normalize).

WHY THIS EXISTS (D-038):
    Needle 2 is a 14MB English-first tool-calling model. It routes English commands
    correctly, but Hinglish command words ("kholo", "agla", "awaz") are outside its
    training distribution, so it correctly refuses (low confidence / no call) instead of
    guessing. GENIE's owner requires Hinglish as the default conversational language.

    So GENIE performs a deterministic *language normalisation* step in FRONT of the
    director: Hinglish command words are mapped to their English equivalents, and NEDLE2
    still makes the routing decision. This is not a second inference stack — it is a
    lexicon, fully auditable and testable.

    The original user text is always preserved (trace, audit, memory writes); only the
    director's input is normalised.
"""
from __future__ import annotations

import re
from typing import List, Tuple

# Phrase-level rules first (longest match wins), then single-token rules.
PHRASES: List[Tuple[str, str]] = [
    (r"\bbandh?\s+kar\s*(do|dena|do na)?\b", "close"),
    (r"\bchalu\s+kar\s*(do|dena)?\b", "start"),
    (r"\bkhol\s*(do|dena|na)?\b", "open"),
    (r"\bcontinue\s+kar\s*(do|na)?\b", "continue"),
    (r"\bkar\s*(do|dena)\b", ""),
    (r"\bbadha\s*(do|dena)\b", "up"),
    (r"\bghata\s*(do|dena)\b", "down"),
    (r"\bmera\s+latest\s+project\b", "my latest project"),
    (r"\blatest\s+project\b", "latest project"),
    (r"\bpichh?la\s+project\b", "previous project"),
]

TOKENS = {
    # actions
    "kholo": "open", "khol": "open", "kholna": "open", "kholie": "open",
    "kholiye": "open", "open": "open", "launch": "open",
    "band": "close", "bandh": "close", "close": "close", "exit": "close",
    "chalao": "play", "bajao": "play", "play": "play",
    "roko": "pause", "ruko": "pause", "pause": "pause",
    "agla": "next", "aglaa": "next", "next": "next", "skip": "next",
    "pichhla": "previous", "pichla": "previous", "previous": "previous",
    "badhao": "up", "barhao": "up", "increase": "up",
    "kam": "down", "kum": "down", "ghatao": "down", "decrease": "down",
    # objects
    "awaz": "volume", "awaaz": "volume", "sound": "volume", "volume": "volume",
    "gana": "song", "gaana": "song", "song": "song", "music": "music",
    "phone": "phone", "mobile": "phone", "android": "phone", "cellphone": "phone",
    "laptop": "laptop", "computer": "pc", "desktop": "pc", "pc": "pc",
    "browser": "chrome", "chrome": "chrome",
    # possessives / fillers
    "mera": "my", "meri": "my", "mere": "my", "my": "my",
    "yaad": "remember", "rakho": "remember", "rakh": "remember",
    "dhundo": "find", "dhoondo": "find", "dhundo ": "find", "khojo": "find",
    "talash": "find", "chahiye": "want",
    "batao": "tell", "dikhao": "show", "karo": "", "kardo": "", "kar": "",
    "do": "", "dena": "", "na": "", "hai": "", "ka": "", "ki": "", "ke": "",
    "ko": "", "par": "on", "me": "in", "mein": "in", "se": "",
    "aur": "and", "bhi": "also", "abhi": "now", "jaldi": "quickly",
    "thoda": "a bit", "zara": "please",
}

_DEVANAGARI_HINT = re.compile(r"[\u0900-\u097F]")

# Devanagari command words (typed Hindi) — mapped before transliteration-free matching
DEVANAGARI = {
    "खोलो": "open", "खोल": "open", "बंद": "close", "चालू": "start",
    "आवाज़": "volume", "आवाज": "volume", "गाना": "song", "अगला": "next",
    "ढूंढो": "find", "खोजो": "find",
    "पिछला": "previous", "बढ़ाओ": "up", "कम": "down", "रोको": "pause",
    "चलाओ": "play", "मेरा": "my", "मेरी": "my", "याद": "remember",
}


def normalize(text: str) -> str:
    """Return an English-normalised form of a Hinglish/Hindi/English command.

    Never changes meaning-carrying values (numbers, app names, song names); only
    command words, particles and possessives are mapped.
    """
    if not text:
        return text
    out = text

    for dev, eng in DEVANAGARI.items():
        out = out.replace(dev, f" {eng} ")

    # phrase rules run case-insensitively on the ORIGINAL text so that user values
    # (song names, file names, app names) keep their original casing
    for pattern, repl in PHRASES:
        out = re.sub(pattern, repl, out, flags=re.I)

    def _replace_word(match: "re.Match[str]") -> str:
        word = match.group(0)
        mapped = TOKENS.get(word.lower())
        return mapped if mapped is not None else word      # unmatched -> keep verbatim

    out = re.sub(r"[0-9A-Za-z\u0900-\u097F]+", _replace_word, out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = _digits_from_words(out)
    out = _verb_first(out)
    return out or text.strip()


# Speech engines return "volume thirty" rather than "volume 30", so spoken numbers are
# converted to digits before routing (A-044).
_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
         "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
         "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}


def _digits_from_words(text: str) -> str:
    words = text.split()
    out: list[str] = []
    index = 0
    while index < len(words):
        word = words[index].lower()
        value = None
        consumed = 1

        if word in _ONES:
            value = _ONES[word]
            if index + 1 < len(words) and words[index + 1].lower() == "hundred":
                value *= 100
                consumed = 2
        elif word in _TENS:
            value = _TENS[word]
            if index + 1 < len(words) and words[index + 1].lower() in _ONES:
                value += _ONES[words[index + 1].lower()]
                consumed = 2
        elif word == "hundred":
            value = 100
        elif word == "a" and index + 1 < len(words) and words[index + 1].lower() == "hundred":
            value, consumed = 100, 2

        if value is None:
            out.append(words[index])
            index += 1
        else:
            out.append(str(value))
            index += consumed
    return " ".join(out)


# English command verbs the router expects at the start of the sentence
_VERBS = ("open", "close", "play", "pause", "next", "previous", "find", "move", "copy",
          "minimize", "maximize", "focus", "mute", "unmute", "start", "run",
          # A-063: "continue"/"resume" are command verbs too, and Hinglish puts them last
          # ("mera project continue karo"). Without the reorder the router saw
          # "my latest project continue" and mis-routed it to media.play with 0.99
          # confidence — a wrong action. Verb-first makes it escalate instead, which the
          # smoke suite (and the architecture) treats as the correct safe outcome.
          "continue", "resume")


def _verb_first(text: str) -> str:
    """Hinglish puts the verb last ("notepad kholo" -> "notepad open"); the router expects
    the English verb first ("open notepad"). Reorder deterministically (D-038)."""
    words = text.split()
    for index, word in enumerate(words):
        if word.lower() in _VERBS and index > 0:
            return " ".join([words[index]] + words[:index] + words[index + 1:])
    return text


def normalize_decision_input(text: str) -> Tuple[str, bool]:
    """Returns (normalised_text, changed)."""
    norm = normalize(text)
    return norm, norm.strip().lower() != (text or "").strip().lower()
