import asyncio
import logging
import time
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv
import os

# Загружаем .env
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("MODERATION_CHANNEL_ID"))

# Включаем логирование
logging.basicConfig(level=logging.INFO)

# Инициализация
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Глобальные данные (в памяти — для MVP)
users = {}  # user_id -> {gender, interests, state, session_start_time, ...}
search_queue = []  # список user_id в поиске
active_sessions = {}  # user_id -> partner_id

# Состояния
class UserState(StatesGroup):
    choosing_gender = State()
    choosing_interests = State()
    in_chat = State()
    waiting_for_captcha = State()

# Категории
CATEGORIES = ["аниме", "книги", "спорт", "школа", "депрессия", "отношения"]

# Клавиатуры
def get_gender_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Мужской"), KeyboardButton(text="Женский")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_interests_kb():
    buttons = [[KeyboardButton(text=cat)] for cat in CATEGORIES]
    buttons.append([KeyboardButton(text="Пропустить")])
    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_chat_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/next"), KeyboardButton(text="/stop"), KeyboardButton(text="/link")]
        ],
        resize_keyboard=True
    )

# Команда /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await state.set_state(UserState.choosing_gender)
    await message.answer("Добро пожаловать! Выберите ваш пол:", reply_markup=get_gender_kb())

# Выбор пола
@dp.message(UserState.choosing_gender)
async def choose_gender(message: types.Message, state: FSMContext):
    if message.text not in ["Мужской", "Женский"]:
        await message.answer("Пожалуйста, выберите пол из кнопок.")
        return
    users[message.from_user.id] = {
        "gender": "male" if message.text == "Мужской" else "female",
        "interests": [],
        "session_start_time": None
    }
    await state.set_state(UserState.choosing_interests)
    await message.answer("Выберите категорию общения:", reply_markup=get_interests_kb())

# Выбор интересов
@dp.message(UserState.choosing_interests)
async def choose_interests(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if message.text in CATEGORIES:
        users[user_id]["interests"] = [message.text]
    # Если "Пропустить" или что-то другое — оставляем пустой список
    await state.clear()
    await message.answer(
        "Готово! Используйте /search, чтобы найти собеседника.\n"
        "Или /interes, чтобы изменить категорию."
    )

# Команда /interes
@dp.message(Command("interes"))
async def cmd_interes(message: types.Message, state: FSMContext):
    await state.set_state(UserState.choosing_interests)
    await message.answer("Выберите категорию общения:", reply_markup=get_interests_kb())

# Команда /search
@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    user_id = message.from_user.id
    if user_id in active_sessions:
        await message.answer("Вы уже в чате! Используйте /next или /stop.")
        return
    if user_id in search_queue:
        await message.answer("Вы уже в поиске...")
        return

    search_queue.append(user_id)
    await message.answer("🔍 Поиск собеседника... (макс. 5 минут)")

    # Простой подбор: ищем в течение 5 минут
    start_time = time.time()
    while time.time() - start_time < 300:  # 5 минут
        await asyncio.sleep(0.5)
        # Ищем партнёра
        for candidate in search_queue:
            if candidate != user_id and candidate not in active_sessions:
                # Проверяем пол (пока просто не совпадает)
                if users.get(user_id, {}).get("gender") != users.get(candidate, {}).get("gender"):
                    # Нашли пару!
                    search_queue.remove(user_id)
                    if candidate in search_queue:
                        search_queue.remove(candidate)
                    active_sessions[user_id] = candidate
                    active_sessions[candidate] = user_id
                    users[user_id]["session_start_time"] = time.time()
                    users[candidate]["session_start_time"] = time.time()
                    await bot.send_message(user_id, "✅ Собеседник найден! Общайтесь.", reply_markup=get_chat_kb())
                    await bot.send_message(candidate, "✅ Собеседник найден! Общайтесь.", reply_markup=get_chat_kb())
                    return
        # Если нет подходящего — ждём
    # Таймаут
    if user_id in search_queue:
        search_queue.remove(user_id)
    await message.answer("❌ Не удалось найти собеседника. Попробуйте позже.")

# Обработка сообщений в чате
@dp.message()
async def handle_chat(message: types.Message):
    user_id = message.from_user.id
    if user_id not in active_sessions:
        if not (message.text and message.text.startswith("/")):
            await message.answer("Используйте /search, чтобы начать общение.")
        return

    partner_id = active_sessions[user_id]
    current_time = time.time()
    session_start = users[user_id]["session_start_time"]

    # Проверка 15 секунд для медиа
    if message.photo or message.video or message.voice or message.animation:
        if current_time - session_start < 15:
            await message.answer("❌ Отправлять медиа можно только через 15 секунд после начала общения.")
            return
        # Отправляем под спойлером
        if message.photo:
            await bot.send_photo(
                partner_id,
                photo=message.photo[-1].file_id,
                caption=message.caption,
                has_spoiler=True
            )
        elif message.video:
            await bot.send_video(
                partner_id,
                video=message.video.file_id,
                caption=message.caption,
                has_spoiler=True
            )
        elif message.voice:
            await bot.send_voice(
                partner_id,
                voice=message.voice.file_id,
                caption=message.caption,
                has_spoiler=True  # голосовые тоже под спойлер
            )
        elif message.animation:
            await bot.send_animation(
                partner_id,
                animation=message.animation.file_id,
                caption=message.caption,
                has_spoiler=True
            )
        # Пересылка в канал (без спойлера!)
        await bot.forward_message(CHANNEL_ID, user_id, message.message_id)
    else:
        # Текст
        await bot.send_message(partner_id, message.text)
        # Текст не пересылаем в канал (только медиа)

# Команда /next
@dp.message(Command("next"))
async def cmd_next(message: types.Message):
    user_id = message.from_user.id
    if user_id not in active_sessions:
        await message.answer("Вы не в чате.")
        return
    partner_id = active_sessions.pop(user_id)
    active_sessions.pop(partner_id, None)
    await bot.send_message(partner_id, "Ваш собеседник покинул чат. Начать поиск нового? Используйте /search.")
    await message.answer("Чат завершён. Используйте /search для нового поиска.")

# Команда /stop
@dp.message(Command("stop"))
async def cmd_stop(message: types.Message):
    await cmd_next(message)  # та же логика

# Команда /link
@dp.message(Command("link"))
async def cmd_link(message: types.Message):
    # Пока просто отправляем username, если есть
    if message.from_user.username:
        partner_id = active_sessions.get(message.from_user.id)
        if partner_id:
            await bot.send_message(partner_id, f"Собеседник поделился профилем: https://t.me/{message.from_user.username}")
            await message.answer("✅ Ссылка отправлена собеседнику.")
        else:
            await message.answer("Вы не в чате.")
    else:
        await message.answer("У вас нет username в Telegram. Установите его в настройках профиля.")

# Запуск
async def on_startup(bot: Bot):
    print("✅ Бот запущен!")

async def main():
    dp.startup.register(on_startup)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
