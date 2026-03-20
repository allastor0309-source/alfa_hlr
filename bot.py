import os
import re
import logging
import httpx
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALPHASMS_API_KEY = os.getenv("ALPHASMS_API_KEY")
ALPHASMS_URL = "https://alphasms.ua/api/json.php"

FLAG_MAP = {
    "Ukraine": "🇺🇦", "United States": "🇺🇸", "United Kingdom": "🇬🇧",
    "Germany": "🇩🇪", "France": "🇫🇷", "Poland": "🇵🇱",
    "Italy": "🇮🇹", "Spain": "🇪🇸", "Romania": "🇷🇴",
}

STATUS_MAP = {
    "DELIVERED":                   ("🟢", "Номер активний та доступний"),
    "UNDELIVERABLE":               ("🔴", "Номер недоступний (вимкнено або поза зоною)"),
    "INVALID DESTINATION ADDRESS": ("⚫", "Номер не існує"),
    "INVALID_DESTINATION_ADDRESS": ("⚫", "Номер не існує"),
    "NO ROUTE":                    ("⚠️", "Проблема маршрутизації до оператора"),
    "NO_ROUTE":                    ("⚠️", "Проблема маршрутизації до оператора"),
    "EXPIRED":                     ("⏱️", "Час запиту вичерпано"),
    "REJECTED":                    ("🔴", "Запит відхилено оператором"),
    "FILTERED":                    ("🚫", "Заблоковано фільтром"),
    "QUEUED":                      ("🕐", "В черзі, очікуйте..."),
    "ACCEPTED":                    ("🕐", "Прийнято, обробляється..."),
    "SIM FULL":                    ("📵", "Пам'ять SIM заповнена"),
    "SIM_FULL":                    ("📵", "Пам'ять SIM заповнена"),
    "UNKNOWN":                     ("⚪", "Статус невідомий"),
}

def clean_phone(text: str) -> str:
    cleaned = re.sub(r"[^\d]", "", text.strip())
    return cleaned

def flag(country_name: str) -> str:
    return FLAG_MAP.get(country_name, "🌍")

async def hlr_lookup(phone: str) -> dict:
    payload = {
        "auth": ALPHASMS_API_KEY,
        "data": [
            {
                "type": "hlr",
                "id": 1,
                "phone": int(phone)
            }
        ]
    }
    headers = {"Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(ALPHASMS_URL, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()

def format_result(response: dict, raw_input: str) -> str:
    # Перевірка загального успіху
    if not response.get("success"):
        error = response.get("error", "Невідома помилка")
        return f"❌ *Помилка API:* {error}"

    data_list = response.get("data", [])
    if not data_list:
        return f"❌ *Немає даних для номера* `{raw_input}`"

    item = data_list[0]
    if not item.get("success"):
        return f"❌ *Помилка запиту для номера* `{raw_input}`"

    data = item.get("data", {})
    phone = data.get("phone", raw_input)
    status = data.get("status", "UNKNOWN")
    ported = data.get("ported", False)
    imsi = data.get("imsi")
    network = data.get("network", {})

    # Статус
    emoji, status_text = STATUS_MAP.get(status, ("⚪", status))

    # Мережа
    origin = network.get("origin", {})
    ported_net = network.get("ported", {})

    origin_name = origin.get("name", "")
    origin_country = origin.get("country", {}).get("name", "")

    ported_name = ported_net.get("name", "") if ported else ""
    ported_country = ported_net.get("country", {}).get("name", "") if ported else ""

    lines = [
        f"{emoji} *{status_text}*\n",
        f"📞 *Номер:* `+{phone}`",
    ]

    if origin_country:
        lines.append(f"{flag(origin_country)} *Країна:* {origin_country}")

    if origin_name:
        lines.append(f"📡 *Оператор (початковий):* {origin_name}")

    if ported and ported_name:
        lines.append(f"🔄 *Перенесено до:* {ported_name}")
        if ported_country and ported_country != origin_country:
            lines.append(f"{flag(ported_country)} *Країна оператора:* {ported_country}")

    if imsi:
        lines.append(f"🪪 *IMSI:* `{imsi}`")

    return "\n".join(lines)


# --- Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Привіт! Я бот для HLR-перевірки номерів.*\n\n"
        "Надішліть номер телефону у міжнародному форматі:\n"
        "`+380991234567` або `380991234567`\n\n"
        "Я перевірю *реальний стан SIM-карти* через AlphaSMS:\n"
        "• 🟢 Активна та в мережі\n"
        "• 🟡 Поза мережею\n"
        "• ⚫ Не існує\n"
        "• 🔄 Перенесений номер (MNP)\n\n"
        "/help — довідка",
        parse_mode="Markdown"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Довідка*\n\n"
        "Надішліть номер у будь-якому форматі:\n"
        "• `+380991234567`\n"
        "• `380991234567`\n"
        "• `+1 (555) 123-4567`\n\n"
        "*Що таке HLR lookup?*\n"
        "Реальний запит до бази оператора. "
        "Показує чи активна SIM прямо зараз, оператора та факт перенесення номера.\n\n"
        "⚡ *API:* AlphaSMS",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    phone = clean_phone(text)

    if len(phone) < 7:
        await update.message.reply_text(
            "⚠️ Схоже, це не номер телефону.\n"
            "Надішліть номер, наприклад: `+380991234567`",
            parse_mode="Markdown"
        )
        return

    msg = await update.message.reply_text("🔍 Виконую HLR запит до оператора...")

    try:
        response = await hlr_lookup(phone)
        result = format_result(response, phone)
        await msg.edit_text(result, parse_mode="Markdown")
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error: {e}")
        if e.response.status_code == 401:
            await msg.edit_text("❌ Невірний API ключ AlphaSMS.")
        else:
            await msg.edit_text(f"❌ Помилка API: {e.response.status_code}. Спробуйте пізніше.")
    except httpx.TimeoutException:
        await msg.edit_text("⏱️ Запит завис. Спробуйте ще раз.")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        await msg.edit_text("❌ Сталася непередбачена помилка.")

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN не встановлено!")
    if not ALPHASMS_API_KEY:
        raise ValueError("ALPHASMS_API_KEY не встановлено!")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("AlphaSMS HLR бот запущено...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
