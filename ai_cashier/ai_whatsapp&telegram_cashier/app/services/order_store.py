"""Persist confirmed orders. Local JSONL file always; kitchen webhook optional."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


class OrderStore:
    def __init__(self, path: str | Path = "data/orders.jsonl", kitchen_url: str | None = None):
        self.path = Path(path)
        self.kitchen_url = kitchen_url or os.getenv("KITCHEN_WEBHOOK_URL") or None

    def save(self, order: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(order, ensure_ascii=False) + "\n")
        if self.kitchen_url:
            self._notify_kitchen(order)

    def _notify_kitchen(self, order: dict) -> None:
        try:
            import httpx

            httpx.post(self.kitchen_url, json=order, timeout=10).raise_for_status()
        except Exception as exc:  # noqa: BLE001 - the order is already saved locally
            log.error("Kitchen notification failed for order %s: %s", order.get("id"), exc)
