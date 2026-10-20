"""Menu lookup: the single place that decides which product / variant a piece of
customer text refers to. menu.json stays the source of truth.

Public API kept from the original class: search_product(), search_variant().
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from app.models.menu import Menu, MenuItem, Variant
from app.models.search import ProductSearchResult
from app.services.arabic import TextNormalizer, contains_seq, load_aliases, normalize
from app.services.choices import choice_groups_for

# Words that describe a size/portion, not a product: never used as product aliases.
_NOT_PRODUCT_WORDS = {
    "وجبه", "وجبات", "علبه", "و", "صغير", "وسط", "كبير", "عادي", "سوبر", "دبل",
    "تربل", "نصف", "ربع", "ساندويتش", "من", "مع",
}
_STOP = {"و"}


@dataclass(frozen=True)
class Mention:
    start: int
    end: int  # exclusive
    item: MenuItem


class MenuSearcher:
    def __init__(self, menu: Menu, fuzzy_threshold: int = 80, aliases: dict | None = None):
        self.menu = menu
        self.fuzzy_threshold = fuzzy_threshold
        aliases = aliases if aliases is not None else load_aliases()
        self.normalize = TextNormalizer(aliases.get("words"))

        self.product_index = {self._normalize(i.name): i for i in menu.items}
        self.product_names = list(self.product_index.keys())
        self._alias_to_item: dict[tuple[str, ...], MenuItem] = {}
        self._option_owner: dict[tuple[str, ...], MenuItem] = {}
        self._max_alias_len = 1
        self._alias_first_tokens: set[str] = set()
        self._groups_cache: dict[str, list] = {}
        self._build_aliases(aliases.get("products", {}))

    # ------------------------------------------------------------ indexing
    @staticmethod
    def _normalize(text: str) -> str:
        return normalize(text)

    def _build_aliases(self, product_aliases: dict[str, str]) -> None:
        name_tokens: dict[str, set[str]] = defaultdict(set)      # token -> product names
        variant_tokens: dict[str, set[str]] = defaultdict(set)   # token -> product names
        for item in self.menu.items:
            key = item.name.strip()
            for tok in self.normalize(item.name).split():
                name_tokens[tok].add(key)
            for variant in item.variants:
                for tok in self.normalize(variant.size_type).split():
                    variant_tokens[tok].add(key)

        def add(alias: str, item: MenuItem) -> None:
            seq = tuple(self.normalize(alias).split())
            if seq:
                self._alias_to_item[seq] = item
                self._max_alias_len = max(self._max_alias_len, len(seq))

        for item in self.menu.items:
            add(item.name, item)
        for item in self.menu.items:
            key = item.name.strip()
            for tok in self.normalize(item.name).split():
                owners = name_tokens[tok] | variant_tokens.get(tok, set())
                if tok not in _NOT_PRODUCT_WORDS and owners == {key}:
                    self._alias_to_item.setdefault((tok,), item)
            for variant in item.variants:  # e.g. "بطاطا" -> اضافة, "حلبي" -> شاورما
                for tok in self.normalize(variant.size_type).split():
                    owners = name_tokens.get(tok, set()) | variant_tokens[tok]
                    if tok not in _NOT_PRODUCT_WORDS and owners == {key}:
                        self._alias_to_item.setdefault((tok,), item)
        for alias, product in product_aliases.items():
            owner = self.product_by_name(product)
            if owner:
                add(alias, owner)
        self._alias_first_tokens = {seq[0] for seq in self._alias_to_item}

        all_variant_tokens = set(variant_tokens)
        option_items: dict[tuple[str, ...], set[str]] = defaultdict(set)
        for item in self.menu.items:
            for group in choice_groups_for(item):
                for option in group.options:
                    seq = tuple(self.normalize(option).split())
                    if seq and not (set(seq) & all_variant_tokens):
                        option_items[seq].add(item.name.strip())
        for seq, owners in option_items.items():
            if len(owners) == 1:
                self._option_owner[seq] = self.product_by_name(next(iter(owners)))

    # ------------------------------------------------------------- products
    def choice_groups(self, item: MenuItem) -> list:
        key = item.name.strip()
        if key not in self._groups_cache:
            self._groups_cache[key] = choice_groups_for(item)
        return self._groups_cache[key]

    def product_by_name(self, name: str) -> MenuItem | None:
        return self.product_index.get(self._normalize(name))

    def _strip_al(self, token: str) -> str:
        """'الزنجر' -> 'زنجر' when the rest starts a known product alias."""
        if token.startswith("ال") and len(token) > 4 and token[2:] in self._alias_first_tokens:
            return token[2:]
        return token

    def find_mentions(self, text_or_tokens: str | list[str]) -> list[Mention]:
        tokens = (
            text_or_tokens
            if isinstance(text_or_tokens, list)
            else self.normalize(text_or_tokens).split()
        )
        tokens = [self._strip_al(t) for t in tokens]
        raw, i = [], 0
        while i < len(tokens):
            for length in range(min(self._max_alias_len, len(tokens) - i), 0, -1):
                item = self._alias_to_item.get(tuple(tokens[i : i + length]))
                if item is not None:
                    raw.append(Mention(i, i + length, item))
                    i += length
                    break
            else:
                i += 1
        # "سدر دجاجة" / "سدر عليه دجاجة": consecutive mentions of the same product
        # (no "و" between them) are one mention.
        merged: list[Mention] = []
        for m in raw:
            if merged and merged[-1].item is m.item and "و" not in tokens[merged[-1].end : m.start]:
                merged[-1] = Mention(merged[-1].start, m.end, m.item)
            else:
                merged.append(m)
        return merged

    def product_for_choice(self, choices: list[str]) -> MenuItem | None:
        owners = {
            self._option_owner[seq].name
            for seq in (tuple(self.normalize(c).split()) for c in choices)
            if seq in self._option_owner
        }
        return self.product_by_name(next(iter(owners))) if len(owners) == 1 else None

    def search_product(self, name: str) -> ProductSearchResult:
        normalized = self.normalize(name)
        if not normalized:
            return ProductSearchResult(found=False, method="not_found")

        item = self.product_index.get(self._normalize(name)) or self.product_index.get(normalized)
        if item:
            return ProductSearchResult(item=item, found=True, method="exact", score=100.0)

        owners = {m.item.name for m in self.find_mentions(normalized)}
        if len(owners) == 1:
            item = self.product_by_name(next(iter(owners)))
            return ProductSearchResult(item=item, found=True, method="alias", score=95.0)

        result = process.extractOne(normalized, self.product_names, scorer=fuzz.ratio)
        if result is None:
            return ProductSearchResult(found=False, method="not_found")
        matched, score, _ = result
        if score >= self.fuzzy_threshold:
            return ProductSearchResult(
                item=self.product_index[matched], found=True, method="fuzzy", score=float(score)
            )
        return ProductSearchResult(found=False, method="not_found", score=float(score))

    def suggest(self, name: str, limit: int = 3) -> list[str]:
        """Closest real product names, for 'not on the menu' replies."""
        normalized = self.normalize(name)
        scored = sorted(
            ((fuzz.ratio(normalized, key), key) for key in self.product_names), reverse=True
        )
        return [self.product_index[k].name.strip() for s, k in scored[:limit] if s >= 60]

    # ------------------------------------------------------------- variants
    def search_variant(self, item: MenuItem, variant_name: str) -> Variant | None:
        wanted = self.normalize(variant_name)
        for variant in item.variants:
            if self.normalize(variant.size_type) == wanted:
                return variant
        return self.resolve_variant(item, variant_name)

    def resolve_variant(self, item: MenuItem, text: str) -> Variant | None:
        """Pick the variant of `item` that `text` clearly refers to, else None
        (None means 'ambiguous or not mentioned' -> the bot asks, never guesses)."""
        tokens = self.normalize(text).split()
        if not tokens or not item.variants:
            return None
        product_tokens = set(self.normalize(item.name).split())
        full = [(v, self.normalize(v.size_type).split()) for v in item.variants]

        # A) the complete variant name appears in the text (longest wins)
        hits = [(v, vt) for v, vt in full if contains_seq(tokens, vt)]
        if hits:
            longest = max(len(vt) for _, vt in hits)
            top = [v for v, vt in hits if len(vt) == longest]
            return top[0] if len(top) == 1 else None

        cores = [(v, [t for t in vt if t not in product_tokens] or vt) for v, vt in full]

        # B) the variant name minus the product words appears in the text
        hits = [(v, c) for v, c in cores if contains_seq(tokens, c)]
        if hits:
            longest = max(len(c) for _, c in hits)
            top = [(v, c) for v, c in hits if len(c) == longest]
            if len(top) == 1:
                v, c = top[0]
                if not any(contains_seq(oc, c) for ov, oc in cores if ov is not v):
                    return v
            return None

        # C) distinctive single words ("سوبر", "دبل", "حلبي"), weighted by rarity
        df = Counter(t for _, c in cores for t in set(c) if t not in _STOP)
        scored = sorted(
            (
                (sum(1 / df[t] for t in set(c) if t in tokens and t not in _STOP), i)
                for i, (_, c) in enumerate(cores)
            ),
            reverse=True,
        )
        if scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return cores[scored[0][1]][0]
        return None
