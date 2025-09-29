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
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("MODERATION_CHANNEL_ID"))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# === ДАННЫЕ ===
users = {}  # user_id -> {own_gender, search_preference, banned_until}
search_queue = set()
active_sessions = {}

# === БЕЗОПАСНОСТЬ ===
user_command_count = defaultdict(list)  # для 30 команд/мин
user_captcha_attempts = defaultdict(int)  # неудачные попытки
captcha_challenges = {}  # user_id -> правильный смайлик

# === СОСТОЯНИЯ ===
class UserState(StatesGroup):
    choosing_own_gender = State()
    choosing_search_pref = State()
    in_chat = State()
    waiting_for_captcha = State()
    in_search = State()
    confirming_link = State()

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

def get_link_confirm_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="link_confirm_yes")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="link_confirm_no")]
    ])

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def is_banned(user_id: int) -> bool:
    banned_until = users.get(user_id, {}).get("banned_until", 0)
    if time.time() < banned_until:
        return True
    return False

def get_ban_time_left(user_id: int) -> str:
    banned_until = users[user_id]["banned_until"]
    seconds = int(banned_until - time.time())
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}ч {minutes}мин"

def is_rate_limited(user_id: int) -> bool:
    if is_banned(user_id):
        return True
    now = time.time()
    user_command_count[user_id] = [t for t in user_command_count[user_id] if now - t < 60]
    if len(user_command_count[user_id]) >= 30:
        return True
    user_command_count[user_id].append(now)
    return False

def trigger_captcha(user_id: int):
    user_captcha_attempts[user_id] = 0
    emojis = ["🍎", "🚗", "😊", "🐱", "🌈", "🍕", "🚀", "⚽", "🎮", "📚"]
    correct = random.choice(emojis)
    captcha_challenges[user_id] = correct
    options = random.sample([e for e in emojis if e != correct], 5) + [correct]
    random.shuffle(options)
    return correct, options

def ban_user(user_id: int, hours: int = 4):
    users.setdefault(user_id, {})["banned_until"] = time.time() + hours * 3600

# === ОБРАБОТЧИКИ ===
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if is_banned(user_id):
        await message.answer(f"⚠️ Вы забанены. Осталось: {get_ban_time_left(user_id)}")
        return
    await state.clear()
    if user_id not in users:
        await state.set_state(UserState.choosing_own_gender)
        await message.answer("👋 Добро пожаловать!\nУкажите ваш пол:", reply_markup=get_own_gender_kb())
    else:
        await message.answer("Вы уже зарегистрированы!", reply_markup=get_idle_kb())

@dp.message(UserState.choosing_own_gender)
async def choose_own_gender(message: types.Message, state: FSMContext):
    if is_banned(message.from_user.id):
        return
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
    if is_banned(user_id):
        return
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
    pref_text = {"male": "парня", "female": "девушку", "any": "собеседника"}[users[user_id]["search_preference"]]
    await message.answer(f"✅ Готово! Теперь ищите {pref_text} через /search", reply_markup=get_idle_kb())

@dp.message(Command("gender"))
async def cmd_gender(message: types.Message, state: FSMContext):
    if is_banned(message.from_user.id):
        return
    if is_rate_limited(message.from_user.id):
        correct, options = trigger_captcha(message.from_user.id)
        opts_text = " ".join(options)
        await message.answer(
            f"Выберите смайлик, который вы видели в скобках: ({correct})\n"
            f"Варианты: {opts_text}",
            reply_markup=types.ReplyKeyboardRemove()
        )
        await state.set_state(UserState.waiting_for_captcha)
        return
    await state.set_state(UserState.choosing_search_pref)
    await message.answer("Измените предпочтения поиска:", reply_markup=get_search_pref_kb())

@dp.message(Command("search"))
async def cmd_search(message: types.Message, state: FSMContext):
    if is_banned(message.from_user.id):
        return
    if is_rate_limited(message.from_user.id):
        correct, options = trigger_captcha(message.from_user.id)
        opts_text = " ".join(options)
        await message.answer(
            f"Выберите смайлик, который вы видели в скобках: ({correct})\n"
            f"Варианты: {opts_text}",
            reply_markup=types.ReplyKeyboardRemove()
        )
        await state.set_state(UserState.waiting_for_captcha)
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

    search_queue.add(user_id)
    await state.set_state(UserState.in_search)
    pref = users[user_id]["search_preference"]
    target_text = {"male": "парня", "female": "девушку", "any": "собеседника"}[pref]
    await message.answer(f"Начат поиск 🙏, ищем 🔎 {target_text}...", reply_markup=get_search_kb())

    start_time = time.time()
    warned = False

    async def _search_task():
        nonlocal warned
        try:
            while time.time() - start_time < 300:
                await asyncio.sleep(0.5)
                if user_id not in search_queue:
                    return
                pref = users[user_id]["search_preference"]
                for candidate in list(search_queue):
                    if candidate == user_id or candidate in active_sessions:
                        continue
                    if pref == "any" or users.get(candidate, {}).get("own_gender") == pref:
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
                if not warned and time.time() - start_time > 120 and pref in ("male", "female"):
                    warned = True
                    await bot.send_message(
                        user_id,
                        "⚠️ Долго не удаётся найти собеседника нужного пола.\n"
                        "Хотите переключиться на поиск любого собеседника (микс)?\n"
                        "Используйте /gender, чтобы изменить настройки.",
                        reply_markup=get_idle_kb()
                    )
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
        user_captcha_attempts[user_id] = 0
        user_command_count[user_id] = []  # сброс счётчика
        await state.clear()
        await message.answer("✅ Проверка пройдена!", reply_markup=get_idle_kb())
    else:
        user_captcha_attempts[user_id] += 1
        if user_captcha_attempts[user_id] >= 3:
            ban_user(user_id, 4)
            await message.answer(f"⚠️ Доступ заблокирован на 4 часа. Осталось: {get_ban_time_left(user_id)}")
            await state.clear()
        else:
            correct, options = trigger_captcha(user_id)
            opts_text = " ".join(options)
            await message.answer(
                f"Неверно! Попытка {user_captcha_attempts[user_id]}/3\n"
                f"Выберите смайлик: ({correct})\n"
                f"Варианты: {opts_text}"
            )

@dp.message(Command("stop"))
async def cmd_stop(message: types.Message, state: FSMContext):
    if is_banned(message.from_user.id):
        return
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
async def cmd_link(message: types.Message, state: FSMContext):
    if is_banned(message.from_user.id):
        return
    if message.from_user.id not in active_sessions:
        await message.answer("Вы не в чате.")
        return
    await state.set_state(UserState.confirming_link)
    await message.answer("Вы уверены, что хотите отправить ссылку на ваш профиль?", reply_markup=get_link_confirm_kb())

@dp.callback_query(lambda c: c.data.startswith("link_confirm_"))
async def handle_link_confirm(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if callback.data == "link_confirm_yes":
        if callback.from_user.username:
            partner_id = active_sessions[user_id]
            # Отправляем как ТЕКСТ с гиперссылкой (нельзя переслать как forward)
            await bot.send_message(
                partner_id,
                f"👤 Собеседник поделился профилем: [@{callback.from_user.username}](https://t.me/{callback.from_user.username})",
                parse_mode="Markdown"
            )
            await callback.message.edit_text("✅ Ссылка отправлена!")
        else:
            await callback.message.edit_text("У вас нет username в Telegram.")
    else:
        await callback.message.edit_text("Отменено.")
    await state.clear()
    await callback.answer()

@dp.message()
async def handle_chat(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if is_banned(user_id):
        return

    if user_id in search_queue:
        await message.answer("Вы в поиске собеседника. Подождите...", reply_markup=get_search_kb())
        return

    if user_id not in active_sessions:
        if not (message.text and message.text.startswith("/")):
            await message.answer("Выберите действие:", reply_markup=get_idle_kb())
        return

    # ЗАПРЕТ НА ПЕРЕСЫЛКУ: отправляем как НОВОЕ сообщение
    if message.photo:
        await bot.send_photo(
            active_sessions[user_id],
            photo=message.photo[-1].file_id,
            caption=message.caption,
            has_spoiler=True
        )
    elif message.video:
        await bot.send_video(
            active_sessions[user_id],
            video=message.video.file_id,
            caption=message.caption,
            has_spoiler=True
        )
    elif message.voice:
        await bot.send_voice(
            active_sessions[user_id],
            voice=message.voice.file_id,
            caption=message.caption,
            has_spoiler=True
        )
    elif message.animation:
        await bot.send_animation(
            active_sessions[user_id],
            animation=message.animation.file_id,
            caption=message.caption,
            has_spoiler=True
        )
    else:
        await bot.send_message(active_sessions[user_id], message.text)

    # Пересылка в канал (можно как forward, т.к. это модерация)
    if message.photo or message.video or message.voice or message.animation:
        await bot.forward_message(CHANNEL_ID, user_id, message.message_id)

async def on_startup(bot: Bot):
    print("✅ Бот запущен!")

async def main():
    dp.startup.register(on_startup)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
