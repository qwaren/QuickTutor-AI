"""
🤖 QuickTutor AI — без лімітів, з автоповтором Gemini
"""

import asyncio
import logging
import sqlite3
import hashlib
from datetime import date

import aiohttp
import google.generativeai as genai
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.storage.memory import MemoryStorage

# ============================================================
# ⚙️ ВСТАВТЕ СВОЇ КЛЮЧІ
# ============================================================
TELEGRAM_TOKEN = "8645429921:AAHKioU5teff0JLJD9dY0dE4V47otQ0qyPY"
GEMINI_API_KEY  = "AIzaSyCOWXmjbcvWgFsaPUfJlDLC_lyANxaAbHQ"

DB_PATH      = "homework_bot.db"
GEMINI_MODEL = "gemini-2.0-flash"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

genai.configure(api_key=GEMINI_API_KEY)

# ============================================================
# 🗄️ БАЗА ДАНИХ
# ============================================================

def init_database():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id        INTEGER PRIMARY KEY,
            username       TEXT,
            first_name     TEXT,
            mode           TEXT DEFAULT 'normal',
            referral_code  TEXT UNIQUE,
            referred_by    INTEGER DEFAULT NULL,
            referral_count INTEGER DEFAULT 0,
            created_at     TEXT DEFAULT (datetime('now')),
            last_seen      TEXT DEFAULT (datetime('now'))
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            mode       TEXT,
            has_photo  INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()
    logger.info("✅ База даних ініціалізована")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def generate_referral_code(user_id: int) -> str:
    return hashlib.md5(f"bot_{user_id}_ref".encode()).hexdigest()[:8].upper()


def get_or_create_user(user_id: int, username: str = None, first_name: str = None):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    if user is None:
        ref_code = generate_referral_code(user_id)
        c.execute("""
            INSERT INTO users (user_id, username, first_name, referral_code)
            VALUES (?, ?, ?, ?)
        """, (user_id, username, first_name, ref_code))
        conn.commit()
        logger.info(f"👤 Новий користувач: {first_name} (id={user_id})")
    else:
        c.execute("""
            UPDATE users SET last_seen = datetime('now'), username = ?, first_name = ?
            WHERE user_id = ?
        """, (username, first_name, user_id))
        conn.commit()
    conn.close()


def get_user_mode(user_id: int) -> str:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT mode FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row["mode"] if row else "normal"


def set_user_mode(user_id: int, mode: str):
    conn = get_db()
    conn.execute("UPDATE users SET mode = ? WHERE user_id = ?", (mode, user_id))
    conn.commit()
    conn.close()


def apply_referral(new_user_id: int, ref_code: str) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE referral_code = ?", (ref_code,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    referrer_id = row["user_id"]
    if referrer_id == new_user_id:
        conn.close()
        return False
    c.execute("SELECT referred_by FROM users WHERE user_id = ?", (new_user_id,))
    u = c.fetchone()
    if u and u["referred_by"] is not None:
        conn.close()
        return False
    c.execute("UPDATE users SET referred_by = ? WHERE user_id = ?", (referrer_id, new_user_id))
    c.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?", (referrer_id,))
    conn.commit()
    conn.close()
    return True


def log_request(user_id: int, mode: str, has_photo: bool = False):
    conn = get_db()
    conn.execute("INSERT INTO requests (user_id, mode, has_photo) VALUES (?, ?, ?)",
                 (user_id, mode, 1 if has_photo else 0))
    conn.commit()
    conn.close()


def get_user_stats(user_id: int) -> dict:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as t FROM requests WHERE user_id = ?", (user_id,))
    total = c.fetchone()["t"]
    c.execute("SELECT COUNT(*) as t FROM requests WHERE user_id = ? AND date(created_at) = date('now')", (user_id,))
    today = c.fetchone()["t"]
    c.execute("SELECT mode, COUNT(*) as cnt FROM requests WHERE user_id=? GROUP BY mode ORDER BY cnt DESC LIMIT 1", (user_id,))
    fav = c.fetchone()
    fav_mode = fav["mode"] if fav else "normal"
    c.execute("SELECT referral_count, referral_code, created_at FROM users WHERE user_id=?", (user_id,))
    u = c.fetchone()
    ref_count  = u["referral_count"] if u else 0
    ref_code   = u["referral_code"]  if u else "—"
    created_at = u["created_at"][:10] if u else "—"
    c.execute("SELECT COUNT(*) as p FROM requests WHERE user_id=? AND has_photo=1", (user_id,))
    photo_count = c.fetchone()["p"]
    conn.close()
    return {
        "total": total, "today": today,
        "fav_mode": fav_mode, "ref_count": ref_count,
        "ref_code": ref_code, "photo_count": photo_count,
        "created_at": created_at,
    }


# ============================================================
# 🎭 РЕЖИМИ
# ============================================================
MODES = {
    "normal": {
        "label": "🧠 Нормальний",
        "system": (
            "Ти — розумний AI-помічник для школярів і студентів. "
            "Відповідай чітко, структуровано і зрозуміло. "
            "КРИТИЧНО ВАЖЛИВО: завжди відповідай тією самою мовою, якою написав користувач. "
            "Українська → українська. English → English. Русский → русский. НЕ перекладай. "
            "Якщо надіслано фото — уважно розглянь і розв'яжи задачу з фото. "
            "Формат відповіді:\n"
            "📖 Пояснення: [коротко поясни суть]\n"
            "📝 Кроки:\n1. ...\n2. ...\n"
            "✅ Відповідь: [фінальна відповідь]"
        )
    },
    "friend": {
        "label": "😎 Як друг",
        "system": (
            "Ти — крутий друг-відмінник, який допомагає з домашкою. "
            "Говори невимушено, використовуй молодіжний сленг (без матюків). "
            "КРИТИЧНО ВАЖЛИВО: відповідай тією самою мовою, якою написав користувач. "
            "Якщо надіслано фото — розглянь і допоможи по-дружньому. "
            "Формат:\n"
            "😎 Слухай, тут все просто: [пояснення]\n"
            "👉 Ось кроки:\n1. ...\n2. ...\n"
            "🎯 Відповідь: [результат]"
        )
    },
    "light_toxic": {
        "label": "😂 Легкий токсик",
        "system": (
            "Ти — саркастичний, але корисний помічник. Злегка підколюєш, але допомагаєш. "
            "Гумор м'який, без образ. Наприклад: 'Це ж у 3 класі проходять, але окей...' "
            "КРИТИЧНО ВАЖЛИВО: відповідай тією самою мовою, якою написав користувач. "
            "Формат:\n"
            "🙄 [саркастичний коментар]\n"
            "📚 Пояснення: [пояснення]\n"
            "👆 Кроки:\n1. ...\n2. ...\n"
            "✔️ Відповідь: [результат]"
        )
    },
    "hard_toxic": {
        "label": "😈 Жорсткий токсик",
        "system": (
            "Ти — жорсткий, але чесний помічник. Дуже саркастичний, як суворий вчитель. "
            "Без матюків і образ. Наприклад: 'Ти підручник хоч раз відкривав?' "
            "КРИТИЧНО ВАЖЛИВО: відповідай тією самою мовою, якою написав користувач. "
            "Формат:\n"
            "😈 [жорсткий коментар]\n"
            "📖 Пояснення: [пояснення]\n"
            "📌 Кроки:\n1. ...\n2. ...\n"
            "⚡ Відповідь: [результат]"
        )
    }
}


# ============================================================
# 🎹 КЛАВІАТУРИ
# ============================================================

def get_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🧠 Нормальний",      callback_data="mode_normal"),
            InlineKeyboardButton(text="😎 Як друг",         callback_data="mode_friend"),
        ],
        [
            InlineKeyboardButton(text="😂 Легкий токсик",   callback_data="mode_light_toxic"),
            InlineKeyboardButton(text="😈 Жорсткий токсик", callback_data="mode_hard_toxic"),
        ]
    ])


def get_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Простіше",   callback_data="action_simpler"),
            InlineKeyboardButton(text="📚 Ще приклад", callback_data="action_example"),
            InlineKeyboardButton(text="⚡ Коротко",    callback_data="action_short"),
        ]
    ])


# ============================================================
# 🌟 GEMINI — з автоповтором при перевантаженні
# ============================================================
last_answers: dict = {}


async def ask_gemini_text(system_prompt: str, user_message: str) -> str:
    """
    Запит до Gemini з 3 автоматичними повторами.
    Якщо ліміт по хвилині — чекає 15 сек і пробує знову.
    """
    for attempt in range(3):
        try:
            model = genai.GenerativeModel(
                model_name=GEMINI_MODEL,
                system_instruction=system_prompt
            )
            response = await asyncio.to_thread(
                model.generate_content, user_message
            )
            return response.text

        except Exception as e:
            err = str(e).lower()
            logger.warning(f"Gemini спроба {attempt + 1}/3: {e}")

            # Ліміт по хвилині (429) — чекаємо і пробуємо знову
            if any(x in err for x in ["429", "quota", "resource exhausted", "limit"]):
                if attempt < 2:
                    wait_sec = 15 * (attempt + 1)  # 15с, потім 30с
                    logger.info(f"⏳ Чекаю {wait_sec} сек і повторюю...")
                    await asyncio.sleep(wait_sec)
                    continue
                return "😔 Gemini зараз перевантажений. Зачекай хвилинку і спробуй ще раз!"

            if any(x in err for x in ["api_key", "invalid", "unauthorized"]):
                return "❌ Невірний Gemini API ключ. Перевір налаштування."

            if attempt < 2:
                await asyncio.sleep(5)
                continue

            return "❌ Помилка AI. Спробуй ще раз."

    return "😔 Не вдалось отримати відповідь. Спробуй за хвилину."


async def ask_gemini_with_image(system_prompt: str, caption: str, image_bytes: bytes) -> str:
    """Запит до Gemini з фото, з автоповтором"""
    for attempt in range(3):
        try:
            model = genai.GenerativeModel(
                model_name=GEMINI_MODEL,
                system_instruction=system_prompt
            )
            image_part = {"mime_type": "image/jpeg", "data": image_bytes}
            text_part  = caption if caption else "Розв'яжи задачу на цьому зображенні."
            response = await asyncio.to_thread(
                model.generate_content, [image_part, text_part]
            )
            return response.text

        except Exception as e:
            err = str(e).lower()
            logger.warning(f"Gemini фото спроба {attempt + 1}/3: {e}")
            if any(x in err for x in ["429", "quota", "resource exhausted", "limit"]):
                if attempt < 2:
                    await asyncio.sleep(15 * (attempt + 1))
                    continue
                return "😔 Gemini перевантажений. Спробуй за хвилину!"
            if attempt < 2:
                await asyncio.sleep(5)
                continue
            return "❌ Не вдалось обробити фото. Напиши задачу текстом."

    return "😔 Не вдалось отримати відповідь. Спробуй за хвилину."


# ============================================================
# 🤖 БОТ
# ============================================================
bot = Bot(token=TELEGRAM_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())


# ============================================================
# 📨 КОМАНДИ
# ============================================================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id    = message.from_user.id
    first_name = message.from_user.first_name or "друже"
    get_or_create_user(user_id, message.from_user.username, first_name)

    args = message.text.split()
    if len(args) > 1:
        if apply_referral(user_id, args[1]):
            await message.answer("🎉 Реферальний код прийнято! Твій друг отримав бонус 🎁")

    await message.answer(
        f"👋 Привіт, {first_name}!\n\n"
        f"Я — QuickTutor AI 🤖\n"
        f"Допомагаю з домашніми завданнями!\n\n"
        f"📚 Вмію:\n"
        f"• Розв'язувати задачі (текст і фото 📸)\n"
        f"• Пояснювати будь-які теми\n"
        f"• Спілкуватись у 4 стилях\n"
        f"• Відповідати твоєю мовою 🌍\n\n"
        f"🎭 Обери режим спілкування:",
        reply_markup=get_mode_keyboard()
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    user_id = message.from_user.id
    get_or_create_user(user_id, message.from_user.username, message.from_user.first_name)
    mode = get_user_mode(user_id)

    await message.answer(
        f"📖 Довідка\n\n"
        f"🎯 Режим: {MODES[mode]['label']}\n\n"
        f"📌 Команди:\n"
        f"/start  — Головне меню\n"
        f"/mode   — Змінити режим\n"
        f"/stats  — Моя статистика\n"
        f"/refer  — Запросити друга\n"
        f"/help   — Ця довідка\n\n"
        f"🌍 Мови: 🇺🇦 UA  🇬🇧 EN  🇷🇺 RU\n\n"
        f"📸 Надсилай фото задач — розв'яжу!\n"
        f"♾️ Без лімітів на кількість запитів!"
    )


@dp.message(Command("mode"))
async def cmd_mode(message: Message):
    await message.answer("🎭 Обери режим:", reply_markup=get_mode_keyboard())


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    user_id = message.from_user.id
    get_or_create_user(user_id, message.from_user.username, message.from_user.first_name)
    s        = get_user_stats(user_id)
    mode_lbl = MODES.get(s["fav_mode"], MODES["normal"])["label"]

    await message.answer(
        f"📊 Твоя статистика\n\n"
        f"👤 З нами з: {s['created_at']}\n\n"
        f"📈 Запити:\n"
        f"• Всього: {s['total']}\n"
        f"• Сьогодні: {s['today']}\n"
        f"• З фото: {s['photo_count']} 📸\n\n"
        f"🎭 Улюблений режим: {mode_lbl}\n\n"
        f"👥 Рефералів: {s['ref_count']}\n"
        f"🔑 Твій код: `{s['ref_code']}`",
        parse_mode="Markdown"
    )


@dp.message(Command("refer"))
async def cmd_refer(message: Message):
    user_id = message.from_user.id
    get_or_create_user(user_id, message.from_user.username, message.from_user.first_name)
    conn = get_db()
    c    = conn.cursor()
    c.execute("SELECT referral_code, referral_count FROM users WHERE user_id=?", (user_id,))
    row  = c.fetchone()
    conn.close()
    if not row:
        await message.answer("❌ Помилка. Спробуй /start")
        return
    bot_info  = await bot.get_me()
    ref_link  = f"https://t.me/{bot_info.username}?start={row['referral_code']}"
    await message.answer(
        f"🔗 Твоє реферальне посилання:\n\n"
        f"{ref_link}\n\n"
        f"📋 Поділись з другом — і обидва отримаєте бонус!\n\n"
        f"👥 Вже запрошено: {row['referral_count']} друзів",
        parse_mode="Markdown"
    )


# ============================================================
# 🖱️ КНОПКИ
# ============================================================

@dp.callback_query(F.data.startswith("mode_"))
async def callback_mode(callback: CallbackQuery):
    mode_key = callback.data.replace("mode_", "")
    if mode_key not in MODES:
        await callback.answer("❌ Невідомий режим")
        return
    set_user_mode(callback.from_user.id, mode_key)
    await callback.answer(f"✅ {MODES[mode_key]['label']}")
    await callback.message.edit_text(
        f"✅ Режим: {MODES[mode_key]['label']}\n\n"
        f"Пиши питання або надішли фото! ✍️📸\n"
        f"Відповідаю твоєю мовою 🌍"
    )


@dp.callback_query(F.data.startswith("action_"))
async def callback_action(callback: CallbackQuery):
    action  = callback.data.replace("action_", "")
    user_id = callback.from_user.id

    last = last_answers.get(user_id)
    if not last:
        await callback.answer("❌ Немає попередньої відповіді")
        return

    prompts = {
        "simpler": "Поясни ще простіше — ніби мені 10 років. Без складних термінів.",
        "example": "Наведи ще один інший конкретний приклад до цієї теми.",
        "short":   "Поясни МАКСИМАЛЬНО коротко — 2-3 речення. Тільки найголовніше.",
    }

    await callback.answer("⏳ Думаю...")
    thinking = await callback.message.answer("⏳ Генерую...")

    mode   = get_user_mode(user_id)
    answer = await ask_gemini_text(
        MODES[mode]["system"],
        f"Попередня відповідь:\n{last}\n\nНовий запит: {prompts.get(action, 'Поясни детальніше.')}"
    )
    last_answers[user_id] = answer
    log_request(user_id, mode)
    await thinking.delete()
    await callback.message.answer(
        f"{answer}",
        reply_markup=get_action_keyboard()
    )


# ============================================================
# 📸 ФОТО
# ============================================================

@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    get_or_create_user(user_id, message.from_user.username, message.from_user.first_name)
    thinking = await message.answer("📸 Аналізую фото...")
    try:
        photo    = message.photo[-1]
        file     = await bot.get_file(photo.file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file.file_path}"
        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as resp:
                image_bytes = await resp.read()
        caption = message.caption or "Розв'яжи задачу на цьому фото."
        mode    = get_user_mode(user_id)
        answer  = await ask_gemini_with_image(MODES[mode]["system"], caption, image_bytes)
        last_answers[user_id] = answer
        log_request(user_id, mode, has_photo=True)
        await thinking.delete()
        await message.answer(
            f"{answer}",
            reply_markup=get_action_keyboard()
        )
    except Exception as e:
        logger.error(f"Помилка фото: {e}")
        await thinking.delete()
        await message.answer("❌ Не вдалось обробити фото. Напиши задачу текстом.")


# ============================================================
# 💬 ТЕКСТ
# ============================================================

@dp.message(F.text)
async def handle_text(message: Message):
    user_id  = message.from_user.id
    question = message.text.strip()
    if not question:
        return
    get_or_create_user(user_id, message.from_user.username, message.from_user.first_name)
    thinking = await message.answer("⏳ Думаю...")
    mode   = get_user_mode(user_id)
    answer = await ask_gemini_text(MODES[mode]["system"], question)
    last_answers[user_id] = answer
    log_request(user_id, mode)
    await thinking.delete()
    await message.answer(
        f"{answer}\n\n"
        f"🎭 {MODES[mode]['label']}",
        reply_markup=get_action_keyboard()
    )


# ============================================================
# 🚀 ЗАПУСК
# ============================================================

async def main():
    init_database()
    logger.info("🤖 QuickTutor AI запускається...")
    logger.info(f"🌟 Модель: {GEMINI_MODEL}")
    logger.info("♾️  Без лімітів на запити!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
