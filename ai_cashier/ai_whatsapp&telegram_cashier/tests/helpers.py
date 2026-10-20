import json
from pathlib import Path

from app.models.menu import Menu
from app.services.menu_searcher import MenuSearcher
from app.services.order_conversation import OrderConversation
from app.services.rule_parser import RuleBasedUnderstander
from app.services.text_understander import CustomerTurn, ItemRequest

MENU_PATH = Path(__file__).resolve().parents[1] / "data" / "menu.json"


def load_menu(unavailable: tuple[str, ...] = ()) -> Menu:
    data = json.loads(MENU_PATH.read_text(encoding="utf-8"))
    for item in data["items"]:
        if item["name"].strip() in unavailable:
            item["is_available"] = False
    return Menu.model_validate(data)


def searcher(unavailable: tuple[str, ...] = ()) -> MenuSearcher:
    return MenuSearcher(load_menu(unavailable))


def conversation(unavailable: tuple[str, ...] = ()):
    s = searcher(unavailable)
    return OrderConversation(s), RuleBasedUnderstander(s).understand


def say(conv, understand, *messages: str) -> str:
    reply = ""
    for message in messages:
        reply = conv.handle(message, understand)
    return reply


def scripted(turn: CustomerTurn):
    """An understander that always returns `turn` (stands in for the LLM)."""
    return lambda message, cart="": turn


def item(name, variant=None, quantity=None, choices=None, action="add", note=None):
    return ItemRequest(product_name=name, variant_name=variant, quantity=quantity,
                       choices=choices or [], action=action, note=note)
