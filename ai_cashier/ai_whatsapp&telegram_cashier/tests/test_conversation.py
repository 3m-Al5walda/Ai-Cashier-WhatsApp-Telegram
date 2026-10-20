from decimal import Decimal

from app.services.text_understander import CustomerTurn
from tests.helpers import conversation, item, say, scripted


# ------------------------------------------------ bugs found in the previous version
def test_price_question_does_not_add_to_cart():
    conv, u = conversation()
    reply = say(conv, u, "كم سعر الزنجر؟")
    assert conv.lines == []
    assert "3.00" in reply and "وجبة سوبر" in reply  # answered from menu.json


def test_availability_question_about_menu_and_non_menu_items():
    conv, u = conversation()
    assert "متوفر" in say(conv, u, "في شاورما؟") and conv.lines == []
    reply = say(conv, u, "في بيتزا؟")
    assert "ما لقيت «بيتزا»" in reply and conv.lines == []
    assert "مندي" in say(conv, u, "في مندي؟") and conv.lines == []


def test_yes_like_words_never_confirm_unless_waiting_for_confirmation():
    conv, u = conversation()
    say(conv, u, "بدي زنجر وجبة عادي")
    reply = say(conv, u, "اكيد")           # used to be treated as "confirm"
    assert conv.completed_order is None and "تم تسجيل" not in reply
    say(conv, u, "استلام")
    assert conv.awaiting_confirm is True
    assert "تم تسجيل طلبك رقم" in say(conv, u, "اكيد")   # now it is an explicit answer to "confirm?"
    assert conv.completed_order is not None


def test_item_and_delivery_in_one_message_keeps_the_item():
    conv, u = conversation()
    reply = say(conv, u, "بدي 2 زنجر سوبر توصيل")      # used to drop the item
    assert conv.fulfillment == "delivery"
    assert [(l.product_name, l.quantity, l.variant_name) for l in conv.lines] == [("زنجر", 2, "وجبة سوبر")]
    assert "عنوان" in reply


def test_sentence_with_ana_is_not_stored_as_name_and_other_product_does_not_hijack_pending_variant():
    conv, u = conversation()
    say(conv, u, "بدي زنجر")                        # variant pending
    say(conv, u, "بدي شاورما دبل")                   # used to silently change the zinger to "double"
    got = {(l.product_name, l.variant_name) for l in conv.lines}
    assert ("زنجر", None) in got and ("وجبات شاورما", "وجبة شاورما دبل") in got


def test_unavailable_item_in_menu_json_is_refused_and_hidden():
    conv, u = conversation(unavailable=("زنجر",))
    assert "غير متوفر" in say(conv, u, "بدي زنجر سوبر")
    assert conv.lines == []
    assert "زنجر:" not in say(conv, u, "المنيو")


def test_money_uses_decimal_not_float():
    conv, u = conversation()
    say(conv, u, "بدي 3 علبة مشروب")
    assert conv.lines[0].total == Decimal("0.90")
    assert "المجموع: 0.90" in conv.summary()


def test_new_order_after_confirmation_starts_clean():
    from app.bot import CashierBot
    from tests.helpers import searcher
    from app.services.rule_parser import RuleBasedUnderstander

    s = searcher()
    saved = []

    class Store:
        def save(self, order): saved.append(order)

    bot = CashierBot(s, RuleBasedUnderstander(s), store=Store())
    for text in ("بدي زنجر سوبر", "استلام", "تأكيد"):
        reply = bot.reply("962790000000", text)
    assert "تم تسجيل طلبك رقم" in reply
    assert saved[0]["customer_phone"] == "962790000000" and saved[0]["total"] == "3.00"
    assert "السلة فارغة" in bot.reply("962790000000", "ملخص الطلب")   # fresh order


def test_session_expires_after_ttl():
    from app.bot import CashierBot
    from tests.helpers import searcher
    from app.services.rule_parser import RuleBasedUnderstander

    s, now = searcher(), [0.0]
    bot = CashierBot(s, RuleBasedUnderstander(s), ttl_minutes=60, clock=lambda: now[0])
    bot.reply("1", "بدي زنجر سوبر")
    now[0] = 30 * 60
    assert "زنجر" in bot.reply("1", "ملخص الطلب")
    now[0] = 30 * 60 + 61 * 60
    assert "السلة فارغة" in bot.reply("1", "ملخص الطلب")


# ---------------------------------------------------------------- ordering flow
def test_multi_item_message_and_totals():
    conv, u = conversation()
    say(conv, u, "بدي 2 زنجر سوبر وشاورما دبل")
    assert len(conv.lines) == 2
    assert conv._total() == Decimal("9.25")


def test_variant_question_then_answer_then_bread_question():
    conv, u = conversation()
    assert "أي حجم" in say(conv, u, "بدي زنجر")
    reply = say(conv, u, "ساندويتش")
    assert "نوع خبز" in reply and "شراك" in reply
    assert "نوع خبز" in say(conv, u, "فرنسي")             # invalid option is never saved
    assert conv.lines[0].choices == {}
    say(conv, u, "شراك")
    assert conv.lines[0].choices == {"نوع خبز": "شراك"}


def test_bread_is_not_asked_for_a_meal():
    conv, u = conversation()
    reply = say(conv, u, "بدي زنجر وجبة سوبر")
    assert "خبز" not in reply and conv._first_pending() is None


def test_rice_type_is_asked_and_mandi_alone_finds_the_product():
    conv, u = conversation()
    reply = say(conv, u, "بدي سدر دجاجة")
    assert "نوع رز" in reply and "كبسة" in reply
    say(conv, u, "مندي")
    assert conv.lines[0].choices == {"نوع رز": "مندي"}
    conv2, u2 = conversation()
    reply = say(conv2, u2, "بدي مندي")
    assert conv2.lines[0].product_name == "سدر ارز" and "أي حجم" in reply
    say(conv2, u2, "سدر دجاجة ونص")
    assert conv2.lines[0].choices == {"نوع رز": "مندي"}
    assert conv2.lines[0].variant_name == "سدر دجاجة و نصف"


def test_edit_quantity_and_remove_line():
    conv, u = conversation()
    say(conv, u, "بدي زنجر سوبر")
    say(conv, u, "خليهم 3")
    assert conv.lines[0].quantity == 3
    say(conv, u, "بدي شاورما دبل")
    reply = say(conv, u, "شيل الزنجر")
    assert [l.product_name for l in conv.lines] == ["وجبات شاورما"] and "شلت" in reply


def test_same_item_twice_merges_quantities():
    conv, u = conversation()
    say(conv, u, "بدي زنجر سوبر", "بدي 2 زنجر سوبر")
    assert len(conv.lines) == 1 and conv.lines[0].quantity == 3


def test_unknown_product_is_reported_not_silently_dropped():
    conv, u = conversation()
    say(conv, u, "بدي زنجر سوبر")
    reply = say(conv, u, "بدي بيتزا")
    assert "ما لقيت «بيتزا»" in reply and len(conv.lines) == 1


def test_llm_style_turn_with_note_and_choices():
    conv, _ = conversation()
    turn = CustomerTurn(intent="order", items=[
        item("سدر ارز", "سدر دجاجة", 2, ["كبسة"], note="بدون حمص"),
        item("اضافة", "علبة مشروب غازي", 2, note="بيبسي"),
    ])
    conv.handle("...", scripted(turn))
    assert conv.lines[0].choices == {"نوع رز": "كبسة"} and conv.lines[0].note == "بدون حمص"
    assert conv.lines[1].note == "بيبسي" and conv._first_pending() is None


def test_llm_confirm_intent_is_only_honoured_when_waiting_for_confirmation():
    conv, u = conversation()
    say(conv, u, "بدي زنجر سوبر", "استلام")
    conv.handle("تمام يا معلم", scripted(CustomerTurn(intent="confirm")))
    assert conv.completed_order is not None   # we had shown the summary and asked
    conv, u = conversation()
    say(conv, u, "بدي زنجر سوبر")
    conv.handle("اوكي", scripted(CustomerTurn(intent="confirm")))
    assert conv.completed_order is None       # not waiting -> no accidental order


# ----------------------------------------------------------- delivery / confirm
def test_delivery_address_is_captured_without_keywords_and_required():
    conv, u = conversation()
    say(conv, u, "بدي زنجر سوبر", "توصيل")
    assert "عنوان التوصيل" in say(conv, u, "تأكيد")
    assert conv.completed_order is None
    say(conv, u, "الجبيهة")                      # no "شارع/عمارة" keyword needed
    assert conv.address == "الجبيهة" and conv.awaiting_confirm
    assert "تم تسجيل طلبك" in say(conv, u, "أكد الطلب")
    assert conv.completed_order["address"] == "الجبيهة"


def test_confirm_needs_complete_order():
    conv, u = conversation()
    say(conv, u, "بدي زنجر")
    reply = say(conv, u, "اكد الطلب")
    assert "قبل التأكيد" in reply and conv.completed_order is None


def test_cancel_clears_everything_and_confirm_combined_with_total_question_confirms():
    conv, u = conversation()
    say(conv, u, "بدي زنجر سوبر", "استلام")
    assert "تم تسجيل" in say(conv, u, "اكد الطلب كم المجموع؟")
    conv, u = conversation()
    say(conv, u, "بدي زنجر سوبر", "الغاء الطلب")
    assert conv.lines == [] and "السلة فارغة" in conv.summary()


def test_location_pin_sets_delivery_address():
    conv, u = conversation()
    say(conv, u, "بدي زنجر سوبر")
    conv.set_location("الدوار السابع (31.95, 35.86)")
    assert conv.fulfillment == "delivery" and conv.address.startswith("الدوار")


# ---------------------------------------------------------------- non-order chat
def test_greeting_menu_thanks_and_out_of_scope():
    conv, u = conversation()
    assert "أهلاً" in say(conv, u, "مرحبا") and conv.lines == []
    menu = say(conv, u, "بدي المنيو")
    assert "سدر ارز" in menu and "زنجر" in menu and "3.00" in menu and conv.lines == []
    assert "سناكات" in say(conv, u, "شو عندكم سناكات") and conv.lines == []
    assert "يعافيك" in say(conv, u, "يعطيك العافية")
    other = conv.handle("الدوام لحد كم؟", scripted(CustomerTurn(intent="other")))
    assert "موظف" in other and conv.needs_human


def test_price_question_while_variant_is_pending_keeps_the_pending_item():
    conv, u = conversation()
    say(conv, u, "بدي زنجر")
    reply = say(conv, u, "كم سعر الوجبة العادي؟")
    assert conv.lines[0].variant_name is None      # question != answer
    assert "نرجع لطلبك" in reply


def test_repeating_item_without_variant_increments_quantity_instead_of_ignoring_it():
    conv, u = conversation()
    reply = say(conv, u, "بدي زنجر", "بدي زنجر")
    assert len(conv.lines) == 1
    assert conv.lines[0].quantity == 2
    assert "أي حجم" in reply
