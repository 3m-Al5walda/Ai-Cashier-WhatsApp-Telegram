"""Local CLI for development:  python -m app.main"""
import logging

from dotenv import load_dotenv

from app.bot import build_default_bot


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.WARNING)
    bot = build_default_bot()
    print("AI WhatsApp Cashier — وضع الاختبار المحلي")
    print("اكتب رسالة كأنك الزبون. أوامر مفيدة: المنيو | ملخص الطلب | تأكيد | الغاء الطلب | exit")
    while True:
        message = input("الزبون: ").strip()
        if message.casefold() in {"exit", "quit", "خروج"}:
            break
        if message:
            print(bot.reply("cli-session", message))


if __name__ == "__main__":
    main()
