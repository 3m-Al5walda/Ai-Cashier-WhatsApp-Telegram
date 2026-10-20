"""Deterministic (no-LLM) recognition of short, safety-critical messages.

Confirming or cancelling an order must never depend on fuzzy matching: the
previous version treated "اكيد" ("sure") as "اكد" ("confirm") because of a
similarity threshold. Everything here is exact-token based.
"""
from __future__ import annotations

from rapidfuzz.fuzz import ratio

from app.services.arabic import normalize

FILLERS = {
    "لو", "سمحت", "من", "فضلك", "بليز", "please", "pls", "يا", "اخي", "اخوي", "طيب",
    "يلا", "الان", "هلق", "هلا", "وين", "حبيبي", "غالي",
}

GREETING_WORDS = {
    "مرحبا", "مرحبتين", "اهلا", "اهلين", "هلا", "هلو", "هاي", "هالو", "سلام", "السلام",
    "عليكم", "ورحمه", "الله", "وبركاته", "صباح", "مساء", "الخير", "النور", "وسهلا", "مسا",
    "hi", "hello",
}
THANKS_WORDS = {
    "شكرا", "شكر", "مشكور", "مشكوره", "يعطيك", "العافيه", "الله", "يسلمو", "يسلم", "ايديك",
    "تسلم", "ثانكس", "thanks", "thank", "you", "يعافيك", "كتر", "خيرك", "الف", "يخليك",
}
AFFIRMATIVE_WORDS = {
    "نعم", "ايوه", "ايوا", "اي", "اجل", "اكيد", "تمام", "ماشي", "اوكي", "ok", "okay", "yes",
    "صح", "موافق", "اه", "ايه", "تم", "اوك", "بالضبط",
}
NEGATIVE_FIRST = {"لا", "لاء", "كلا", "no", "مش", "بلاش"}
EDIT_WORDS = {"اعدل", "تعديل", "عدل", "غير", "بدل"}

CONFIRM_PHRASES = [
    "اكد الطلب", "تاكيد الطلب", "اعتمد الطلب", "ثبت الطلب", "تمام اكد", "اكدلي",
    "اكد", "تاكيد", "اعتمد", "ثبت", "confirm",
]
CONFIRM_TAIL = {"كم", "المجموع", "الحساب", "السعر", "و", "الطلب", "هيك", "هلق"}
CANCEL_PHRASES = {
    "الغي الطلب", "الغاء الطلب", "الغاء", "كنسل", "كنسل الطلب", "cancel", "الغي كل شي",
    "الغي كل شيء", "الغي الكل", "الغيه كله", "الغي الطلب كله",
}
SUMMARY_PHRASES = [
    "ملخص", "ملخص الطلب", "الملخص", "السله", "سلتي", "طلبي", "شو طلبت", "summary", "cart",
]
TOTAL_WORDS = {"المجموع", "الحساب", "الاجمالي", "الحساب كم"}
MENU_WORDS = {"منيو", "المنيو", "المنو", "قايمه", "القايمه", "menu"}

DELIVERY_WORDS = {
    "توصيل", "دليفري", "delivery", "وصلوه", "وصلوها", "وصلولي", "وصلي", "وصلني", "توصيله",
    "وصللي", "للبيت",
}
PICKUP_WORDS = {"استلام", "سفري", "pickup", "استلم", "تيك", "اواي"}
FULFILLMENT_ONLY_WORDS = DELIVERY_WORDS | PICKUP_WORDS | {
    "بدي", "ابي", "خليه", "خليها", "خلي", "طلب", "الطلب", "ال", "و", "على", "البيت",
    "المحل", "من", "المطعم", "بس", "طلبي", "اله", "الها",
}

NUMBER_WORDS = {
    "واحد": 1, "واحده": 1, "وحده": 1, "اثنين": 2, "اتنين": 2, "ثنتين": 2, "وجبتين": 2,
    "علبتين": 2, "ثلاثه": 3, "ثلاث": 3, "تلاته": 3, "اربعه": 4, "اربع": 4, "خمسه": 5,
    "خمس": 5, "سته": 6, "ست": 6, "سبعه": 7, "ثمانيه": 8, "تسعه": 9, "عشره": 10,
}


def core_tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split() if t not in FILLERS]


def is_greeting(text: str) -> bool:
    toks = normalize(text).split()
    return 0 < len(toks) <= 5 and all(t in GREETING_WORDS for t in toks)


def is_thanks(text: str) -> bool:
    toks = normalize(text).split()
    return 0 < len(toks) <= 6 and all(t in THANKS_WORDS for t in toks)


def is_affirmative(text: str) -> bool:
    toks = core_tokens(text)
    return 0 < len(toks) <= 4 and all(t in AFFIRMATIVE_WORDS for t in toks)


def is_negative(text: str) -> bool:
    toks = core_tokens(text)
    return bool(toks) and (toks[0] in NEGATIVE_FIRST or any(t in EDIT_WORDS for t in toks))


def command_of(text: str) -> str | None:
    """'confirm' | 'cancel' | 'summary' | 'menu' | None — exact phrases only."""
    core = core_tokens(text)
    if not core:
        return None
    joined = " ".join(core)
    if joined in {normalize(p) for p in CANCEL_PHRASES}:
        return "cancel"
    for phrase in CONFIRM_PHRASES:
        ptoks = normalize(phrase).split()
        if core[: len(ptoks)] == ptoks and set(core[len(ptoks):]) <= CONFIRM_TAIL:
            return "confirm"
    if any(t in TOTAL_WORDS for t in core) and any(t in {"كم", "شو", "المجموع", "الحساب"} for t in core):
        return "summary"
    if any(joined == normalize(p) or ratio(joined, normalize(p)) >= 88 for p in SUMMARY_PHRASES):
        return "summary"
    if any(t in MENU_WORDS for t in core):
        return "menu"
    return None


def fulfillment_of(text: str) -> str | None:
    toks = normalize(text).split()
    if any(t in DELIVERY_WORDS for t in toks):
        return "delivery"
    joined = " ".join(toks)
    if any(t in PICKUP_WORDS for t in toks) or "من المحل" in joined or "بجي استلم" in joined:
        return "pickup"
    return None


def only_fulfillment(text: str) -> bool:
    toks = normalize(text).split()
    return bool(toks) and fulfillment_of(text) is not None and all(
        t in FULFILLMENT_ONLY_WORDS or t in FILLERS for t in toks
    )


def quantity_in(tokens: list[str]) -> int | None:
    for tok in tokens:
        if tok.isdigit() and 0 < int(tok) <= 99:
            return int(tok)
    for tok in tokens:
        if tok in NUMBER_WORDS:
            return NUMBER_WORDS[tok]
    return None


QUESTION_WORDS = {"كم", "بكم", "سعر", "سعره", "شو", "ايش", "وش", "هل", "اسعار"}


def is_question(text: str) -> bool:
    return "؟" in text or "?" in text or any(t in QUESTION_WORDS for t in normalize(text).split())
