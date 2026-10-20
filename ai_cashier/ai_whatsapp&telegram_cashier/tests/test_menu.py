from app.services.choices import parse_choice_sentence
from app.services.menu_loader import MenuLoader
from tests.helpers import MENU_PATH, load_menu, searcher


def test_menu_loads_seven_products_and_reads_is_available():
    menu = MenuLoader(MENU_PATH).load()
    assert len(menu.items) == 7
    assert load_menu(unavailable=("زنجر",)).items[2].is_available is False  # was ignored (typo) before
    assert all(i.is_available for i in menu.items)


def test_product_search_exact_alias_and_fuzzy():
    s = searcher()
    assert s.search_product("زنجر").method == "exact"
    assert s.search_product("زنقر").item.name.strip() == "زنجر"
    assert s.search_product("شاورما").item.name.strip() == "وجبات شاورما"   # failed before (ratio < 80)
    assert s.search_product("رز").item.name.strip() == "سدر ارز"            # failed before
    assert s.search_product("زنجر بكريمة").item.name.strip() == "زنجر بكريما"
    assert s.search_product("الزنجر").item.name.strip() == "زنجر"
    assert s.search_product("بطاطا").item.name.strip() == "اضافة"
    assert s.search_product("بيتزا").found is False


def _variant(product, text):
    s = searcher()
    v = s.resolve_variant(s.search_product(product).item, text)
    return v.size_type.strip() if v else None


def test_variant_resolution_matches_only_when_clear():
    assert _variant("زنجر", "بدي 2 زنجر سوبر") == "وجبة سوبر"
    assert _variant("زنجر", "ساندويتش") == "ساندويتش"
    assert _variant("شاورما", "سوبر") == "وجبة شاورما سوبر"
    assert _variant("شاورما", "ماريا") == "وجبة شاورما ماريا"
    assert _variant("سدر ارز", "سدر دجاجة") == "سدر دجاجة"
    assert _variant("سدر ارز", "سدر عليه دجاجة ونص") == "سدر دجاجة و نصف"
    assert _variant("سدر ارز", "نص دجاجة") == "سدر نصف دجاجة"
    assert _variant("اضافة", "بطاطا وسط") == "علبة بطاطا وسط"


def test_variant_resolution_returns_none_when_ambiguous():
    assert _variant("زنجر", "وجبة") is None
    assert _variant("زنجر", "بدي زنجر") is None
    assert _variant("شاورما", "شاورما") is None
    assert _variant("سدر ارز", "وجبة") is None
    assert _variant("اضافة", "بطاطا") is None


def test_choice_groups_are_parsed_from_menu_sentences():
    s = searcher()
    rice = s.choice_groups(s.search_product("سدر ارز").item)[0]
    assert rice.options == ("زرب", "كبسة", "مندي", "اوزي", "برياني") and rice.trigger is None
    bread = s.choice_groups(s.search_product("زنجر").item)[0]
    assert bread.options == ("شراك", "عادي", "تورتيلا") and bread.trigger == "ساندويتش"
    assert bread.label == "نوع خبز"
    assert s.choice_groups(s.search_product("شاورما").item) == []
    assert parse_choice_sentence("لا يوجد اختيارات") is None


def test_choice_option_identifies_its_owner_product():
    s = searcher()
    assert s.product_for_choice(["مندي"]).name.strip() == "سدر ارز"
    assert s.product_for_choice(["شراك"]) is None  # shared by several products -> ambiguous


def test_common_zinger_typos_are_normalized_and_unrelated_suggestions_are_hidden():
    s = searcher()
    for spelling in ("زنقر", "زنغر", "سنجر"):
        result = s.search_product(spelling)
        assert result.found and result.item.name.strip() == "زنجر"
    assert s.suggest("بيتزا") == []
