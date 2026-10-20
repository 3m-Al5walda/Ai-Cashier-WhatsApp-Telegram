import json

from app.services.rule_parser import RuleBasedUnderstander
from app.services.text_understander import (
    CustomerTurn, FallbackUnderstander, ItemRequest, LLMUnderstander,
)
from tests.helpers import load_menu, searcher


class FakeLLM:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.messages = response, error, None

    def invoke(self, messages):
        self.messages = messages
        if self.error:
            raise self.error
        return self.response


def test_llm_understander_cleans_null_lists_and_prompt_has_menu_but_no_prices():
    turn = CustomerTurn(intent="order", items=[ItemRequest(product_name="سدر ارز", choices=None, quantity=0)])
    llm = FakeLLM(turn)
    result = LLMUnderstander(load_menu(), llm).understand("بدي مندي", cart_text="فارغة")
    assert result.items[0].choices == [] and result.items[0].quantity is None
    prompt = llm.messages[0][1]
    assert "سدر ارز" in prompt and "مندي" in prompt and "نوع خبز" in prompt
    assert "7.0" not in prompt and "14.0" not in prompt          # the LLM never sees/quotes prices
    assert llm.messages[1] == ("human", "بدي مندي")


def test_llm_failure_falls_back_to_rules():
    rules = RuleBasedUnderstander(searcher())
    understander = FallbackUnderstander(LLMUnderstander(load_menu(), FakeLLM(error=TimeoutError())), rules)
    turn = understander.understand("بدي 2 زنجر سوبر")
    assert turn.items[0].product_name == "زنجر" and turn.items[0].quantity == 2


def test_rule_parser_basic_intents():
    r = RuleBasedUnderstander(searcher())
    assert r.understand("كم سعر الزنجر؟").intent == "ask_price"
    assert r.understand("في مندي؟").intent == "ask_availability"
    assert r.understand("شو عندكم سناكات").topic == "سناكات"
    assert r.understand("بدي زنجر سوبر و 3 شاورما ماريا").items[1].quantity == 3
    assert r.understand("الدوام لحد كم؟").intent == "other"
    turn = r.understand("بدي 2 زنجر سوبر وشاورما دبل توصيل")
    assert turn.fulfillment == "delivery" and len(turn.items) == 2
