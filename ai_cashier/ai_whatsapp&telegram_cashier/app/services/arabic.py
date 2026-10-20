"""Arabic text helpers shared by the whole project."""
from __future__ import annotations

import json
import re
from pathlib import Path

_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670\u0640]")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_LETTERS = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"}
)

DEFAULT_ALIASES_PATH = Path(__file__).resolve().parents[2] / "data" / "aliases.json"


def normalize(text: str | None) -> str:
    """Lower-case, strip diacritics/punctuation, unify Arabic letter variants."""
    text = (text or "").translate(_DIGITS).casefold()
    text = _DIACRITICS.sub("", text).translate(_LETTERS)
    text = _NON_WORD.sub(" ", text)
    return " ".join(text.split())


def tokens_of(text: str | None) -> list[str]:
    return normalize(text).split()


def contains_seq(tokens: list[str], seq: list[str]) -> bool:
    """True if `seq` appears as a contiguous run inside `tokens`."""
    n = len(seq)
    if n == 0 or n > len(tokens):
        return False
    return any(tokens[i : i + n] == seq for i in range(len(tokens) - n + 1))


def load_aliases(path: str | Path | None = None) -> dict:
    path = Path(path) if path else DEFAULT_ALIASES_PATH
    if not path.exists():
        return {"words": {}, "products": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {"words": data.get("words", {}), "products": data.get("products", {})}


class TextNormalizer:
    """normalize() plus token-level spelling aliases from data/aliases.json."""

    def __init__(self, word_aliases: dict[str, str] | None = None):
        self.word_aliases = {
            normalize(k): normalize(v)
            for k, v in (word_aliases or {}).items()
            if normalize(k)
        }

    def __call__(self, text: str | None) -> str:
        base = normalize(text).split()
        if self.word_aliases:
            base = [self.word_aliases.get(tok, tok) for tok in base]
        return " ".join(" ".join(base).split())
