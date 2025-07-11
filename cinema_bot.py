import logging
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
import asyncio

BOT_TOKEN = "7602655211:AAEbUa1-rPtIe-JZ02sBW9wT0PZ_WzJF2Wk"
DB_PATH = "bookings.db"

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

ROWS = 3
SEATS_PER_ROW = 8

# Пример дат и сеансов
DATES = ["12 июня", "13 июня", "14 июня"]
SESSIONS_PER_DATE = {
    "12 июня": ["10:00", "14:00"],
    "13 июня": ["11:00", "15:00"],
    "14 июня": ["12:00", "16:00"],
}

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                session TEXT NOT NULL,
                row INTEGER NOT NULL,
                seat INTEGER NOT NULL,
                user_id INTEGER NOT NULL
            )
        """)
        await db.commit()

async def get_occupied_seats(date: str, session: str, row: int) -> dict[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT seat, user_id FROM bookings WHERE date = ? AND session = ? AND row = ?",
            (date, session, row)
        )
        rows = await cursor.fetchall()
        return {seat: user_id for seat, user_id in rows}

async def book_seat(date: str, session: str, row: int, seat: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO bookings (date, session, row, seat, user_id) VALUES (?, ?, ?, ?, ?)",
            (date, session, row, seat, user_id)
        )
        await db.commit()

async def cancel_booking(date: str, session: str, row: int, seat: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM bookings WHERE date = ? AND session = ? AND row = ? AND seat = ? AND user_id = ?",
            (date, session, row, seat, user_id)
        )
        await db.commit()

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

    for seat_num in range(1, SEATS_PER_ROW + 1):
        if seat_num in occupied:
            if occupied[seat_num] == callback.from_user.id:
                kb.button(
                    text=f"🔵",
                    callback_data=f"cancel_{date}_{session}_{row_num}_{seat_num}"
                )
            else:
                kb.button(
                    text=f"❌",
                    callback_data="ignore"
                )
        else:
            kb.button(
                text=f"{seat_num}",
                callback_data=f"seat_{date}_{session}_{row_num}_{seat_num}"
            )

    kb.button(text="⬅️ Назад к рядам", callback_data=f"session_{date}_{session}")
    kb.button(text="🏠 Назад к датам", callback_data="start")

    await callback.message.edit_text(
        f"Дата: {date}\nСеанс: {session}\nРяд: {row_num}\nВыберите место:",
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
    await callback.answer("Место забронировано ✅", show_alert=False)

    # 📩 Отправка информации о бронировании
    await bot.send_message(
        user_id,
        f"🎟 Ваша бронь:\n\n"
        f"📅 Дата: {date}\n"
        f"🕒 Сеанс: {session}\n"
        f"🎫 Ряд: {row_num}\n"
        f"💺 Место: {seat_num}"
    )

    await select_seat(callback)


@dp.callback_query(F.data.startswith("cancel_"))
async def cancel_seat(callback: CallbackQuery):
    _, date, session, row_num, seat_num = callback.data.split("_")
    row_num = int(row_num)
    seat_num = int(seat_num)
    user_id = callback.from_user.id

    await cancel_booking(date, session, row_num, seat_num, user_id)
    await callback.answer("Бронирование отменено ✅", show_alert=False)

    # 📨 Отправка подтверждения отмены
    await bot.send_message(
        user_id,
        f"❌ Ваша бронь отменена:\n\n"
        f"📅 Дата: {date}\n"
        f"🕒 Сеанс: {session}\n"
        f"🎫 Ряд: {row_num}\n"
        f"💺 Место: {seat_num}"
    )

    await select_seat(callback)

@dp.callback_query(F.data == "ignore")
async def ignore_handler(callback: CallbackQuery):
    await callback.answer("Место занято", show_alert=True)

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
