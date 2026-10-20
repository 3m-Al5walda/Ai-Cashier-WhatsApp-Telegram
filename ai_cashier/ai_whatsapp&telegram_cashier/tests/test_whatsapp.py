import hashlib
import hmac
import json

from app.services.order_store import OrderStore
from app.whatsapp_utils import extract_text, split_message, valid_signature


def _sig(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_signature_validation():
    body = b'{"entry": []}'
    assert valid_signature(body, _sig(body, "s3cret"), "s3cret")
    assert not valid_signature(body, _sig(body, "other"), "s3cret")
    assert not valid_signature(body, None, "s3cret")
    assert valid_signature(body, None, None)   # check disabled when no secret is configured


def test_split_message_keeps_lines_and_limit():
    text = "\n".join(f"line {i}" for i in range(500))
    chunks = split_message(text, limit=200)
    assert all(len(c) <= 200 for c in chunks) and "\n".join(chunks) == text
    assert split_message("hi") == ["hi"]


def test_extract_text_variants():
    assert extract_text({"type": "text", "text": {"body": " بدي زنجر "}}) == ("بدي زنجر", None)
    assert extract_text({"type": "interactive", "interactive": {"button_reply": {"title": "تأكيد"}}}) == ("تأكيد", None)
    text, location = extract_text({"type": "location", "location": {"latitude": 31.9, "longitude": 35.9, "name": "البيت"}})
    assert text == "" and "البيت" in location and "31.9" in location
    assert extract_text({"type": "image"}) == (None, None)


def test_order_store_appends_jsonl(tmp_path):
    store = OrderStore(tmp_path / "orders.jsonl", kitchen_url=None)
    store.save({"id": "A1", "total": "3.00"})
    store.save({"id": "A2", "total": "4.00"})
    rows = [json.loads(l) for l in (tmp_path / "orders.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["id"] for r in rows] == ["A1", "A2"]
