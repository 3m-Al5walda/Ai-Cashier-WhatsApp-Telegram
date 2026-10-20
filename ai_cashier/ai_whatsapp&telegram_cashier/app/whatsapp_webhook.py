"""Meta WhatsApp Cloud API webhook.

Run:  uvicorn app.whatsapp_webhook:app --host 0.0.0.0 --port 8000

Production notes
- Set WHATSAPP_APP_SECRET so every request's X-Hub-Signature-256 is verified.
- Meta expects a fast 200: messages are processed in the background, the (blocking)
  LLM call runs in a worker thread, and duplicates (Meta retries) are ignored by message id.
- Sessions live in memory (one process). Use Redis before running several workers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import OrderedDict, defaultdict

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response

from app.bot import CashierBot, build_default_bot
from app.whatsapp_utils import extract_text, split_message, valid_signature

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("whatsapp")

app = FastAPI(title="AI WhatsApp Cashier", version="0.3.0")
_bot: CashierBot | None = None
_seen: "OrderedDict[str, None]" = OrderedDict()
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
UNSUPPORTED = "حالياً بقدر أستقبل رسائل نصية فقط 🙏 اكتب طلبك أو «المنيو»."


def get_bot() -> CashierBot:
    global _bot
    if _bot is None:
        _bot = build_default_bot()
    return _bot


def _already_seen(message_id: str | None) -> bool:
    if not message_id:
        return False
    if message_id in _seen:
        return True
    _seen[message_id] = None
    while len(_seen) > 5000:
        _seen.popitem(last=False)
    return False


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "whatsapp_configured": bool(os.getenv("WHATSAPP_ACCESS_TOKEN") and os.getenv("WHATSAPP_PHONE_NUMBER_ID")),
        "signature_check": bool(os.getenv("WHATSAPP_APP_SECRET")),
    }


@app.get("/webhook")
async def verify_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    expected = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    if mode == "subscribe" and expected and verify_token == expected and challenge:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Webhook verification failed")


async def _send_text(to: str, body: str) -> None:
    token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    version = os.getenv("WHATSAPP_API_VERSION", "v23.0")
    if not token or not phone_id:
        log.error("WhatsApp credentials are missing; cannot reply.")
        return
    url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=20) as client:
        for chunk in split_message(body):
            payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": chunk}}
            try:
                (await client.post(url, headers=headers, json=payload)).raise_for_status()
            except httpx.HTTPError as exc:
                log.error("Sending WhatsApp message failed: %s", exc)
                return


async def _process_message(message: dict) -> None:
    sender = message.get("from")
    if not sender:
        return
    text, location = extract_text(message)
    if text is None:
        await _send_text(sender, UNSUPPORTED)
        return
    try:
        async with _locks[sender]:  # one message at a time per customer
            reply = await asyncio.to_thread(get_bot().reply, sender, text, location)
    except Exception:  # noqa: BLE001
        log.exception("Processing failed for %s", sender)
        reply = "صار عندي خلل بسيط 🙏 ممكن تعيد رسالتك؟"
    await _send_text(sender, reply)


@app.post("/webhook")
async def receive_webhook(request: Request, background: BackgroundTasks):
    raw = await request.body()
    if not valid_signature(raw, request.headers.get("X-Hub-Signature-256"), os.getenv("WHATSAPP_APP_SECRET")):
        raise HTTPException(status_code=403, detail="Invalid signature")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON") from None
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for message in change.get("value", {}).get("messages", []):
                if not _already_seen(message.get("id")):
                    background.add_task(_process_message, message)
    return {"status": "received"}
