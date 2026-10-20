"""Deterministic fallback understander (no LLM).

Used when the LLM is unavailable, and in tests. It is intentionally conservative:
it only extracts what the menu data lets it match with confidence.
"""
from __future__ import annotations

from app.services import intents
from app.services.arabic import normalize
from app.services.choices import choice_groups_for
from app.services.menu_searcher import MenuSearcher
from app.services.text_understander import CustomerTurn, ItemRequest

ORDER_VERBS = {"بدي", "ابي", "اطلب", "هات", "هاتلي", "ضيف", "اضف", "اعطيني", "عطيني", "خذ", "ابغى", "ابغي", "بدنا"}
REMOVE_VERBS = {"شيل", "شيلي", "احذف", "احذفي", "امسح", "الغي", "الغيه", "الغيها", "بلاش"}
SET_QTY_VERBS = {"خليهم", "خليها", "خليه", "خليهن", "عدل", "غير", "بدل"}
PRICE_WORDS = {"كم", "بكم", "سعر", "سعره", "سعرها", "اسعار", "ثمن", "تمن", "اسعاره"}
STRONG_PRICE_WORDS = PRICE_WORDS - {"كم"}
QUESTION_NOISE = PRICE_WORDS | {"عندكم", "متوفر", "متوفره", "موجود", "موجوده", "فيه", "في", "بتوفروا", "عندك", "هل", "شو", "ايش", "وش", "ال", "هو", "هي", "ب"}
AVAIL_WORDS = {"عندكم", "متوفر", "متوفره", "موجود", "موجوده", "فيه", "في", "بتوفروا", "عندك"}
MENU_QUESTION = {"شو", "ايش", "وش", "ايه"}


class RuleBasedUnderstander:
    def __init__(self, searcher: MenuSearcher):
        self.searcher = searcher

    # ------------------------------------------------------------------ api
    def understand(self, message: str, cart_text: str = "") -> CustomerTurn:
        tokens = self._split_waw(self.searcher.normalize(message).split())
        turn = CustomerTurn(fulfillment=intents.fulfillment_of(message))
        mentions = self.searcher.find_mentions(tokens)
        has_order_verb = any(t in ORDER_VERBS for t in tokens)
        qty = intents.quantity_in(tokens)
        asks_question = "؟" in message or "?" in message

        if any(t in REMOVE_VERBS for t in tokens):
            turn.intent = "modify"
            names = [m.item.name.strip() for m in mentions] or [""]
            turn.items = [ItemRequest(product_name=n, action="remove") for n in names]
            return turn

        if qty is not None and any(t in SET_QTY_VERBS for t in tokens):
            turn.intent = "modify"
            name = mentions[0].item.name.strip() if mentions else ""
            turn.items = [ItemRequest(product_name=name, quantity=qty, action="set_quantity")]
            return turn

        price_signal = any(t in STRONG_PRICE_WORDS for t in tokens) or ("كم" in tokens and mentions)
        if not has_order_verb and price_signal:
            turn.intent = "ask_price"
            turn.items = self._items_for(tokens, mentions)
            turn.topic = None if turn.items else self._leftover(tokens, QUESTION_NOISE)
            return turn

        starts_avail = bool(tokens) and tokens[0] in AVAIL_WORDS
        if not has_order_verb and (starts_avail or (asks_question and any(t in AVAIL_WORDS for t in tokens))):
            turn.intent = "ask_availability"
            turn.items = self._items_for(tokens, mentions)
            turn.topic = None if turn.items else self._leftover(tokens, QUESTION_NOISE)
            return turn

        if not has_order_verb and tokens and tokens[0] in MENU_QUESTION and not mentions:
            turn.intent = "ask_menu"
            turn.topic = " ".join(t for t in tokens[1:] if t not in {"عندكم", "في", "عندك"}) or None
            return turn

        items = self._items_for(tokens, mentions)
        if not items and has_order_verb:
            # "بدي بيتزا": keep the unknown name so the bot can say it is not on the menu
            unknown = self._leftover(tokens, ORDER_VERBS)
            if unknown:
                items = [ItemRequest(product_name=unknown)]
        if items:
            turn.intent = "order"
            turn.items = items
        elif turn.fulfillment:
            turn.intent = "order"
        return turn

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _leftover(tokens: list[str], noise: set[str]) -> str | None:
        skip = noise | intents.FILLERS | intents.FULFILLMENT_ONLY_WORDS | set(intents.NUMBER_WORDS)
        words = [t for t in tokens if t not in skip and not t.isdigit()]
        return " ".join(words) or None

    def _split_waw(self, tokens: list[str]) -> list[str]:
        """'وشاورما' -> 'و', 'شاورما' when the rest is a known product word."""
        out: list[str] = []
        for tok in tokens:
            if tok.startswith("و") and len(tok) > 3 and self.searcher.find_mentions([tok[1:]]):
                out += ["و", tok[1:]]
            else:
                out.append(tok)
        return out

    def _segments(self, tokens: list[str], mentions) -> list[list[str]]:
        bounds = [0]
        for prev, cur in zip(mentions, mentions[1:]):
            gap = tokens[prev.end : cur.start]
            cut = prev.end  # default: the whole gap belongs to the previous product
            if "و" in gap:
                cut = prev.end + len(gap) - gap[::-1].index("و")  # after the last "و"
            else:
                for offset in range(len(gap) - 1, -1, -1):
                    if intents.quantity_in([gap[offset]]) is not None:
                        cut = prev.end + offset
                        break
            bounds.append(cut)
        bounds.append(len(tokens))
        return [tokens[bounds[i] : bounds[i + 1]] for i in range(len(mentions))]

    def _items_for(self, tokens: list[str], mentions) -> list[ItemRequest]:
        if not mentions:
            options = [
                " ".join(tokens[i:j])
                for i in range(len(tokens))
                for j in range(i + 1, min(len(tokens), i + 3) + 1)
            ]
            item = self.searcher.product_for_choice(options)
            if item is None:
                return []
            choice_options = [o for g in choice_groups_for(item) for opt in g.options for o in [opt] if any(normalize(opt) == normalize(x) for x in options)]
            return [ItemRequest(product_name=item.name.strip(), choices=choice_options[:1])]
        items = []
        for mention, seg in zip(mentions, self._segments(tokens, mentions)):
            item = mention.item
            seg_text = " ".join(seg)
            variant = self.searcher.resolve_variant(item, seg_text)
            choices = [opt for g in choice_groups_for(item) if (opt := g.match(seg_text))]
            items.append(
                ItemRequest(
                    product_name=item.name.strip(),
                    variant_name=variant.size_type.strip() if variant else None,
                    quantity=intents.quantity_in(seg),
                    choices=choices,
                )
            )
        return items
