import logging
import sqlite3
import asyncio
import os
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# 📌 Переменные окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret")
BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL")  # Пример: https://your-bot.onrender.com
WEBHOOK_PATH = "/webhook"
DB_PATH = "bookings.db"
PORT = int(os.environ["PORT"])  # Обязательно для Render
assert BOT_TOKEN, "❌ Переменная BOT_TOKEN не установлена"
assert BASE_WEBHOOK_URL, "❌ Переменная BASE_WEBHOOK_URL не установлена"
assert PORT, "❌ Переменная PORT не установлена"

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# 🗕 Константы
ROWS = 3
SEATS_PER_ROW = 8
DATES = ["12 июня", "13 июня", "14 июня"]
SESSIONS_PER_DATE = {
    "12 июня": ["10:00", "14:00"],
    "13 июня": ["11:00", "15:00"],
    "14 июня": ["12:00", "16:00"],
}

# База данных
def init_db_sync():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                session TEXT NOT NULL,
                row INTEGER NOT NULL,
                seat INTEGER NOT NULL,
                user_id INTEGER NOT NULL
            )
        """)
        db.commit()

def get_occupied_seats_sync(date, session, row):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.execute("SELECT seat, user_id FROM bookings WHERE date=? AND session=? AND row=?", (date, session, row))
        return dict(cursor.fetchall())

def book_seat_sync(date, session, row, seat, user_id):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("INSERT INTO bookings (date, session, row, seat, user_id) VALUES (?, ?, ?, ?, ?)", (date, session, row, seat, user_id))
        db.commit()

def cancel_booking_sync(date, session, row, seat, user_id):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("DELETE FROM bookings WHERE date=? AND session=? AND row=? AND seat=? AND user_id=?", (date, session, row, seat, user_id))
        db.commit()

# Асинхронные обёртки
async def init_db():
    await asyncio.to_thread(init_db_sync)

async def get_occupied_seats(date, session, row):
    return await asyncio.to_thread(get_occupied_seats_sync, date, session, row)

async def book_seat(date, session, row, seat, user_id):
    await asyncio.to_thread(book_seat_sync, date, session, row, seat, user_id)

async def cancel_booking(date, session, row, seat, user_id):
    await asyncio.to_thread(cancel_booking_sync, date, session, row, seat, user_id)

# Хендлеры
@dp.message(Command("start"))
async def cmd_start(message: Message):
    kb = InlineKeyboardBuilder()
    for d in DATES:
        kb.button(text=d, callback_data=f"date_{d}")
    await message.answer("Выберите дату сеанса:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("date_"))
async def select_session(callback: CallbackQuery):
    _, date = callback.data.split("_", 1)
    sessions = SESSIONS_PER_DATE.get(date, [])
    kb = InlineKeyboardBuilder()
    for s in sessions:
        kb.button(text=s, callback_data=f"session_{date}_{s}")
    kb.button(text="🏠 Назад", callback_data="start")
    await callback.message.edit_text(f"Дата: {date}\nВыберите сеанс:", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "start")
async def go_start(callback: CallbackQuery):
    await cmd_start(callback.message)

@dp.callback_query(F.data.startswith("session_"))
async def select_row(callback: CallbackQuery):
    _, date, session = callback.data.split("_", 2)
    kb = InlineKeyboardBuilder()
    for row_num in range(1, ROWS + 1):
        kb.button(text=f"Ряд {row_num}", callback_data=f"row_{date}_{session}_{row_num}")
    kb.button(text="⬅️ Назад к датам", callback_data="start")
    await callback.message.edit_text(f"Дата: {date}\nСеанс: {session}\nВыберите ряд:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("row_"))
async def select_seat(callback: CallbackQuery):
    _, date, session, row_num = callback.data.split("_", 3)
    row_num = int(row_num)
    occupied = await get_occupied_seats(date, session, row_num)
    kb = InlineKeyboardBuilder()
    row_buttons = []

    for seat_num in range(1, SEATS_PER_ROW + 1):
        if seat_num in occupied:
            if occupied[seat_num] == callback.from_user.id:
                text = "🔵"
                cb_data = f"cancel_{date}_{session}_{row_num}_{seat_num}"
            else:
                text = "❌"
                cb_data = "ignore"
        else:
            text = str(seat_num)
            cb_data = f"seat_{date}_{session}_{row_num}_{seat_num}"

        row_buttons.append((text, cb_data))

    for i in range(0, len(row_buttons), 4):
        buttons = [InlineKeyboardButton(text=text, callback_data=cb_data) for text, cb_data in row_buttons[i:i+4]]
        kb.row(*buttons)

    kb.row(
        InlineKeyboardButton(text="⬅️ Назад к рядам", callback_data=f"session_{date}_{session}"),
        InlineKeyboardButton(text="🏠 Назад к датам", callback_data="start")
    )

    await callback.message.edit_text(
        f"📅 Дата: {date}\n🕒 Сеанс: {session}\n🎫 Ряд: {row_num}\n\n"
        f"🔵 — ваше место\n❌ — занято\n\nВыберите место:",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("seat_"))
async def book_seat_handler(callback: CallbackQuery):
    _, date, session, row_num, seat_num = callback.data.split("_")
    row_num = int(row_num)
    seat_num = int(seat_num)
    user_id = callback.from_user.id

    occupied = await get_occupied_seats(date, session, row_num)
    if seat_num in occupied:
        await callback.answer("Место уже занято!", show_alert=True)
        return

    await book_seat(date, session, row_num, seat_num, user_id)
    await callback.answer("Место забронировано ✅")

    # Удаляем сообщение с выбором места
    await callback.message.delete()

    # Отправляем новое сообщение с подтверждением
    await bot.send_message(
        user_id,
        f"🎟 Ваша бронь подтверждена:\n📅 {date}\n🕒 {session}\n🎫 Ряд {row_num}, место {seat_num}"
    )

@dp.callback_query(F.data.startswith("cancel_"))
async def cancel_seat(callback: CallbackQuery):
    _, date, session, row_num, seat_num = callback.data.split("_")
    row_num = int(row_num)
    seat_num = int(seat_num)
    user_id = callback.from_user.id

    await cancel_booking(date, session, row_num, seat_num, user_id)
    await callback.answer("Бронь отменена ✅")
    await bot.send_message(user_id, f"❌ Бронь отменена:\n📅 {date}\n🕒 {session}\n🎫 Ряд {row_num}, место {seat_num}")
    await select_seat(callback)

@dp.callback_query(F.data == "ignore")
async def ignore(callback: CallbackQuery):
    await callback.answer("Место занято", show_alert=True)

# 🛡 Webhook-сервер
async def on_startup(app):
    print("🚀 on_startup работает!")
    await bot.set_webhook(f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}", secret_token=WEBHOOK_SECRET)
    await init_db()

async def on_shutdown(app):
    await bot.delete_webhook()
    await bot.close()

def create_app():
    app = web.Application()
    app["bot"] = bot
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=WEBHOOK_SECRET).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    async def healthcheck(request):
        return web.Response(text="✅ Бот работает")

    app.router.add_get("/", healthcheck)

    return app

if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=PORT)
