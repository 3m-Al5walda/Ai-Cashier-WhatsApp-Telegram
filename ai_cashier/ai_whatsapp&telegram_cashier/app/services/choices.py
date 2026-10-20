"""Turn the free-text `required_choices` sentences of menu.json into structured
choice groups, so the bot never hard-codes rice types / bread types.

Examples handled (taken from the real menu):
  "يجب سؤال الزيون عن ما النوع الرز الذي يريده  , زرب او كبسة او مندي او اوزي او برياني"
  "إذا اختار الزبون 'ساندويتش'، يجب سؤاله عن نوع الخبز: شراك، عادي، أو تورتيلا ."
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.menu import MenuItem
from app.services.arabic import contains_seq, normalize

_QUOTED = re.compile(r"['\"‘’“”«»]([^'\"‘’“”«»]+)['\"‘’“”«»]")
_LABEL = re.compile(r"نوع\s+(?:ال)?([^\s:：،,.]+)")
_SPLIT = re.compile(r"\s*[،,]\s*|\s+(?:او|أو)\s+")
_LEADING_OR = re.compile(r"^(?:او|أو)\s+")


@dataclass(frozen=True)
class ChoiceGroup:
    label: str                      # e.g. "نوع رز"
    options: tuple[str, ...]        # e.g. ("زرب", "كبسة", ...)
    trigger: str | None = None      # normalized word that must be in the variant name

    def applies_to(self, variant_name: str | None) -> bool:
        if self.trigger is None:
            return True
        return bool(variant_name) and self.trigger in normalize(variant_name)

    def question(self) -> str:
        return f"أي {self.label} بدك؟ ({'، '.join(self.options)})"

    def match(self, text: str) -> str | None:
        tokens = normalize(text).split()
        for option in sorted(self.options, key=lambda o: -len(normalize(o))):
            if contains_seq(tokens, normalize(option).split()):
                return option
        return None

    def canonical(self, option: str) -> str | None:
        wanted = normalize(option)
        return next((o for o in self.options if normalize(o) == wanted), None)


def _options_tail(sentence: str) -> str | None:
    if ":" in sentence or "：" in sentence:
        return re.split(r"[:：]", sentence)[-1]
    m = re.search(r"[,،]", sentence)
    return sentence[m.end():] if m else None


def parse_choice_sentence(sentence: str) -> ChoiceGroup | None:
    tail = _options_tail(sentence or "")
    if not tail:
        return None
    options: list[str] = []
    for part in _SPLIT.split(tail):
        part = _LEADING_OR.sub("", part.strip()).strip(" .،,؛;")
        if part and part not in options:
            options.append(part)
    if len(options) < 2:
        return None
    label_match = _LABEL.search(sentence)
    noun = re.sub(r"^ال", "", label_match.group(1)) if label_match else "اختيار"
    quoted = _QUOTED.search(sentence)
    trigger = normalize(quoted.group(1)) if quoted else None
    return ChoiceGroup(label=f"نوع {noun}", options=tuple(options), trigger=trigger or None)


def choice_groups_for(item: MenuItem) -> list[ChoiceGroup]:
    groups = []
    for sentence in item.required_choices or []:
        group = parse_choice_sentence(sentence)
        if group:
            groups.append(group)
    return groups
