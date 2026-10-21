"""MOKA demo ordering bot. No payment or real cafe order processing."""

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.exceptions import TelegramAPIError


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = Path(os.getenv("DB_PATH", str(Path(__file__).with_name("moka_orders.sqlite3"))))

ADMIN_CHAT_ID = None
admin_id_value = os.getenv("ADMIN_CHAT_ID", "").strip()
if admin_id_value:
    try:
        ADMIN_CHAT_ID = int(admin_id_value)
    except ValueError:
        logging.error("ADMIN_CHAT_ID має бути числовим Telegram ID. Пересилання адміністратору вимкнено.")

PRODUCTS = {
    "espresso": {"name": "Еспресо", "price": 55, "category": "coffee"},
    "cappuccino": {"name": "Капучино", "price": 80, "category": "coffee"},
    "flat_white": {"name": "Флет-вайт", "price": 90, "category": "coffee"},
    "matcha": {"name": "Матча-лате", "price": 105, "category": "other"},
    "cocoa": {"name": "Какао", "price": 75, "category": "other"},
    "croissant": {"name": "Круасан", "price": 65, "category": "bakery"},
}
CATEGORIES = {
    "coffee": "☕ Кава",
    "other": "🍵 Інші напої",
    "bakery": "🥐 До кави",
}

dp = Dispatcher()
user_carts: dict[int, dict[str, int]] = {}


def init_database() -> None:
    """Create a small local database for demo order records."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS demo_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                items_json TEXT NOT NULL,
                total_uah INTEGER NOT NULL,
                admin_notified INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def save_order(cart: dict[str, int]) -> tuple[int, int, str]:
    """Save selected demo items; returns order number, total, and item summary."""
    total = sum(PRODUCTS[item_id]["price"] * quantity for item_id, quantity in cart.items())
    items = [
        {
            "name": PRODUCTS[item_id]["name"],
            "quantity": quantity,
            "price": PRODUCTS[item_id]["price"],
        }
        for item_id, quantity in cart.items()
    ]
    summary = "\n".join(
        f"• {item['name']} × {item['quantity']} — {item['price'] * item['quantity']} ₴"
        for item in items
    )
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            "INSERT INTO demo_orders (created_at, items_json, total_uah) VALUES (?, ?, ?)",
            (created_at, json.dumps(items, ensure_ascii=False), total),
        )
        order_id = int(cursor.lastrowid)
    return order_id, total, summary


def mark_admin_notified(order_id: int) -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("UPDATE demo_orders SET admin_notified = 1 WHERE id = ?", (order_id,))


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Відкрити меню", callback_data="menu")],
        [InlineKeyboardButton(text="Переглянути кошик", callback_data="cart")],
    ])


def category_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"cat:{category}")]
        for category, label in CATEGORIES.items()
    ]
    rows.append([InlineKeyboardButton(text="🧺 Кошик", callback_data="cart")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_keyboard(category: str) -> InlineKeyboardMarkup:
    rows = []
    for item_id, item in PRODUCTS.items():
        if item["category"] == category:
            rows.append([
                InlineKeyboardButton(
                    text=f"＋ {item['name']} · {item['price']} ₴",
                    callback_data=f"add:{item_id}",
                )
            ])
    rows.append([
        InlineKeyboardButton(text="← Категорії", callback_data="menu"),
        InlineKeyboardButton(text="🧺 Кошик", callback_data="cart"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cart_keyboard(has_items: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_items:
        rows.append([
            InlineKeyboardButton(text="✅ Підтвердити демо", callback_data="order:confirm"),
            InlineKeyboardButton(text="Очистити", callback_data="cart:clear"),
        ])
    rows.append([InlineKeyboardButton(text="← До меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def category_text(category: str) -> str:
    return (
        f"{CATEGORIES[category]}\n\n"
        "Обери позицію, щоб додати її до демонстраційного кошика.\n"
        "Ціни наведені лише для навчального прикладу."
    )


def cart_text(user_id: int) -> tuple[str, bool]:
    cart = user_carts.get(user_id, {})
    if not cart:
        return "Кошик поки порожній. Відкрий меню та додай позиції.", False

    lines = []
    total = 0
    for item_id, quantity in cart.items():
        item = PRODUCTS[item_id]
        item_total = item["price"] * quantity
        total += item_total
        lines.append(f"• {item['name']} × {quantity} — {item_total} ₴")

    text = (
        "Твій демонстраційний кошик:\n\n"
        + "\n".join(lines)
        + f"\n\nРазом: {total} ₴\n\nЦе тестове замовлення без оплати."
    )
    return text, True


async def show_menu(message: Message) -> None:
    await message.answer(
        "Меню MOKA\n\nНавчальний демопроєкт: замовлення не є реальними, оплата не приймається.",
        reply_markup=category_keyboard(),
    )


@dp.message(CommandStart())
async def start(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1] == "menu":
        await show_menu(message)
        return

    await message.answer(
        "Привіт! Це MOKA — навчальний концепт Telegram-бота кав’ярні.\n\n"
        "Тут можна переглянути тестове меню, зібрати кошик і пройти демонстраційне підтвердження. "
        "Реальні замовлення та оплата не приймаються.",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("menu"))
async def menu_command(message: Message) -> None:
    await show_menu(message)


@dp.callback_query(F.data == "menu")
async def open_categories(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.edit_text(
            "Обери категорію меню. Позиції та ціни наведені для демо.",
            reply_markup=category_keyboard(),
        )
    await callback.answer()


@dp.callback_query(F.data.startswith("cat:"))
async def open_category(callback: CallbackQuery) -> None:
    category = callback.data.split(":", maxsplit=1)[1]
    if category not in CATEGORIES:
        await callback.answer("Категорію не знайдено.", show_alert=True)
        return
    if callback.message:
        await callback.message.edit_text(category_text(category), reply_markup=product_keyboard(category))
    await callback.answer()


@dp.callback_query(F.data.startswith("add:"))
async def add_to_cart(callback: CallbackQuery) -> None:
    item_id = callback.data.split(":", maxsplit=1)[1]
    if item_id not in PRODUCTS:
        await callback.answer("Позицію не знайдено.", show_alert=True)
        return
    user_id = callback.from_user.id
    cart = user_carts.setdefault(user_id, {})
    cart[item_id] = cart.get(item_id, 0) + 1
    await callback.answer(f"Додано: {PRODUCTS[item_id]['name']}")


@dp.callback_query(F.data == "cart")
async def show_cart(callback: CallbackQuery) -> None:
    text, has_items = cart_text(callback.from_user.id)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=cart_keyboard(has_items))
    await callback.answer()


@dp.callback_query(F.data == "cart:clear")
async def clear_cart(callback: CallbackQuery) -> None:
    user_carts.pop(callback.from_user.id, None)
    if callback.message:
        await callback.message.edit_text(
            "Кошик очищено. Це демо — реальне замовлення не створювалося.",
            reply_markup=cart_keyboard(False),
        )
    await callback.answer("Кошик очищено")


@dp.callback_query(F.data == "order:confirm")
async def confirm_demo_order(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    cart = user_carts.get(user_id, {})
    if not cart:
        if callback.message:
            await callback.message.edit_text("Кошик порожній. Спочатку обери позиції в меню.", reply_markup=main_keyboard())
        await callback.answer()
        return

    order_id, total, summary = save_order(cart)
    user_carts.pop(user_id, None)

    if ADMIN_CHAT_ID is None:
        response = (
            f"Демо-замовлення №{order_id} сформовано:\n\n{summary}\n\n"
            f"Разом: {total} ₴\n\n"
            "Це навчальний тест: адміністратору нічого не надіслано, бо ADMIN_CHAT_ID не налаштовано. "
            "Реального замовлення немає."
        )
    else:
        admin_message = (
            f"MOKA · ДЕМО-ЗАМОВЛЕННЯ №{order_id}\n\n{summary}\n\nРазом: {total} ₴\n"
            "Це тестове замовлення, оплата не приймалася."
        )
        try:
            await callback.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_message)
            mark_admin_notified(order_id)
            response = (
                f"Демо-замовлення №{order_id} надіслано адміністратору.\n\n"
                f"{summary}\n\nРазом: {total} ₴\n\nРеальної покупки та оплати немає."
            )
        except TelegramAPIError:
            logging.exception("Не вдалося надіслати демо-замовлення №%s адміністратору", order_id)
            response = (
                f"Демо-замовлення №{order_id} збережено в локальній базі, але адміністратору його не доставлено.\n\n"
                "Перевір ADMIN_CHAT_ID і переконайся, що адміністратор відкрив бота командою /start. "
                "Реального замовлення та оплати немає."
            )

    if callback.message:
        await callback.message.edit_text(response, reply_markup=main_keyboard())
    await callback.answer("Демо-сценарій завершено")


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задано BOT_TOKEN у змінних середовища. Створи окремого бота та додай його токен у налаштування хостингу.")

    init_database()
    bot = Bot(token=BOT_TOKEN)
    logging.info("MOKA demo bot is starting")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("MOKA demo bot stopped")
