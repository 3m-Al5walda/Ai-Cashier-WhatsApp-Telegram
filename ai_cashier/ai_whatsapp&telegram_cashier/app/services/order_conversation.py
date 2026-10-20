"""Stateful, menu-backed order conversation (one instance per WhatsApp customer).

Design rules
- menu.json (through MenuSearcher) is the only source of names, variants, prices
  and options. Nothing about rice/bread/products is hard-coded here.
- The LLM/rule parser only produces a `CustomerTurn`; this class validates it.
- Questions (price, availability, menu) never touch the cart.
- Missing details are *derived* from the cart (`_first_pending`), not stored, so
  state can never get out of sync.
- Confirm/cancel are exact commands; "yes"-style words only count while the bot
  is explicitly waiting for the final confirmation.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from app.models.menu import MenuItem
from app.services import intents
from app.services.arabic import normalize, tokens_of
from app.services.choices import ChoiceGroup
from app.services.menu_searcher import MenuSearcher
from app.services.text_understander import CustomerTurn, ItemRequest

Understand = Callable[[str, str], CustomerTurn]

QUESTION_INTENTS = {"ask_price", "ask_menu", "ask_availability", "ask_info"}
HANDOFF = (
    "هالسؤال مش موجود ضمن معلومات المنيو اللي عندي، رح أحوّله للموظف ليرد عليك بأقرب وقت 🙏\n"
    "وإذا بدك تطلب احكيلي شو بدك."
)


@dataclass
class OrderLine:
    product_name: str
    variant_name: str | None = None
    unit_price: Decimal | None = None
    quantity: int = 1
    choices: dict[str, str] = field(default_factory=dict)  # group label -> option
    note: str | None = None

    @property
    def total(self) -> Decimal | None:
        return None if self.unit_price is None else self.unit_price * self.quantity


@dataclass
class OrderConversation:
    searcher: MenuSearcher
    currency: str = ""
    lines: list[OrderLine] = field(default_factory=list)
    fulfillment: str | None = None  # "delivery" | "pickup"
    address: str | None = None
    awaiting_confirm: bool = False
    completed_order: dict | None = None
    needs_human: bool = False

    # ------------------------------------------------------------ entry point
    def handle(self, message: str, understand: Understand | None = None) -> str:
        text = (message or "").strip()
        if not text:
            return "اكتب طلبك وأنا جاهز 😊"

        command = intents.command_of(text)
        if command == "cancel":
            return self._cancel()
        if command == "confirm":
            return self._confirm()
        if command == "summary":
            return self.summary()
        if self.awaiting_confirm:
            if intents.is_affirmative(text):
                return self._confirm()
            if intents.is_negative(text):
                self.awaiting_confirm = False
                return "تمام، احكيلي شو بدك تعدّل: تضيف صنف، تغيّر كمية، أو تشيل صنف 😊"
        if command == "menu":
            return self.render_menu()
        if intents.is_greeting(text):
            return "أهلاً وسهلاً فيك 😊 شو بتحب تطلب؟ (اكتب «المنيو» إذا بدك تشوف الأصناف)"
        if intents.is_thanks(text):
            return "الله يعافيك 😊 إذا بدك شي ثاني احكيلي."

        answered = self._answer_pending(text)
        if answered is not None:
            return answered
        if intents.is_affirmative(text):
            # "اكيد"/"تمام" outside a confirmation step never confirms or adds anything
            return self._after_change(["تمام 😊"]) if self.lines else "تمام 😊 شو بدك تطلب؟"

        notes: list[str] = []
        fulfillment = intents.fulfillment_of(text)
        if fulfillment:
            notes.append(self._set_fulfillment(fulfillment))
            if intents.only_fulfillment(text):
                return self._after_change(notes)
        elif self.fulfillment == "delivery" and not self.address and self._looks_like_address(text):
            self.address = text
            return self._after_change(["📍 سجلت عنوان التوصيل."])

        turn = understand(text, self.cart_text()) if understand else CustomerTurn()
        return self._apply_turn(text, turn.clean(), notes)

    def set_location(self, location: str) -> str:
        """A WhatsApp location pin / address supplied outside a text message."""
        if self.fulfillment == "pickup":
            return "اخترت الاستلام من المحل. إذا بدك توصيل اكتب «توصيل» وبسجل موقعك."
        self.fulfillment, self.address = "delivery", location
        return self._after_change(["📍 سجلت موقع التوصيل."])

    # ------------------------------------------------------------- turn logic
    def _apply_turn(self, text: str, turn: CustomerTurn, notes: list[str]) -> str:
        if turn.fulfillment and turn.fulfillment != self.fulfillment:
            notes.append(self._set_fulfillment(turn.fulfillment))
        if turn.address and turn.address.strip():
            if self.fulfillment is None:
                notes.append(self._set_fulfillment("delivery"))
            if self.fulfillment == "delivery":
                self.address = turn.address.strip()

        intent = turn.intent
        if intent == "greeting":
            return "أهلاً وسهلاً فيك 😊 شو بتحب تطلب؟"
        if intent == "thanks":
            return "الله يعافيك 😊 إذا بدك شي ثاني احكيلي."
        if intent == "summary":
            return self.summary()
        if intent == "cancel":
            return "عشان ما نلغي طلبك بالغلط، اكتب «الغاء الطلب» إذا متأكد."
        if intent == "confirm":
            if self.awaiting_confirm:
                return self._confirm()
            return self._confirm_request(notes)
        if intent in QUESTION_INTENTS:
            return self._answer_question(turn, text)

        if not turn.items:
            if notes:
                return self._after_change(notes)
            if intent == "other":
                pending = self._first_pending()
                if pending and not intents.is_question(text):
                    # most likely an invalid answer to our own question ("فرنسي" for the bread type)
                    return "\n".join(notes + ["ما فهمت اختيارك 🙏", self._pending_question(pending)])
                self.needs_human = True
                back = ("\n\n— نرجع لطلبك —\n" + self._pending_question(pending)) if pending else ""
                return HANDOFF + back
            return "ما فهمت طلبك تماماً 🙏 اكتب اسم الصنف اللي بدك إياه، أو «المنيو» لتشوف الأصناف."

        acks: list[str] = []
        problems: list[str] = []
        single = len(turn.items) == 1
        for req in turn.items:
            item = self._resolve_item(req)
            if req.action != "add":
                self._modify(req, item, acks, problems)
            elif item is None:
                problems.append(self._not_found(req.product_name or "هذا الصنف"))
            elif not item.is_available:
                problems.append(f"عذراً، {item.name.strip()} غير متوفر حالياً.")
            else:
                self._add(req, item, text if single else "", acks)
        return self._after_change(notes + acks + problems)

    def _resolve_item(self, req: ItemRequest) -> MenuItem | None:
        if req.product_name:
            found = self.searcher.search_product(req.product_name)
            if found.found:
                return found.item
        if req.choices and not req.product_name:
            return self.searcher.product_for_choice(req.choices)
        return None

    # -------------------------------------------------------------- add / edit
    def _add(self, req: ItemRequest, item: MenuItem, text: str, acks: list[str]) -> None:
        name = item.name.strip()
        variant = self.searcher.search_variant(item, req.variant_name) if req.variant_name else None
        if variant is None and text:
            variant = self.searcher.resolve_variant(item, text)
        variant_name = variant.size_type.strip() if variant else None

        chosen: dict[str, str] = {}
        for group in self.searcher.choice_groups(item):
            for raw in req.choices:
                option = group.canonical(raw) or group.match(raw)
                if option:
                    chosen[group.label] = option
            if group.label not in chosen and text and group.applies_to(variant_name):
                option = group.match(text)
                if option:
                    chosen[group.label] = option

        quantity = req.quantity or 1
        line = None
        if variant_name is None:
            line = next((l for l in reversed(self.lines) if l.product_name == name and l.variant_name is None), None)
            if line is not None:
                # Repeating an underspecified item increases its quantity, just like
                # repeating a fully specified line; it must not silently do nothing.
                line.quantity += quantity
                acks.append(f"✅ أضفت {quantity} × {name}.")
                return
        else:
            line = next(
                (l for l in self.lines if l.product_name == name and l.variant_name == variant_name
                 and l.choices == chosen and l.note == req.note),
                None,
            )
            if line is not None:
                line.quantity += quantity
                acks.append("✅ " + self._line_text(line))
                return
            # a half-finished line of the same product ("زنجر" then "زنجر سوبر") is completed, not duplicated
            line = next((l for l in reversed(self.lines) if l.product_name == name and l.variant_name is None), None)

        if line is None:
            line = OrderLine(product_name=name, quantity=quantity)
            self.lines.append(line)
        elif req.quantity:
            line.quantity = req.quantity
        if variant:
            self._set_variant(line, variant)
        line.choices.update(chosen)
        if req.note:
            line.note = req.note
        acks.append("✅ " + self._line_text(line) if line.variant_name else f"✅ أضفت {name}.")

    @staticmethod
    def _set_variant(line: OrderLine, variant) -> None:
        line.variant_name = variant.size_type.strip()
        line.unit_price = Decimal(str(variant.price))

    def _modify(self, req: ItemRequest, item: MenuItem | None, acks: list[str], problems: list[str]) -> None:
        if item is None and req.product_name:
            problems.append(self._not_found(req.product_name))
            return
        targets = self._lines_for(item, req)
        label = item.name.strip() if item else (targets[0].product_name if targets else "الصنف")
        if not targets:
            problems.append(f"ما لقيت {label} بطلبك الحالي. اكتب «ملخص الطلب» لتشوف الأصناف.")
            return
        if len(targets) > 1:
            options = "، ".join(f"{l.variant_name or 'بدون حجم'}" for l in targets)
            problems.append(f"عندك أكثر من سطر من {label} ({options}). حدّد أي حجم تقصد.")
            return
        line = targets[0]
        if req.action == "remove":
            self.lines.remove(line)
            acks.append(f"🗑️ شلت {line.product_name}" + (f" ({line.variant_name})" if line.variant_name else "") + " من الطلب.")
        elif req.quantity is None:
            problems.append("كم الكمية اللي بدك إياها؟")
        else:
            line.quantity = req.quantity
            acks.append("✅ " + self._line_text(line))

    def _lines_for(self, item: MenuItem | None, req: ItemRequest) -> list[OrderLine]:
        if item is None:
            return [self.lines[-1]] if self.lines else []
        name = item.name.strip()
        found = [l for l in self.lines if l.product_name == name]
        if req.variant_name and found:
            variant = self.searcher.search_variant(item, req.variant_name)
            if variant:
                found = [l for l in found if l.variant_name == variant.size_type.strip()]
        return found

    # ---------------------------------------------------------- pending asks
    def _first_pending(self) -> tuple[str, OrderLine, ChoiceGroup | None] | None:
        for line in self.lines:
            if line.variant_name is None:
                return ("variant", line, None)
            item = self.searcher.product_by_name(line.product_name)
            for group in self.searcher.choice_groups(item) if item else []:
                if group.applies_to(line.variant_name) and group.label not in line.choices:
                    return ("choice", line, group)
        return None

    def _pending_question(self, pending) -> str:
        kind, line, group = pending
        if kind == "variant":
            item = self.searcher.product_by_name(line.product_name)
            options = "\n".join(f"• {v.size_type.strip()} — {self._money(v.price)}" for v in item.variants)
            return f"أي حجم/نوع بدك من {line.product_name}؟\n{options}"
        return f"{group.question()} — {line.product_name}"

    def _answer_pending(self, text: str) -> str | None:
        pending = self._first_pending()
        if pending is None or intents.is_question(text):
            return None
        kind, line, group = pending
        item = self.searcher.product_by_name(line.product_name)
        # a message that names a *different* product is a new request, not an answer
        if any(m.item.name.strip() != line.product_name for m in self.searcher.find_mentions(text)):
            return None
        if kind == "variant":
            variant = self.searcher.resolve_variant(item, text)
            if variant is None:
                return None
            self._set_variant(line, variant)
            quantity = intents.quantity_in(tokens_of(text))
            if quantity:
                line.quantity = quantity
            ack = "✅ " + self._line_text(line)
        else:
            option = group.match(text)
            if option is None:
                return None
            line.choices[group.label] = option
            ack = f"✅ {group.label}: {option}"
        return self._after_change([ack])

    def _after_change(self, parts: list[str]) -> str:
        """Compose the reply after any cart/order change: acks + the next thing we need."""
        self.awaiting_confirm = False
        parts = [p for p in parts if p]
        pending = self._first_pending()
        if pending:
            parts.append(self._pending_question(pending))
        elif self.lines:
            parts.append(self._next_step())
        return "\n".join(parts)

    def _next_step(self) -> str:
        if self.fulfillment is None:
            return "بدك تضيف شي ثاني؟ وإذا خلصت احكيلي: توصيل ولا استلام من المحل؟"
        if self.fulfillment == "delivery" and not self.address:
            return "📍 ابعتلي عنوان التوصيل (أو شارك موقعك)."
        self.awaiting_confirm = True
        return self.summary() + "\nإذا كل شي تمام اكتب «تأكيد»، أو احكيلي شو بدك تعدّل."

    def _set_fulfillment(self, value: str) -> str:
        self.fulfillment = value
        if value == "pickup":
            self.address = None
            return "تمام، استلام من المحل 👍"
        return "تمام، توصيل 🛵"

    @staticmethod
    def _looks_like_address(text: str) -> bool:
        return len(text) <= 200 and "؟" not in text and "?" not in text and not intents.is_question(text)

    # ---------------------------------------------------------- confirm/cancel
    def _missing_details(self) -> list[str]:
        missing = []
        for line in self.lines:
            if line.variant_name is None:
                missing.append(f"حجم/نوع {line.product_name}")
                continue
            item = self.searcher.product_by_name(line.product_name)
            for group in self.searcher.choice_groups(item) if item else []:
                if group.applies_to(line.variant_name) and group.label not in line.choices:
                    missing.append(f"{group.label} ({line.product_name})")
        if self.fulfillment is None:
            missing.append("توصيل أو استلام من المحل")
        elif self.fulfillment == "delivery" and not self.address:
            missing.append("عنوان التوصيل")
        return missing

    def _confirm_request(self, notes: list[str]) -> str:
        if not self.lines:
            return "السلة فارغة حالياً، شو بدك تطلب؟ 😊"
        return self._after_change(notes)

    def _confirm(self) -> str:
        if not self.lines:
            return "السلة فارغة حالياً، شو بدك تطلب؟ 😊"
        missing = self._missing_details()
        if missing:
            return "قبل التأكيد أحتاج منك: " + "، ".join(missing) + "\n" + self._after_change([])
        order = self._build_order()
        self.completed_order = order
        return f"✅ تم تسجيل طلبك رقم {order['id']}.\n\n{self.summary(final=True)}\nشكراً لك 🌷"

    def _cancel(self) -> str:
        self.lines.clear()
        self.fulfillment = self.address = None
        self.awaiting_confirm = False
        return "تم إلغاء الطلب الحالي. إذا حاب تطلب من جديد أنا جاهز 😊"

    def _build_order(self) -> dict:
        total = self._total()
        return {
            "id": datetime.now().strftime("%y%m%d") + "-" + uuid.uuid4().hex[:4].upper(),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "fulfillment": self.fulfillment,
            "address": self.address,
            "currency": self.currency,
            "total": None if total is None else f"{total:.2f}",
            "lines": [
                {
                    "product": l.product_name,
                    "variant": l.variant_name,
                    "quantity": l.quantity,
                    "unit_price": f"{l.unit_price:.2f}",
                    "line_total": f"{l.total:.2f}",
                    "choices": dict(l.choices),
                    "note": l.note,
                }
                for l in self.lines
            ],
        }

    # ------------------------------------------------------------- questions
    def _answer_question(self, turn: CustomerTurn, text: str) -> str:
        parts: list[str] = []
        single = len(turn.items) == 1
        for req in turn.items:
            item = self._resolve_item(req)
            if item is None:
                parts.append(self._not_found(req.product_name or "هذا الصنف"))
            elif not item.is_available:
                parts.append(f"عذراً، {item.name.strip()} غير متوفر حالياً.")
            else:
                variant = None
                if req.variant_name:
                    variant = self.searcher.search_variant(item, req.variant_name)
                elif single:
                    variant = self.searcher.resolve_variant(item, text)
                parts.append(self._describe(item, variant, req, turn.intent))
        if not turn.items:
            parts.append(self._answer_topic((turn.topic or "").strip(), turn.intent))
        pending = self._first_pending()
        if pending:
            parts.append("— نرجع لطلبك —\n" + self._pending_question(pending))
        return "\n\n".join(p for p in parts if p)

    def _answer_topic(self, topic: str, intent: str) -> str:
        if topic:
            in_category = self._items_in_category(topic)
            if in_category:
                return self.render_menu(in_category)
            found = self.searcher.search_product(topic)
            if found.found:
                return self._describe(found.item, None, None, intent)
            if intent == "ask_menu":
                return self.render_menu()
            return self._not_found(topic)
        if intent == "ask_menu":
            return self.render_menu()
        return "أي صنف بدك تسأل عنه؟ اكتب اسمه أو «المنيو» لتشوف الأصناف."

    def _describe(self, item: MenuItem, variant, req: ItemRequest | None, intent: str) -> str:
        name = item.name.strip()
        if variant is not None:
            return f"{name} — {variant.size_type.strip()}: {self._money(variant.price)}"
        lead = ""
        groups = self.searcher.choice_groups(item)
        for group in groups:
            for raw in (req.choices if req else []):
                option = group.canonical(raw) or group.match(raw)
                if option:
                    lead = f"نعم، {option} متوفر ({group.label}) مع {name} 👌\n"
        if not lead and intent == "ask_availability":
            lead = f"نعم، {name} متوفر 👌\n"
        rows = [f"• {v.size_type.strip()} — {self._money(v.price)}" for v in item.variants]
        extras = [f"{g.label}: {'، '.join(g.options)}" + (f" (للـ{g.trigger})" if g.trigger else "") for g in groups]
        return lead + f"{name}:\n" + "\n".join(rows + extras)

    def _items_in_category(self, topic: str) -> list[MenuItem]:
        def strip(text: str) -> list[str]:
            return [t[2:] if t.startswith("ال") and len(t) > 4 else t for t in self.searcher.normalize(text).split()]

        wanted = set(strip(topic))
        return [
            i for i in self.searcher.menu.items
            if i.is_available and wanted and set(strip(i.category)) & wanted
        ]

    def _not_found(self, name: str) -> str:
        message = f"ما لقيت «{name}» بالمنيو."
        suggestions = self.searcher.suggest(name)
        if suggestions:
            return message + " يمكن تقصد: " + "، ".join(suggestions) + "؟"
        return message + " اكتب «المنيو» وبعرضلك الأصناف المتوفرة."

    def render_menu(self, items: list[MenuItem] | None = None) -> str:
        items = items if items is not None else [i for i in self.searcher.menu.items if i.is_available]
        if not items:
            return "ما قدرت أقرأ المنيو حالياً، جرّب مرة ثانية أو اسألني عن صنف محدد."
        rows, category = ["أكيد، هاي قائمتنا 😊"], None
        for item in items:
            if item.category != category:
                category = item.category
                rows.append(f"\n🍽️ {category.strip()}")
            options = "، ".join(f"{v.size_type.strip()} ({self._money(v.price)})" for v in item.variants)
            rows.append(f"• {item.name.strip()}: {options}")
        rows.append("\nاحكيلي اسم الصنف والحجم اللي بدك إياه 😊")
        return "\n".join(rows)

    # --------------------------------------------------------------- summary
    def _money(self, value) -> str:
        amount = f"{Decimal(str(value)):.2f}"
        return f"{amount} {self.currency}".strip()

    def _line_text(self, line: OrderLine) -> str:
        text = f"{line.quantity} × {line.product_name}"
        if line.variant_name:
            text += f" ({line.variant_name})"
        if line.total is not None:
            text += f" = {self._money(line.total)}"
        if line.choices:
            text += " — " + "، ".join(line.choices.values())
        return text

    def _total(self) -> Decimal | None:
        if not self.lines or any(l.total is None for l in self.lines):
            return None
        return sum((l.total for l in self.lines), Decimal("0"))

    def cart_text(self) -> str:
        if not self.lines:
            return "فارغة"
        return "\n".join(f"{i}. {self._line_text(l)}" for i, l in enumerate(self.lines, 1))

    def summary(self, final: bool = False) -> str:
        if not self.lines:
            return "السلة فارغة حالياً."
        rows = ["🧾 ملخص طلبك:"]
        for i, line in enumerate(self.lines, 1):
            if line.total is None:
                rows.append(f"{i}. {line.product_name} × {line.quantity} — السعر بعد تحديد الحجم")
            else:
                rows.append(f"{i}. {line.product_name} — {line.variant_name} × {line.quantity} = {self._money(line.total)}")
            if line.choices:
                rows.append("   " + "، ".join(f"{k}: {v}" for k, v in line.choices.items()))
            if line.note:
                rows.append(f"   ملاحظة: {line.note}")
        total = self._total()
        if total is not None:
            rows.append(f"المجموع: {self._money(total)}")
            if self.fulfillment == "delivery":
                rows.append("(رسوم التوصيل غير مضمّنة بالمجموع)")
        way = {"delivery": "توصيل", "pickup": "استلام من المحل"}.get(self.fulfillment or "", "لم تُحدد بعد")
        rows.append("طريقة الاستلام: " + way)
        if self.address:
            rows.append("العنوان: " + self.address)
        return "\n".join(rows)
