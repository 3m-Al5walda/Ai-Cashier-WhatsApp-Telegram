"""Pure helpers for the WhatsApp webhook (no FastAPI import, easy to unit-test)."""
from __future__ import annotations

import hashlib
import hmac


def valid_signature(raw_body: bytes, header: str | None, app_secret: str | None) -> bool:
    """Verify Meta's X-Hub-Signature-256. If no secret is configured, skip the check."""
    if not app_secret:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def split_message(text: str, limit: int = 3800) -> list[str]:
    """Split a long reply on line breaks so each WhatsApp message stays under the limit."""
    chunks, current = [], ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks or [""]


def extract_text(message: dict) -> tuple[str | None, str | None]:
    """Return (text, location) for an incoming WhatsApp message, or (None, None) if unsupported."""
    kind = message.get("type")
    if kind == "text":
        return (message.get("text", {}).get("body") or "").strip(), None
    if kind == "interactive":
        reply = message.get("interactive", {})
        picked = reply.get("button_reply") or reply.get("list_reply") or {}
        return (picked.get("title") or "").strip(), None
    if kind == "location":
        loc = message.get("location", {})
        parts = [loc.get("name"), loc.get("address")]
        if loc.get("latitude") is not None and loc.get("longitude") is not None:
            parts.append(f"({loc['latitude']}, {loc['longitude']})")
        return "", " ".join(p for p in parts if p) or None
    return None, None
