"""CashierBot: sessions + understanding + order persistence.

Used by both the local CLI and the WhatsApp webhook so they behave identically.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from app.services.menu_loader import MenuLoader
from app.services.menu_searcher import MenuSearcher
from app.services.order_conversation import OrderConversation
from app.services.order_store import OrderStore
from app.services.rule_parser import RuleBasedUnderstander
from app.services.text_understander import CustomerTurn, FallbackUnderstander, LLMUnderstander

log = logging.getLogger(__name__)
ERROR_REPLY = "صار عندي خلل بسيط 🙏 ممكن تعيد رسالتك؟"


class CashierBot:
    def __init__(
        self,
        searcher: MenuSearcher,
        understander: Any,
        store: OrderStore | None = None,
        currency: str = "",
        ttl_minutes: float = 180,
        clock=time.monotonic,
    ):
        self.searcher, self.understander, self.store = searcher, understander, store
        self.currency, self.ttl, self.clock = currency, ttl_minutes * 60, clock
        self._sessions: dict[str, tuple[OrderConversation, float]] = {}

    def _conversation(self, session_id: str) -> OrderConversation:
        now = self.clock()
        entry = self._sessions.get(session_id)
        if entry is None or now - entry[1] > self.ttl:  # stale carts must not leak into a new day
            entry = (OrderConversation(self.searcher, currency=self.currency), now)
        self._sessions[session_id] = (entry[0], now)
        return entry[0]

    def reply(self, session_id: str, text: str, location: str | None = None) -> str:
        conversation = self._conversation(session_id)
        try:
            if location:
                answer = conversation.set_location(location)
            else:
                answer = conversation.handle(text, self.understander.understand)
        except Exception:  # noqa: BLE001 - never leave a customer without an answer
            log.exception("Failed to handle message for %s", session_id)
            return ERROR_REPLY
        if conversation.needs_human:
            log.warning("Customer %s asked something outside the menu: %r", session_id, text)
            conversation.needs_human = False
        if conversation.completed_order:
            order = {**conversation.completed_order, "customer_phone": session_id}
            try:
                if self.store:
                    self.store.save(order)
            except Exception:  # noqa: BLE001
                log.exception("Could not save order %s", order["id"])
                return ERROR_REPLY
            self._sessions.pop(session_id, None)  # next message starts a fresh order
        return answer


def build_default_bot() -> CashierBot:
    menu = MenuLoader(os.getenv("MENU_PATH", "data/menu.json")).load()
    searcher = MenuSearcher(menu)
    rules = RuleBasedUnderstander(searcher)
    try:
        understander = FallbackUnderstander(LLMUnderstander(menu), rules)
    except (RuntimeError, ImportError) as exc:  # no GROQ_API_KEY / package: keep working with rules only
        log.warning("%s — running with the rule-based understander only.", exc)
        understander = rules
    return CashierBot(
        searcher,
        understander,
        store=OrderStore(os.getenv("ORDERS_PATH", "data/orders.jsonl")),
        currency=os.getenv("CURRENCY_LABEL", ""),
        ttl_minutes=float(os.getenv("SESSION_TTL_MINUTES", "180")),
    )
