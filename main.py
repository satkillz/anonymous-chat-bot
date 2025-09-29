import asyncio
import logging
import time
import random
from collections import defaultdict
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("MODERATION_CHANNEL_ID"))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# === ДАННЫЕ ===
users = {}  # user_id -> {own_gender, search_preference}
search_queue = set()
active_sessions = {}

# === БЕЗОПАСНОСТЬ ===
user_requests = defaultdict(list)
user_media_count = defaultdict(list)
user_actions = defaultdict(list)
captcha_challenges = {}

# === СОСТОЯНИЯ ===
class UserState(StatesGroup):
    choosing_own_gender = State()
    choosing_search_pref = State()
    in_chat = State()
    waiting_for_captcha = State()
    in_search = State()

# === КЛАВИАТУРЫ ===
def get_own_gender_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Мужчина"), KeyboardButton(text="Женщина")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_search_pref_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Только парни")],
            [KeyboardButton(text="Только девушки")],
            [KeyboardButton(text="Микс (любой)")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_search_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="/stop")]],
        resize_keyboard=True
    )

def get_chat_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/next"), KeyboardButton(text="/stop"), KeyboardButton(text="/link")]
        ],
        resize_keyboard=True
    )

def get_idle_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/search"), KeyboardButton(text="/gender")]
        ],
        resize_keyboard=True
    )

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def is_rate_limited(user_id: int) -> bool:
    now = time.time()
    user_requests[user_id] = [t for t in user_requests[user_id] if now - t < 60]
    if len(user_requests[user_id]) >= 5:
        return True
    user_requests[user_id].append(now)
    return False

def is_media_limited(user_id: int) -> bool:
    now = time.time()
    user_media_count[user_id] = [t for t in user_media_count[user_id] if now - t < 60]
    if len(user_media_count[user_id]) >= 25:
        return True
    user_media_count[user_id].append(now)
    return False

def check_for_captcha(user_id: int, action: str) -> bool:
    now = time.time()
    user_actions[user_id] = [a for a in user_actions[user_id] if now - a[1] < 600]
    user_actions[user_id].append((action, now))
    actions = [a[0] for a in user_actions[user_id]]
    return actions.count(action) >= 3

def generate_captcha(user_id: int):
    emojis = ["🍎", "🚗", "😊", "🐱", "🌈", "🍕", "🚀", "⚽", "🎮", "📚"]
    correct = random.choice(emojis)
    captcha_challenges[user_id] = correct
    options = random.sample([e for e in emojis if e != correct], 5) + [correct]
    random.shuffle(options)
    return correct, options

def get_search_text(preference: str) -> str:
    if preference == "male":
        return "парня"
    elif preference == "female":
        return "девушку"
    else:
        return "собеседника"

# === ОБРАБОТЧИКИ ===
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if user_id not in users:
        await state.set_state(UserState.choosing_own_gender)
        await message.answer("👋 Добро пожаловать!\nУкажите ваш пол:", reply_markup=get_own_gender_kb())
    else:
        await message.answer("Вы уже зарегистрированы!", reply_markup=get_idle_kb())

@dp.message(UserState.choosing_own_gender)
async def choose_own_gender(message: types.Message, state: FSMContext):
    if message.text not in ["Мужчина", "Женщина"]:
        await message.answer("Пожалуйста, выберите из кнопок.")
        return
    own_gender = "male" if message.text == "Мужчина" else "female"
    users[message.from_user.id] = {"own_gender": own_gender}
    await state.set_state(UserState.choosing_search_pref)
    await message.answer("Кого вы хотите найти?", reply_markup=get_search_pref_kb())

@dp.message(UserState.choosing_search_pref)
async def choose_search_pref(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text
    if text == "Только парни":
        users[user_id]["search_preference"] = "male"
    elif text == "Только девушки":
        users[user_id]["search_preference"] = "female"
    elif text == "Микс (любой)":
        users[user_id]["search_preference"] = "any"
    else:
        await message.answer("Пожалуйста, выберите из кнопок.")
        return
    await state.clear()
    pref_text = get_search_text(users[user_id]["search_preference"])
    await message.answer(f"✅ Готово! Теперь ищите {pref_text} через /search", reply_markup=get_idle_kb())

@dp.message(Command("gender"))
async def cmd_gender(message: types.Message, state: FSMContext):
    if is_rate_limited(message.from_user.id):
        await message.answer("❌ Слишком много запросов. Подождите 1 минуту.")
        return
    await state.set_state(UserState.choosing_search_pref)
    await message.answer("Измените предпочтения поиска:", reply_markup=get_search_pref_kb())

@dp.message(Command("search"))
async def cmd_search(message: types.Message, state: FSMContext):
    if is_rate_limited(message.from_user.id):
        await message.answer("❌ Слишком много запросов. Подождите 1 минуту.")
        return

    user_id = message.from_user.id
    if user_id not in users:
        await message.answer("Сначала пройдите регистрацию через /start")
        return
    if user_id in active_sessions:
        await message.answer("Вы уже в чате!", reply_markup=get_chat_kb())
        return
    if user_id in search_queue:
        await message.answer("Вы уже в поиске...", reply_markup=get_search_kb())
        return

    if check_for_captcha(user_id, "search"):
        correct, options = generate_captcha(user_id)
        opts_text = " ".join(options)
        await message.answer(
            f"Выберите смайлик, который вы видели в скобках: ({correct})\n"
            f"Варианты: {opts_text}",
            reply_markup=types.ReplyKeyboardRemove()
        )
        await state.set_state(UserState.waiting_for_captcha)
        return

    search_queue.add(user_id)
    await state.set_state(UserState.in_search)
    pref = users[user_id]["search_preference"]
    target_text = get_search_text(pref)
    await message.answer(f"Начат поиск 🙏, ищем 🔎 {target_text}...", reply_markup=get_search_kb())

    start_time = time.time()
    warned = False

    async def _search_task():
        nonlocal warned
        try:
            while time.time() - start_time < 300:  # 5 минут максимум
                await asyncio.sleep(0.5)
                if user_id not in search_queue:
                    return

                pref = users[user_id]["search_preference"]
                for candidate in list(search_queue):
                    if candidate == user_id or candidate in active_sessions:
                        continue
                    # Проверка по предпочтениям
                    if pref == "any":
                        match = True
                    else:
                        match = users.get(candidate, {}).get("own_gender") == pref

                    if match:
                        search_queue.discard(user_id)
                        search_queue.discard(candidate)
                        active_sessions[user_id] = candidate
                        active_sessions[candidate] = user_id
                        await state.set_state(UserState.in_chat)
                        await bot.send_message(
                            user_id,
                            "✅ Собеседник найден, хорошего общения🫶🏻\n/next - следующий собеседник\n/stop - остановить диалог",
                            reply_markup=get_chat_kb()
                        )
                        await bot.send_message(
                            candidate,
                            "✅ Собеседник найден, хорошего общения🫶🏻\n/next - следующий собеседник\n/stop - остановить диалог",
                            reply_markup=get_chat_kb()
                        )
                        return

                # Через 2 минуты без совпадения — предложить микс
                if not warned and time.time() - start_time > 120 and pref in ("male", "female"):
                    warned = True
                    await bot.send_message(
                        user_id,
                        "⚠️ Долго не удаётся найти собеседника нужного пола.\n"
                        "Хотите переключиться на поиск любого собеседника (микс)?\n"
                        "Используйте /gender, чтобы изменить настройки.",
                        reply_markup=get_idle_kb()
                    )
            # Таймаут
            if user_id in search_queue:
                search_queue.discard(user_id)
                await bot.send_message(user_id, "❌ Не удалось найти собеседника. Попробуйте позже.", reply_markup=get_idle_kb())
        except asyncio.CancelledError:
            pass

    asyncio.create_task(_search_task())

@dp.message(UserState.waiting_for_captcha)
async def handle_captcha(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    correct = captcha_challenges.get(user_id)
    if correct and message.text.strip() == correct:
        del captcha_challenges[user_id]
        await state.clear()
        await message.answer("✅ Проверка пройдена!", reply_markup=get_idle_kb())
    else:
        await message.answer("⚠️ Подозрительная активность. Доступ ограничен на 4 часа.")
        await state.clear()

@dp.message(Command("stop"))
async def cmd_stop(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    current_state = await state.get_state()

    if current_state == UserState.in_search.state:
        if user_id in search_queue:
            search_queue.discard(user_id)
        await state.clear()
        await message.answer("Поиск остановлен.", reply_markup=get_idle_kb())
        return

    if user_id in active_sessions:
        partner_id = active_sessions.pop(user_id)
        active_sessions.pop(partner_id, None)
        await bot.send_message(partner_id, "Ваш собеседник покинул чат.", reply_markup=get_idle_kb())
        await state.clear()
        await message.answer("Чат завершён.", reply_markup=get_idle_kb())
        return

    await message.answer("Вы не в поиске и не в чате.", reply_markup=get_idle_kb())

@dp.message(Command("next"))
async def cmd_next(message: types.Message, state: FSMContext):
    await cmd_stop(message, state)

@dp.message(Command("link"))
async def cmd_link(message: types.Message):
    if message.from_user.id not in active_sessions:
        await message.answer("Вы не в чате.")
        return
    if message.from_user.username:
        partner_id = active_sessions[message.from_user.id]
        await bot.send_message(partner_id, f"Собеседник поделился профилем: https://t.me/{message.from_user.username}")
        await message.answer("✅ Ссылка отправлена собеседнику.")
    else:
        await message.answer("У вас нет username в Telegram. Установите его в настройках профиля.")

@dp.message()
async def handle_chat(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    # Если пользователь в поиске — игнорируем (но лучше не попадать сюда)
    if user_id in search_queue:
        await message.answer("Вы в поиске собеседника. Подождите...", reply_markup=get_search_kb())
        return

    # Если не в активной сессии — показываем idle
    if user_id not in active_sessions:
        if not (message.text and message.text.startswith("/")):
            await message.answer("Выберите действие:", reply_markup=get_idle_kb())
        return

    # Проверка медиа
    if message.photo or message.video or message.voice or message.animation:
        if is_media_limited(user_id):
            await message.answer("❌ Лимит медиа: 25 файлов в минуту.")
            return
        partner_id = active_sessions[user_id]
        if message.photo:
            await bot.send_photo(partner_id, photo=message.photo[-1].file_id, caption=message.caption, has_spoiler=True)
        elif message.video:
            await bot.send_video(partner_id, video=message.video.file_id, caption=message.caption, has_spoiler=True)
        elif message.voice:
            await bot.send_voice(partner_id, voice=message.voice.file_id, caption=message.caption, has_spoiler=True)
        elif message.animation:
            await bot.send_animation(partner_id, animation=message.animation.file_id, caption=message.caption, has_spoiler=True)
        await bot.forward_message(CHANNEL_ID, user_id, message.message_id)
    else:
        # Текстовое сообщение
        partner_id = active_sessions[user_id]
        await bot.send_message(partner_id, message.text)
async def on_startup(bot: Bot):
    print("✅ Бот запущен!")

async def main():
    dp.startup.register(on_startup)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
