import os
import re
import asyncio
from datetime import date, timedelta

import requests
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import Command
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
SUPABASE_SERVICE_KEY = (os.getenv("SUPABASE_SERVICE_KEY") or "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN .env da yo‘q")
if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL .env da yo‘q")
if not SUPABASE_SERVICE_KEY:
    raise RuntimeError("SUPABASE_SERVICE_KEY .env da yo‘q")

BASE_REST = f"{SUPABASE_URL}/rest/v1"
HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# --- UI ---
MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Xarajat qo‘shish"), KeyboardButton(text="📊 Xarajatlar")],
    ],
    resize_keyboard=True
)

STATS_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📅 Kunlik", callback_data="stats:day"),
     InlineKeyboardButton(text="🗓 Haftalik", callback_data="stats:week")],
    [InlineKeyboardButton(text="🗓 Oylik", callback_data="stats:month"),
     InlineKeyboardButton(text="📆 Yillik", callback_data="stats:year")],
])

# --- parsing ---
AMOUNT_RE = re.compile(r"(?P<num>\d[\d\s.,]*)(?P<unit>\s*(k|ming|mln|million)?)", re.IGNORECASE)

def parse_amount(text: str):
    m = AMOUNT_RE.search(text or "")
    if not m:
        return None
    raw = m.group("num").replace(" ", "").replace(",", ".")
    unit = (m.group("unit") or "").lower()
    try:
        val = float(raw)
    except ValueError:
        return None

    if "mln" in unit or "million" in unit:
        val *= 1_000_000
    elif unit.endswith("k") or "ming" in unit:
        val *= 1_000
    return int(round(val))

def guess_place(text: str):
    t = (text or "").lower()
    for p in ["korzinka", "karzinka", "makro", "havas", "bozor", "internet", "benzin", "transport", "taksi", "aptek", "dorixona"]:
        if p in t:
            if p in ("korzinka", "karzinka"):
                return "Korzinka"
            if p in ("aptek", "dorixona"):
                return "Dorixona"
            return p.capitalize()
    return None

def guess_category(text: str):
    t = (text or "").lower()
    if any(k in t for k in ["korzinka", "karzinka", "bozor", "ovqat", "makro", "havas"]):
        return "ovqat"
    if any(k in t for k in ["internet", "wifi", "tarif"]):
        return "internet"
    if any(k in t for k in ["benzin", "zapravka", "taksi", "transport", "metro", "avtobus"]):
        return "transport"
    if any(k in t for k in ["aptek", "dorixona", "dori"]):
        return "dori"
    return "boshqa"

def fmt_sum(x: int) -> str:
    return f"{x:,}".replace(",", " ") + " so‘m"

# --- Supabase REST helpers ---
def sb_insert_expense(user_id: int, expense_date: date, place: str | None, category: str, amount: int, note: str):
    url = f"{BASE_REST}/expenses"
    payload = [{
        "user_id": int(user_id),
        "expense_date": str(expense_date),
        "place": place,
        "category": category,
        "amount": int(amount),
        "note": note
    }]
    r = requests.post(url, headers=HEADERS, json=payload, timeout=60)
    # Supabase ba'zan 201 yoki 204 qaytaradi
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(f"Supabase insert failed: {r.status_code} {r.text}")

def sb_fetch_expenses(user_id: int, from_d: date, to_d: date):
    url = f"{BASE_REST}/expenses"
    params = [
        ("select", "place,category,amount,expense_date"),
        ("user_id", f"eq.{int(user_id)}"),
        ("expense_date", f"gte.{from_d}"),
        ("expense_date", f"lte.{to_d}"),
        ("order", "expense_date.desc"),
    ]
    r = requests.get(url, headers=HEADERS, params=params, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Supabase select failed: {r.status_code} {r.text}")
    return r.json() or []

# --- handlers ---
@dp.message(Command("start"))
async def start(m: Message):
    await m.answer("💰 Xarajat botiga xush kelibsiz", reply_markup=MENU)

@dp.message(F.text == "➕ Xarajat qo‘shish")
async def add(m: Message):
    await m.answer("Xarajat yozing:\nMasalan: Korzinka 500k bozorlik")

@dp.message(F.text == "📊 Xarajatlar")
async def stats_menu(m: Message):
    await m.answer("Qaysi davr?", reply_markup=STATS_KB)

@dp.callback_query(F.data.startswith("stats:"))
async def stats(cq: CallbackQuery):
    # ENG MUHIM: darhol javob qaytaramiz (query is too old bo‘lmasin)
    await cq.answer("Hisoblayapman...")

    period = cq.data.split(":", 1)[1]
    today = date.today()

    if period == "day":
        from_d = to_d = today
        title = "📅 Kunlik"
    elif period == "week":
        from_d = today - timedelta(days=6)
        to_d = today
        title = "🗓 Haftalik (7 kun)"
    elif period == "month":
        from_d = today.replace(day=1)
        to_d = today
        title = "🗓 Oylik"
    else:
        from_d = today.replace(month=1, day=1)
        to_d = today
        title = "📆 Yillik"

    try:
        rows = await asyncio.to_thread(sb_fetch_expenses, cq.from_user.id, from_d, to_d)
    except Exception as e:
        await cq.message.answer(f"❌ Supabase xato: {e}")
        return

    total = sum(int(r["amount"]) for r in rows) if rows else 0

    cat_sum = {}
    for r in rows:
        cat = (r.get("category") or "boshqa").strip()
        cat_sum[cat] = cat_sum.get(cat, 0) + int(r["amount"])

    lines = [
        f"{title}",
        f"📌 {from_d} → {to_d}",
        f"💸 Umumiy: *{fmt_sum(total)}*",
        ""
    ]

    if not rows:
        lines.append("Hali yozuv yo‘q.")
    else:
        lines.append("Kategoriya bo‘yicha:")
        for cat, s in sorted(cat_sum.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"• {cat}: {fmt_sum(s)}")

    await cq.message.answer("\n".join(lines), parse_mode="Markdown")

@dp.message(F.text)
async def handle_text(m: Message):
    text = (m.text or "").strip()
    amount = parse_amount(text)
    if amount is None:
        return  # raqam bo‘lmasa jim turadi

    place = guess_place(text)
    category = guess_category(text)

    try:
        await asyncio.to_thread(
            sb_insert_expense,
            m.from_user.id,
            date.today(),
            place,
            category,
            int(amount),
            text
        )
    except Exception as e:
        await m.answer(f"❌ Saqlashda xato: {e}")
        return

    await m.answer(f"✅ Saqlandi: {fmt_sum(int(amount))}")

async def main():
    print("Bot ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
