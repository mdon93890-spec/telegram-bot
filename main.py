import asyncio
import html
import io
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ============ কনফিগ ============
# টোকেন কখনো কোডে লিখবেন না (GitHub এ গেলে অন্যরা চুরি করতে পারে)।
# Render এর Environment Variables এ BOT_TOKEN নামে বসাতে হবে।
TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not TOKEN:
    sys.exit("❌ BOT_TOKEN সেট করা নেই! Render > Environment এ BOT_TOKEN বসান।")
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()

# বাধ্যতামূলক চ্যানেল (ইউজারকে এখানে জয়েন করতেই হবে)
FORCE_CHANNEL = os.getenv("FORCE_CHANNEL", "@EasyIncomeBD_Payment").strip()
FORCE_URL = f"https://t.me/{FORCE_CHANNEL.lstrip('@')}"
JOIN_BTN = "✅ জয়েন করেছি"
ADMIN_ID = int(os.getenv("ADMIN_ID") or 8274424703)

USERS_FILE = "users.json"
GMAIL_FILE = "gmail_tasks.json"
APP_FILE = "app_tasks.json"
FACEBOOK_FILE = "facebook_tasks.json"
INSTAGRAM_FILE = "instagram_tasks.json"
USER_STATS_FILE = "user_stats.json"
BLOCKED_FILE = "blocked_users.json"
SETTINGS_FILE = "settings.json"

(
    PROOF_SUBMIT,
    WITHDRAW_NUM,
    WITHDRAW_AMT,
    SELL_USD_AMT,
    SELL_USD_PROOF,
    SELL_USD_ACC,
    FB_NUMBER,
    PRICE_INPUT,
) = range(8)



logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


# ============ ডাটা সেভ/লোড ============
# MONGODB_URI দেওয়া থাকলে সব ডাটা MongoDB তে থাকে, তাই Render রিস্টার্ট হলেও
# ইউজার, ব্যালেন্স, কাজ, সেটিং কিছুই হারায় না।
# MONGODB_URI না দিলে (যেমন Termux এ) আগের মতো .json ফাইলে সেভ হয়।
_mongo_col = None
_cache = {}
_db_lock = threading.Lock()

if MONGODB_URI:
    from pymongo import MongoClient

    _mongo_col = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=8000)["momin_bot"]["kv"]
    _mongo_col.database.client.admin.command("ping")
    print("✅ MongoDB সংযুক্ত হয়েছে")
else:
    print("⚠️ MONGODB_URI নেই: ডাটা শুধু ফাইলে সেভ হবে (Render এ রিস্টার্টে মুছে যাবে)")


def _read_local(filename, default_val):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default_val
    return default_val


def load_json_file(filename, default_val):
    if _mongo_col is None:
        return _read_local(filename, default_val)
    if filename in _cache:
        return _cache[filename]
    # পড়তে এরর হলে ইচ্ছা করে বট থামিয়ে দিই; নইলে খালি ডাটা দিয়ে আসল ডাটা মুছে যেত।
    doc = _mongo_col.find_one({"_id": filename})
    if doc is not None:
        data = json.loads(doc["json"])
    elif os.path.exists(filename):
        # প্রথমবার: পুরনো .json ফাইলের ডাটা অটো MongoDB তে তুলে দেয়
        data = _read_local(filename, default_val)
        _mongo_col.replace_one(
            {"_id": filename}, {"_id": filename, "json": json.dumps(data, ensure_ascii=False)}, upsert=True
        )
    else:
        data = default_val
    _cache[filename] = data
    return data


def save_json_file(filename, data):
    if _mongo_col is None:
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error("Save failed for %s: %s", filename, e)
        return
    try:
        with _db_lock:
            _cache[filename] = data
            _mongo_col.replace_one(
                {"_id": filename},
                {"_id": filename, "json": json.dumps(data, ensure_ascii=False)},
                upsert=True,
            )
    except Exception as e:
        logging.error("MongoDB save failed for %s: %s", filename, e)


# ============ Render এর জন্য ওয়েব সার্ভার + অটো-পিং ============
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is running")

    do_HEAD = do_GET

    def log_message(self, *args):
        pass


def start_keepalive():
    """Render Web Service এ পোর্ট খোলা লাগে। আর নিজেকে নিজে পিং করে ঘুমিয়ে পড়া ঠেকায়।"""
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logging.info("Health server port %s এ চালু", port)

    url = os.getenv("RENDER_EXTERNAL_URL", "").strip()
    if url:
        import time

        def _ping():
            while True:
                time.sleep(600)
                try:
                    urllib.request.urlopen(url, timeout=20).read()
                except Exception as e:
                    logging.warning("Self ping failed: %s", e)

        threading.Thread(target=_ping, daemon=True).start()


gmail_tasks = load_json_file(GMAIL_FILE, [])
app_tasks = load_json_file(APP_FILE, [])
facebook_tasks = load_json_file(FACEBOOK_FILE, [])
instagram_tasks = load_json_file(INSTAGRAM_FILE, [])
user_data_db = load_json_file(USER_STATS_FILE, {})
blocked_users = set(int(uid) for uid in load_json_file(BLOCKED_FILE, []))


def save_blocked_users():
    save_json_file(BLOCKED_FILE, list(blocked_users))


SETTINGS_DEFAULTS = {
    "gmail_price": 14,
    "fb_price": 3,
    "ig_price": 2,
    "usd_rate": 115,
    "old_gmail_price": 10,
    "withdraw_min": 25,
    "withdraw_min_binance": 30,
    "support_username": "mominboss006",
    "support_message": "",
    "referral_extra": "",
    "referral_bonus": 5,
}
settings = load_json_file(SETTINGS_FILE, {})
for _k, _v in SETTINGS_DEFAULTS.items():
    settings.setdefault(_k, _v)


def save_settings():
    save_json_file(SETTINGS_FILE, settings)


def get_support_message():
    """অ্যাডমিন /setsupportmsg দিয়ে পুরো বার্তা কাস্টম করলে সেটাই দেখাবে,
    নইলে /setsupportuser দিয়ে বসানো ইউজারনেম দিয়ে স্বয়ংক্রিয়ভাবে বার্তা তৈরি হবে।"""
    custom = settings.get("support_message", "").strip()
    if custom:
        return custom
    return f"সাপোর্ট: @{settings['support_username']}"


def load_registered_users():
    data = load_json_file(USERS_FILE, [])
    return set(int(uid) for uid in data)


def save_user_id(user_id):
    users = load_registered_users()
    if int(user_id) not in users:
        users.add(int(user_id))
        save_json_file(USERS_FILE, list(users))


def get_user_stats(user_id):
    key = str(user_id)
    if key not in user_data_db:
        user_data_db[key] = {}
    defaults = {
        "completed": 0,
        "pending": 0,
        "balance": 0.0,
        "ref_count": 0,
        "ref_earnings": 0.0,
        "referred_by": None,
        "has_completed_first_task": False,
        "gmail_submitted": 0,
        "ref_bonus_paid": False,
        "lang": "bn",
    }
    is_new_field = "ref_bonus_paid" not in user_data_db[key]
    for k, v in defaults.items():
        if k not in user_data_db[key]:
            user_data_db[key][k] = v
    # পুরনো সিস্টেমে যাদের রেফার বোনাস আগেই দেওয়া হয়ে গেছে, তাদের জন্য দ্বিতীয়বার দেওয়া হবে না
    if is_new_field and user_data_db[key].get("has_completed_first_task"):
        user_data_db[key]["ref_bonus_paid"] = True
    return user_data_db[key]


def save_user_stats():
    save_json_file(USER_STATS_FILE, user_data_db)


def get_lang(user_id):
    return get_user_stats(user_id).get("lang", "bn")


# ============ বাটনের লেখা (নম্বর ছাড়া) ============
# প্রতিটি বাটনের (বাংলা, ইংরেজি) লেখা এক জায়গায় রাখা হয়েছে।
# লেখা বদলাতে চাইলে শুধু এখানে বদলালেই সব জায়গায় ঠিক হয়ে যাবে।
BTN = {
    "tasks": ("📝 কাজ ▸", "📝 Tasks ▸"),
    "balance": ("💵 ব্যালেন্স", "💵 Balance"),
    "withdraw": ("💰 টাকা উত্তোলন", "💰 Withdraw"),
    "referral": ("🎁 My Referrals", "🎁 My Referrals"),
    "newbie": ("🆕 আমি নতুন", "🆕 I'm New"),
    "support": ("👮 এডমিন সাপোর্ট", "👮 Admin Support"),
    "language": ("🌐 Language / ভাষা", "🌐 Language / ভাষা"),
    "gmail": ("📧 জিমেইল কাজ", "📧 Gmail Task"),
    "facebook": ("📘 ফেসবুক কাজ", "📘 Facebook Task"),
    "instagram": ("📷 ইনস্টাগ্রাম কাজ", "📷 Instagram Task"),
    "sell_usd": ("🪙 ডলার বিক্রি করুন", "🪙 Sell Dollar"),
    "micro": ("🤑 ছোট ছোট কাজ করে ইনকাম করুন", "🤑 Earn by Doing Micro Tasks"),
    "old_gmail": ("📮 পুরাতন জিমেইল বিক্রি", "📮 Sell Old Gmail"),
    "cancel": ("❌ বাতিল করুন", "❌ Cancel"),
    "submit": ("📥 জমা দিন", "📥 Submit"),
    "cancel_task": ("❌ কাজটি বাতিল করুন", "❌ Cancel Task"),
}

# টেলিগ্রামের রঙিন বাটন: primary = নীল, success = সবুজ, danger = লাল
BLUE, GREEN, RED = "primary", "success", "danger"


def L(key, lang="bn"):
    """বাটনের লেখা ভাষা অনুযায়ী দেয়।"""
    return BTN[key][1 if lang == "en" else 0]


def is_btn(text, key):
    return text in BTN[key]


def kb_btn(text, style=None):
    """রঙিন রিপ্লাই-কিবোর্ড বাটন।"""
    if style:
        return KeyboardButton(text, api_kwargs={"style": style})
    return KeyboardButton(text)


def ibtn(text, callback_data=None, style=None, url=None):
    """রঙিন ইনলাইন বাটন।"""
    kwargs = {"api_kwargs": {"style": style}} if style else {}
    return InlineKeyboardButton(text, callback_data=callback_data, url=url, **kwargs)


# ============ কিবোর্ড ============
def get_main_keyboard(lang="bn"):
    rows = [
        [kb_btn(L("tasks", lang), BLUE), kb_btn(L("balance", lang), GREEN)],
        [kb_btn(L("withdraw", lang), GREEN), kb_btn(L("referral", lang), BLUE)],
        [kb_btn(L("newbie", lang), BLUE), kb_btn(L("support", lang), GREEN)],
        [kb_btn(L("language", lang), BLUE)],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_task_sub_keyboard(lang="bn"):
    rows = [
        [kb_btn(L("gmail", lang), BLUE), kb_btn(L("facebook", lang), GREEN)],
        [kb_btn(L("instagram", lang), GREEN), kb_btn(L("sell_usd", lang), BLUE)],
        [kb_btn(L("micro", lang), BLUE)],
        [kb_btn(L("old_gmail", lang), GREEN)],
        [kb_btn(L("cancel", lang), RED)],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_task_keyboard(lang="bn"):
    rows = [[kb_btn(L("submit", lang), GREEN), kb_btn(L("cancel_task", lang), RED)]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _collect_menu_texts():
    texts = set()
    for lang in ("bn", "en"):
        for kb in (
            get_main_keyboard(lang),
            get_task_sub_keyboard(lang),
            get_task_keyboard(lang),
        ):
            for row in kb.keyboard:
                for btn in row:
                    texts.add(btn.text if hasattr(btn, "text") else str(btn))
    return texts


MENU_TEXTS = _collect_menu_texts()
MENU_REGEX = "^(" + "|".join(re.escape(t) for t in MENU_TEXTS) + ")$"
MENU_FILTER = filters.Regex(MENU_REGEX)
SUBMIT_TEXTS = BTN["submit"]
TEXT_INPUT = filters.TEXT & ~filters.COMMAND & ~MENU_FILTER


# ============ হেল্পার ============
def esc(value):
    return html.escape(str(value))


def clear_task_state(context):
    for k in (
        "current_task_text",
        "last_task_msg_id",
        "current_assigned_task_idx",
        "current_task_type",
    ):
        context.user_data.pop(k, None)


async def notify_admin(context, text, photo=None):
    try:
        if photo:
            await context.bot.send_photo(
                chat_id=ADMIN_ID, photo=photo, caption=text[:1000], parse_mode="HTML"
            )
        else:
            await context.bot.send_message(
                chat_id=ADMIN_ID, text=text, parse_mode="HTML"
            )
    except Exception as e:
        logging.error("Admin notify failed: %s", e)


async def safe_send(context, chat_id, text, **kwargs):
    try:
        await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
        return True
    except Exception:
        return False


# ============ বাধ্যতামূলক চ্যানেল জয়েন ============
# জরুরি: বটকে অবশ্যই FORCE_CHANNEL চ্যানেলের **অ্যাডমিন** বানাতে হবে,
# নইলে টেলিগ্রাম ইউজার জয়েন করেছে কি না বটকে জানতে দেয় না।
_joined_cache = {}
JOIN_CACHE_SECONDS = 300
_admin_warned = {"channel": False}


async def is_channel_member(bot, user_id):
    """True = জয়েন আছে, False = নেই, None = চেক করা যায়নি (বট চ্যানেলের অ্যাডমিন নয় ইত্যাদি)।"""
    try:
        m = await bot.get_chat_member(FORCE_CHANNEL, user_id)
        if m.status in ("member", "administrator", "creator"):
            return True
        if m.status == "restricted":
            return bool(getattr(m, "is_member", True))
        return False
    except Exception as e:
        logging.warning("Channel check failed: %s", e)
        return None


def join_keyboard():
    return ReplyKeyboardMarkup([[kb_btn(JOIN_BTN, GREEN)]], resize_keyboard=True)


async def send_join_prompt(message):
    kb = InlineKeyboardMarkup([[ibtn("📢 চ্যানেলে জয়েন করুন", url=FORCE_URL, style=BLUE)]])
    await message.reply_text(
        "⚠️ বটটি ব্যবহার করতে হলে আগে আমাদের চ্যানেলে জয়েন করতে হবে।\n\n"
        f"👉 {FORCE_URL}\n\n"
        "জয়েন করার পর নিচের ✅ জয়েন করেছি বাটনে চাপুন।",
        reply_markup=kb,
    )
    await message.reply_text("👇 জয়েন করার পর এই বাটনে চাপুন", reply_markup=join_keyboard())


async def join_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """সব হ্যান্ডলারের আগে চলে। চ্যানেলে জয়েন না থাকলে ইউজারকে মেনুতে ঢুকতে দেয় না।"""
    user = update.effective_user
    if not user or user.id == ADMIN_ID:
        return
    msg = update.message
    text = (msg.text or "") if msg else ""
    if text.startswith("/start"):
        return  # /start নিজেই চেক করে

    # "✅ জয়েন করেছি" বাটন: সরাসরি নতুন করে চেক
    if msg and text == JOIN_BTN:
        status = await is_channel_member(context.bot, user.id)
        if status is False:
            await send_join_prompt(msg)
            raise ApplicationHandlerStop
        _joined_cache[user.id] = time.time()
        await msg.reply_text(
            "✅ ধন্যবাদ! আপনি সফলভাবে জয়েন করেছেন।\nএখন নিচের মেনু ব্যবহার করুন 👇",
            reply_markup=get_main_keyboard(get_lang(user.id)),
        )
        raise ApplicationHandlerStop

    if time.time() - _joined_cache.get(user.id, 0) < JOIN_CACHE_SECONDS:
        return

    status = await is_channel_member(context.bot, user.id)
    if status is True:
        _joined_cache[user.id] = time.time()
        return
    if status is None:
        # চেক করা যাচ্ছে না: সবাইকে আটকে না দিয়ে ছেড়ে দিই, আর অ্যাডমিনকে জানাই
        if not _admin_warned["channel"]:
            _admin_warned["channel"] = True
            await safe_send(
                context,
                ADMIN_ID,
                f"⚠️ জয়েন চেক কাজ করছে না!\nবটকে {FORCE_CHANNEL} চ্যানেলের অ্যাডমিন বানান, "
                "তারপর আবার চেষ্টা করুন। ততক্ষণ সবাই বট ব্যবহার করতে পারছে।",
            )
        return

    _joined_cache.pop(user.id, None)
    if update.callback_query:
        await update.callback_query.answer(
            "⚠️ আগে চ্যানেলে জয়েন করুন, তারপর ✅ জয়েন করেছি চাপুন।", show_alert=True
        )
        raise ApplicationHandlerStop
    if msg:
        await send_join_prompt(msg)
        raise ApplicationHandlerStop


# ============ ব্যাকআপ / রিস্টোর ============
# এক ফাইলে সব ইউজারের ডাটা (ব্যালেন্স সহ)। আসল স্থায়ী ডাটা থাকে MongoDB তে,
# এই ফাইল হলো তার ডাউনলোডযোগ্য কপি।
BACKUP_FILES = [
    USERS_FILE,
    USER_STATS_FILE,
    BLOCKED_FILE,
    SETTINGS_FILE,
    GMAIL_FILE,
    APP_FILE,
    FACEBOOK_FILE,
    INSTAGRAM_FILE,
]


def build_backup_dict():
    return {
        "users.json": list(load_registered_users()),
        "user_stats.json": user_data_db,
        "blocked_users.json": list(blocked_users),
        "settings.json": settings,
        "gmail_tasks.json": gmail_tasks,
        "app_tasks.json": app_tasks,
        "facebook_tasks.json": facebook_tasks,
        "instagram_tasks.json": instagram_tasks,
    }


def build_backup_bytes():
    data = build_backup_dict()
    data["_created"] = datetime.now().isoformat(timespec="seconds")
    return json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")


def apply_backup(data):
    """ব্যাকআপের ডাটা বটের মেমরি ও স্টোরেজে বসায়।"""
    user_data_db.clear()
    user_data_db.update(data.get("user_stats.json", {}))
    save_user_stats()

    blocked_users.clear()
    blocked_users.update(int(x) for x in data.get("blocked_users.json", []))
    save_blocked_users()

    settings.update(data.get("settings.json", {}))
    save_settings()

    for name, lst, fname in (
        ("gmail_tasks.json", gmail_tasks, GMAIL_FILE),
        ("app_tasks.json", app_tasks, APP_FILE),
        ("facebook_tasks.json", facebook_tasks, FACEBOOK_FILE),
        ("instagram_tasks.json", instagram_tasks, INSTAGRAM_FILE),
    ):
        lst[:] = data.get(name, [])
        save_json_file(fname, lst)

    save_json_file(USERS_FILE, [int(x) for x in data.get("users.json", [])])


def backup_filename():
    return "bot_backup_" + datetime.now().strftime("%Y%m%d_%H%M") + ".json"


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/backup : সব ইউজারের ডাটা এক ফাইলে অ্যাডমিনকে পাঠায়।"""
    if update.effective_user.id != ADMIN_ID:
        return
    raw = build_backup_bytes()
    await update.message.reply_document(
        document=io.BytesIO(raw),
        filename=backup_filename(),
        caption=(
            f"💾 ব্যাকআপ ফাইল\n👥 ইউজার: {len(user_data_db)} জন\n"
            "ফিরিয়ে আনতে: এই ফাইলটা বটে পাঠিয়ে ক্যাপশনে /restore লিখুন।"
        ),
    )


async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ব্যাকআপ ফাইল পাঠিয়ে ক্যাপশনে /restore লিখলে সব ডাটা ফিরে আসে।"""
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        tg_file = await update.message.document.get_file()
        raw = bytes(await tg_file.download_as_bytearray())
        data = json.loads(raw.decode("utf-8"))
        assert isinstance(data, dict) and "user_stats.json" in data
    except Exception:
        await update.message.reply_text("⚠️ ফাইলটা ঠিক নেই। /backup থেকে পাওয়া ফাইলই পাঠান।")
        return

    apply_backup(data)
    await update.message.reply_text(
        f"✅ রিস্টোর সম্পন্ন!\n👥 ইউজার: {len(user_data_db)} জন\n"
        f"🕒 ব্যাকআপের সময়: {data.get('_created', 'অজানা')}"
    )


_last_backup = {"hash": None, "msg_id": None}


def _data_hash():
    return json.dumps(build_backup_dict(), sort_keys=True, ensure_ascii=False)


async def push_backup(application, force=False):
    """ডাটা বদলালে ব্যাকআপ ফাইল অ্যাডমিনের চ্যাটে পাঠিয়ে পিন করে রাখে।
    রিস্টার্টের পর বট এই পিন করা ফাইল থেকে নিজেই সব ডাটা ফিরিয়ে আনে।"""
    if not user_data_db and not force:
        return  # ফাঁকা ডাটা দিয়ে ভালো ব্যাকআপ ওভাররাইট হতে দেব না
    current = _data_hash()
    if not force and current == _last_backup["hash"]:
        return
    bot = application.bot
    sent = await bot.send_document(
        chat_id=ADMIN_ID,
        document=io.BytesIO(build_backup_bytes()),
        filename=backup_filename(),
        caption=f"💾 অটো ব্যাকআপ | 👥 {len(user_data_db)} জন",
        disable_notification=True,
    )
    try:
        await bot.pin_chat_message(ADMIN_ID, sent.message_id, disable_notification=True)
    except Exception as e:
        logging.warning("Pin failed: %s", e)
    old = _last_backup["msg_id"]
    if old:
        try:
            await bot.delete_message(ADMIN_ID, old)
        except Exception:
            pass
    _last_backup["hash"] = current
    _last_backup["msg_id"] = sent.message_id


async def auto_restore_from_pin(application):
    """বট ফাঁকা ডাটা নিয়ে শুরু হলে (যেমন Render রিস্টার্টের পর) পিন করা ব্যাকআপ থেকে ফেরত আনে।"""
    if _mongo_col is not None or user_data_db:
        return
    try:
        chat = await application.bot.get_chat(ADMIN_ID)
        pinned = chat.pinned_message
        if not pinned or not pinned.document or not pinned.document.file_name.startswith("bot_backup_"):
            return
        tg_file = await application.bot.get_file(pinned.document.file_id)
        data = json.loads(bytes(await tg_file.download_as_bytearray()).decode("utf-8"))
        assert "user_stats.json" in data
        apply_backup(data)
        _last_backup["msg_id"] = pinned.message_id
        _last_backup["hash"] = _data_hash()
        await application.bot.send_message(
            ADMIN_ID,
            f"✅ বট রিস্টার্ট হয়েছিল। ব্যাকআপ থেকে অটো ডাটা ফিরে এসেছে!\n"
            f"👥 ইউজার: {len(user_data_db)} জন\n🕒 ব্যাকআপের সময়: {data.get('_created', 'অজানা')}",
        )
    except Exception as e:
        logging.warning("Auto restore failed: %s", e)


async def auto_backup_loop(application):
    """ডাটা বদলালে প্রতি ৩ মিনিটে ব্যাকআপ পাঠায়; কিছু না বদলালে কিছু পাঠায় না।"""
    await auto_restore_from_pin(application)
    while True:
        try:
            await push_backup(application)
        except Exception as e:
            logging.warning("Auto backup failed: %s", e)
        await asyncio.sleep(180)


# ============ /start ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    stats = get_user_stats(user.id)
    save_user_id(user.id)

    if context.args and not stats["referred_by"]:
        try:
            ref_id = int(context.args[0])
            if ref_id != user.id and str(ref_id) in user_data_db:
                ref_stats = get_user_stats(ref_id)
                stats["referred_by"] = ref_id
                ref_stats["ref_count"] += 1
                save_user_stats()
                await safe_send(
                    context,
                    ref_id,
                    f"👥 আপনার লিংকে নতুন একজন ইউজার জয়েন করেছে! ({user.first_name})",
                )
        except Exception:
            pass

    if user.id != ADMIN_ID:
        status = await is_channel_member(context.bot, user.id)
        if status is False:
            _joined_cache.pop(user.id, None)
            await send_join_prompt(update.message)
            return
        if status is True:
            _joined_cache[user.id] = time.time()

    kb = [
        [InlineKeyboardButton("🇧🇩 বাংলা", callback_data="lang_bn")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
    ]
    await update.message.reply_text(
        f"স্বাগতম, {user.first_name}!\n"
        "দয়া করে আপনার ভাষা নির্বাচন করুন:\nPlease select your language:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


# ============ অ্যাডমিন কমান্ড ============
async def online_notice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    for uid in load_registered_users():
        await safe_send(context, uid, "🔔 বট অনলাইনে আছে! কাজ শুরু করুন।")
    await update.message.reply_text("✅ অনলাইন নোটিশ পাঠানো হয়েছে!")


async def notice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    txt = update.message.text.replace("/notice", "", 1).strip()
    if not txt:
        await update.message.reply_text("ব্যবহার: /notice [লেখা]")
        return
    for uid in load_registered_users():
        await safe_send(context, uid, f"📢 নোটিশ:\n\n{txt}")
    await update.message.reply_text("✅ নোটিশ পাঠানো হয়েছে!")


async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    txt = update.message.text.replace("/addtask", "", 1).strip()
    if not txt:
        await update.message.reply_text("ব্যবহার: /addtask [কাজের বিবরণ]")
        return
    gmail_tasks.append(txt)
    save_json_file(GMAIL_FILE, gmail_tasks)
    await update.message.reply_text(
        f"✅ জিমেইল কাজ যোগ করা হয়েছে! মোট: {len(gmail_tasks)} টি।"
    )


async def clear_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    gmail_tasks.clear()
    save_json_file(GMAIL_FILE, gmail_tasks)
    await update.message.reply_text("🗑️ সমস্ত জিমেইল কাজ মুছে ফেলা হয়েছে!")


async def add_fb_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    txt = update.message.text.replace("/addfbtask", "", 1).strip()
    if not txt:
        await update.message.reply_text("ব্যবহার: /addfbtask [কাজের বিবরণ]")
        return
    facebook_tasks.append(txt)
    save_json_file(FACEBOOK_FILE, facebook_tasks)
    await update.message.reply_text(
        f"✅ ফেসবুক কাজ যোগ করা হয়েছে! মোট: {len(facebook_tasks)} টি।"
    )


async def clear_fb_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    facebook_tasks.clear()
    save_json_file(FACEBOOK_FILE, facebook_tasks)
    await update.message.reply_text("🗑️ সমস্ত ফেসবুক কাজ মুছে ফেলা হয়েছে!")


async def add_ig_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    txt = update.message.text.replace("/addigtask", "", 1).strip()
    if not txt:
        await update.message.reply_text("ব্যবহার: /addigtask [কাজের বিবরণ]")
        return
    instagram_tasks.append(txt)
    save_json_file(INSTAGRAM_FILE, instagram_tasks)
    await update.message.reply_text(
        f"✅ ইনস্টাগ্রাম কাজ যোগ করা হয়েছে! মোট: {len(instagram_tasks)} টি।"
    )


async def clear_ig_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    instagram_tasks.clear()
    save_json_file(INSTAGRAM_FILE, instagram_tasks)
    await update.message.reply_text("🗑️ সমস্ত ইনস্টাগ্রাম কাজ মুছে ফেলা হয়েছে!")










async def set_support_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ব্যবহার: /setsupportuser username (@ ছাড়া বা সহ, দুটোই চলবে)"""
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /setsupportuser username")
        return
    uname = context.args[0].lstrip("@").strip()
    settings["support_username"] = uname
    save_settings()
    await update.message.reply_text(f"✅ সাপোর্ট ইউজারনেম এখন @{uname}")


async def set_support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ব্যবহার: /setsupportmsg পুরো বার্তা (ইউজার সাপোর্ট বাটনে চাপলে এটাই দেখবে)"""
    if update.effective_user.id != ADMIN_ID:
        return
    text = update.message.text.replace("/setsupportmsg", "", 1).strip()
    if not text:
        await update.message.reply_text("ব্যবহার: /setsupportmsg পুরো বার্তা")
        return
    settings["support_message"] = text
    save_settings()
    await update.message.reply_text("✅ সাপোর্ট বার্তা আপডেট করা হয়েছে।\n\nপ্রিভিউ:\n" + text)


async def reset_support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    settings["support_message"] = ""
    save_settings()
    await update.message.reply_text(
        "✅ কাস্টম বার্তা সরানো হয়েছে। এখন থেকে শুধু ইউজারনেম দিয়ে ডিফল্ট বার্তা দেখাবে:\n\n"
        + get_support_message()
    )


async def set_referral_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ব্যবহার: /setreferraltext টেক্সট (রেফারেল বার্তার নিচে এটা যোগ হবে)"""
    if update.effective_user.id != ADMIN_ID:
        return
    text = update.message.text.replace("/setreferraltext", "", 1).strip()
    if not text:
        await update.message.reply_text("ব্যবহার: /setreferraltext টেক্সট")
        return
    settings["referral_extra"] = text
    save_settings()
    await update.message.reply_text("✅ রেফারেল মেসেজে এই লেখা যোগ হবে:\n\n" + text)


async def clear_referral_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    settings["referral_extra"] = ""
    save_settings()
    await update.message.reply_text("✅ রেফারেল মেসেজের অতিরিক্ত লেখা মুছে ফেলা হয়েছে।")


# ---- দাম পরিবর্তনের সিস্টেম ----
# settings.json এর কোন key টা কোন সার্ভিসের দাম, আর বাটনে কী নামে দেখাবে
PRICE_LABELS = {
    "gmail_price": "📧 জিমেইল কাজ",
    "fb_price": "📘 ফেসবুক কাজ",
    "ig_price": "📷 ইনস্টাগ্রাম কাজ",
    "usd_rate": "🪙 ১ ডলার বিক্রি",
    "old_gmail_price": "📮 পুরাতন জিমেইল",
    "withdraw_min": "💳 উইথড্র লিমিট (বিকাশ/নগদ/রিচার্জ)",
    "withdraw_min_binance": "💳 উইথড্র লিমিট (Binance)",
    "referral_bonus": "🎁 রেফারেল বোনাস (১ জন রেফারে)",
}
# টেক্সট ধরনের সেটিং (সংখ্যা নয়)
TEXT_LABELS = {
    "support_username": "👮 সাপোর্ট আইডি",
}
PRICE_ALIASES = {
    "withdraw": "withdraw_min",
    "limit": "withdraw_min",
    "binance": "withdraw_min_binance",
    "gmail": "gmail_price",
    "fb": "fb_price",
    "facebook": "fb_price",
    "ig": "ig_price",
    "instagram": "ig_price",
    "usd": "usd_rate",
    "dollar": "usd_rate",
    "old": "old_gmail_price",
    "oldgmail": "old_gmail_price",
    "ref": "referral_bonus",
    "referral": "referral_bonus",
    "bonus": "referral_bonus",
}


def format_amount(amount):
    return int(amount) if amount == int(amount) else amount


def price_panel_text():
    lines = ["⚙️ বর্তমান সেটিং:\n"]
    for key, label in PRICE_LABELS.items():
        lines.append(f"{label}: {settings[key]} টাকা")
    for key, label in TEXT_LABELS.items():
        lines.append(f"{label}: @{settings[key]}")
    lines.append("\n👇 যেটা বদলাতে চান সেটার বাটনে চাপ দিন")
    return "\n".join(lines)


def price_panel_markup():
    buttons = [
        ibtn(label, f"setprice_{key}", BLUE if i % 2 == 0 else GREEN)
        for i, (key, label) in enumerate({**PRICE_LABELS, **TEXT_LABELS}.items())
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


async def prices_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/prices বা /showprices: বাটন দিয়ে সহজে সব দাম বদলানোর প্যানেল।"""
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(price_panel_text(), reply_markup=price_panel_markup())


async def set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ব্যবহার: /setprice [gmail|fb|ig|usd|old] [সংখ্যা]"""
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        service = context.args[0].lower()
        amount = float(context.args[1])
        if amount < 0:
            raise ValueError
    except Exception:
        await update.message.reply_text(
            "ব্যবহার: /setprice [gmail/fb/ig/usd/old/withdraw/binance/ref] [সংখ্যা]\n"
            "উদাহরণ: /setprice gmail 16\n"
            "রেফারেল বোনাস: /setprice ref 7\n"
            "উইথড্র লিমিট: /setprice withdraw 50 (বিকাশ/নগদ), /setprice binance 60\n\n"
            "অথবা বাটন দিয়ে বদলাতে: /prices"
        )
        return
    if service not in PRICE_ALIASES:
        await update.message.reply_text("⚠️ service হতে হবে: gmail, fb, ig, usd, old, withdraw, binance অথবা ref")
        return
    key = PRICE_ALIASES[service]
    amount = format_amount(amount)
    settings[key] = amount
    save_settings()
    await update.message.reply_text(f"✅ {PRICE_LABELS[key]} এর দাম এখন {amount} টাকা।")


async def receive_price_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """প্যানেলের বাটন চাপার পর অ্যাডমিন নতুন দাম লিখলে এখানে আসে।"""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    key = context.user_data.get("price_key")
    if key in TEXT_LABELS:
        value = (update.message.text or "").strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_]{4,32}", value):
            await update.message.reply_text(
                "⚠️ সঠিক ইউজারনেম লিখুন (ইংরেজি অক্ষর, সংখ্যা বা _; কমপক্ষে ৪ অক্ষর)।\n"
                "বাতিল করতে /cancel:"
            )
            return PRICE_INPUT
        settings[key] = value
        if key == "support_username":
            settings["support_message"] = ""  # নইলে পুরনো কাস্টম বার্তাই দেখাত
        save_settings()
        context.user_data.pop("price_key", None)
        await update.message.reply_text(
            f"✅ {TEXT_LABELS[key]} এখন @{value}\n\n" + price_panel_text(),
            reply_markup=price_panel_markup(),
        )
        return ConversationHandler.END
    if key not in PRICE_LABELS:
        return ConversationHandler.END
    try:
        amount = float((update.message.text or "").strip())
        if amount < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ শুধু সংখ্যা লিখুন (বাতিল করতে /cancel):")
        return PRICE_INPUT
    amount = format_amount(amount)
    settings[key] = amount
    save_settings()
    context.user_data.pop("price_key", None)
    await update.message.reply_text(
        f"✅ {PRICE_LABELS[key]} এর দাম এখন {amount} টাকা।\n\n" + price_panel_text(),
        reply_markup=price_panel_markup(),
    )
    return ConversationHandler.END


async def add_app_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    txt = update.message.text.replace("/addapptask", "", 1).strip()
    if not txt:
        await update.message.reply_text("ব্যবহার: /addapptask [বিবরণ]")
        return
    app_tasks.append(txt)
    save_json_file(APP_FILE, app_tasks)
    await update.message.reply_text(
        f"✅ ছোট কাজ যোগ করা হয়েছে! মোট: {len(app_tasks)} টি।"
    )


async def clear_app_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    app_tasks.clear()
    save_json_file(APP_FILE, app_tasks)
    await update.message.reply_text("🗑️ সমস্ত ছোট কাজ মুছে ফেলা হয়েছে!")


def make_reject_handler(label, usage):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            return
        try:
            tid = int(context.args[0])
            st = get_user_stats(tid)
            if st["pending"] > 0:
                st["pending"] -= 1
            save_user_stats()
            await safe_send(
                context, tid, f"❌ আপনার {label}কাজটি বাতিল করা হয়েছে।"
            )
            await update.message.reply_text(
                f"✅ ইউজার {tid} এর {label}কাজ রিজেক্ট করা হয়েছে।"
            )
        except Exception:
            await update.message.reply_text(f"ব্যবহার: {usage} [User_ID]")

    return handler


reject_gmail = make_reject_handler("জিমেইল ", "/rejectg")
reject_facebook = make_reject_handler("ফেসবুক ", "/rejectfb")
reject_instagram = make_reject_handler("ইনস্টাগ্রাম ", "/rejectig")




async def add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        tid = int(context.args[0])
        amt = float(context.args[1])
    except Exception:
        await update.message.reply_text("ব্যবহার: /add [User_ID] [টাকা]")
        return

    st = get_user_stats(tid)
    st["balance"] += amt
    if st["pending"] > 0:
        st["pending"] -= 1
    st["completed"] += 1

    if not st["has_completed_first_task"]:
        st["has_completed_first_task"] = True
    save_user_stats()

    await update.message.reply_text(
        f"✅ ব্যালেন্স যোগ সফল। বর্তমান ব্যালেন্স: {st['balance']} টাকা।"
    )
    await safe_send(
        context, tid, f"🎉 আপনার কাজটি অনুমোদিত হয়েছে এবং অ্যাকাউন্টে {amt} টাকা যোগ করা হয়েছে!"
    )


async def cut_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        tid = int(context.args[0])
        amt = float(context.args[1])
    except Exception:
        await update.message.reply_text("ব্যবহার: /cut [User_ID] [টাকা]")
        return
    st = get_user_stats(tid)
    st["balance"] = max(0.0, st["balance"] - amt)
    save_user_stats()
    await update.message.reply_text(
        f"✅ ব্যালেন্স কাটা সফল। বর্তমান ব্যালেন্স: {st['balance']} টাকা।"
    )
    await safe_send(
        context, tid, f"⚠️ আপনার অ্যাকাউন্ট থেকে {amt} টাকা কেটে নেওয়া হয়েছে।"
    )


async def handle_admin_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """অ্যাডমিন একটি ছবি পাঠিয়ে ক্যাপশনে /paid [User_ID] লিখলে
    সেই ছবি পেমেন্ট প্রমাণ হিসেবে ইউজারকে পাঠানো হয়।"""
    if update.effective_user.id != ADMIN_ID:
        return
    caption = (update.message.caption or "").strip()
    if not caption.lower().startswith("/paid"):
        return

    parts = caption.split(maxsplit=2)
    if len(parts) < 2:
        await update.message.reply_text(
            "ব্যবহার: ছবি পাঠানোর সময় ক্যাপশনে লিখুন:\n/paid [User_ID] [বার্তা (ঐচ্ছিক)]"
        )
        return
    try:
        tid = int(parts[1])
    except ValueError:
        await update.message.reply_text("সঠিক User_ID দিন।")
        return

    extra_note = parts[2].strip() if len(parts) > 2 else ""
    default_msg = "✅ আপনার উইথড্র সফলভাবে সম্পন্ন হয়েছে! নিচে পেমেন্টের প্রমাণ দেওয়া হলো:"
    caption_to_user = f"{default_msg}\n\n{extra_note}" if extra_note else default_msg

    photo = update.message.photo[-1].file_id
    try:
        await context.bot.send_photo(chat_id=tid, photo=photo, caption=caption_to_user)
        await update.message.reply_text(f"✅ ইউজার {tid}-কে পেমেন্ট প্রমাণ পাঠানো হয়েছে।")
    except Exception:
        await update.message.reply_text("❌ ছবি পাঠানো যায়নি। User_ID ঠিক আছে কি না দেখুন।")


async def user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        tid = int(context.args[0])
    except Exception:
        await update.message.reply_text("ব্যবহার: /info [User_ID]")
        return

    if str(tid) not in user_data_db:
        await update.message.reply_text("⚠️ এই User_ID দিয়ে কোনো ইউজার পাওয়া যায়নি।")
        return

    st = get_user_stats(tid)
    status = "🚫 ব্লক করা" if tid in blocked_users else "✅ সক্রিয়"
    text = (
        f"👤 ইউজার তথ্য (<code>{tid}</code>)\n\n"
        f"✅ মোট সম্পন্ন কাজ: {st['completed']}\n"
        f"⏳ পেন্ডিং কাজ: {st['pending']}\n"
        f"💰 ব্যালেন্স: {st['balance']} টাকা\n"
        f"👥 মোট রেফার: {st['ref_count']} জন\n"
        f"💵 রেফারেল আয়: {st['ref_earnings']} টাকা\n"
        f"🔗 রেফারকারী: {st.get('referred_by') or 'নেই'}\n"
        f"📧 জিমেইল সাবমিট: {st.get('gmail_submitted', 0)} টি | রেফার বোনাস দেওয়া হয়েছে: {'হ্যাঁ' if st.get('ref_bonus_paid') else 'না'}\n"
        f"🔖 স্ট্যাটাস: {status}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def total_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        f"📊 মোট ইউজার: {len(load_registered_users())} জন\n"
        f"📂 জিমেইল কাজ: {len(gmail_tasks)} টি\n"
        f"📘 ফেসবুক কাজ: {len(facebook_tasks)} টি\n"
        f"📷 ইনস্টাগ্রাম কাজ: {len(instagram_tasks)} টি\n"
        f"📲 ছোট কাজ: {len(app_tasks)} টি"
    )


# ============ মেনু টেক্সট হ্যান্ডলার ============
async def block_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        tid = int(context.args[0])
    except Exception:
        await update.message.reply_text("ব্যবহার: /block [User_ID]")
        return
    if tid == ADMIN_ID:
        await update.message.reply_text("❌ নিজেকে ব্লক করা যাবে না।")
        return
    blocked_users.add(tid)
    save_blocked_users()
    await update.message.reply_text(f"🚫 ইউজার {tid}-কে ব্লক করা হয়েছে।")
    await safe_send(context, tid, "🚫 আপনাকে এই বট ব্যবহার থেকে ব্লক করা হয়েছে।")


async def unblock_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        tid = int(context.args[0])
    except Exception:
        await update.message.reply_text("ব্যবহার: /unblock [User_ID]")
        return
    if tid in blocked_users:
        blocked_users.discard(tid)
        save_blocked_users()
        await update.message.reply_text(f"✅ ইউজার {tid}-কে আনব্লক করা হয়েছে।")
        await safe_send(
            context, tid, "✅ আপনাকে আনব্লক করা হয়েছে, আপনি আবার বট ব্যবহার করতে পারবেন।"
        )
    else:
        await update.message.reply_text("ℹ️ এই ইউজার ব্লক করা নেই।")


async def check_blocked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """সব হ্যান্ডলারের আগে চলে; ব্লক করা ইউজারকে আটকে দেয়।"""
    user = update.effective_user
    if not user or user.id == ADMIN_ID:
        return
    if user.id in blocked_users:
        if update.callback_query:
            await update.callback_query.answer(
                "🚫 আপনাকে ব্লক করা হয়েছে।", show_alert=True
            )
        elif update.message:
            await update.message.reply_text("🚫 আপনাকে এই বট ব্যবহার থেকে ব্লক করা হয়েছে।")
        raise ApplicationHandlerStop


async def admin_send_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """অ্যাডমিন /msg [User_ID] [লেখা] দিয়ে যেকোনো ইউজারকে সরাসরি মেসেজ পাঠাতে পারেন।"""
    if update.effective_user.id != ADMIN_ID:
        return
    parts = (update.message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await update.message.reply_text("ব্যবহার: /msg [User_ID] [লেখা]")
        return
    try:
        tid = int(parts[1])
    except ValueError:
        await update.message.reply_text("সঠিক User_ID দিন।")
        return
    text = parts[2]
    ok = await safe_send(context, tid, f"📩 এডমিনের মেসেজ:\n\n{text}")
    if ok:
        await update.message.reply_text(f"✅ ইউজার {tid}-কে মেসেজ পাঠানো হয়েছে।")
    else:
        await update.message.reply_text("❌ মেসেজ পাঠানো যায়নি। User_ID ঠিক আছে কি না দেখুন।")


# ============ ভিডিও/ভয়েস/ছবি সহ নোটিশ ও পার্সোনাল মেসেজ ============
async def _broadcast_media(application, from_chat, msg_id, caption):
    sent = failed = 0
    for uid in load_registered_users():
        try:
            await application.bot.copy_message(
                chat_id=uid, from_chat_id=from_chat, message_id=msg_id, caption=caption
            )
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # টেলিগ্রামের গতি-সীমা এড়াতে
    try:
        await application.bot.send_message(
            from_chat, f"✅ নোটিশ পাঠানো শেষ!\n📨 সফল: {sent} জন\n❌ ব্যর্থ: {failed} জন"
        )
    except Exception:
        pass


async def media_notice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ভিডিও/ভয়েস/ছবি/অডিও পাঠিয়ে ক্যাপশনে /notice লিখলে সব ইউজারের কাছে যাবে।
    ক্যাপশনের /notice এর পরের লেখাটাই নিচে দেখাবে।"""
    if update.effective_user.id != ADMIN_ID:
        return
    msg = update.message
    txt = (msg.caption or "").replace("/notice", "", 1).strip()
    caption = f"📢 নোটিশ:\n\n{txt}" if txt else "📢 নোটিশ"
    if len(caption) > 1000:
        await msg.reply_text("⚠️ লেখা বেশি বড়। ১০০০ অক্ষরের মধ্যে লিখুন।")
        return
    await msg.reply_text("⏳ সব ইউজারকে পাঠানো শুরু হয়েছে, শেষ হলে জানাব।")
    context.application.create_task(
        _broadcast_media(context.application, msg.chat_id, msg.message_id, caption)
    )


async def media_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ভয়েস/ভিডিও/ছবি পাঠিয়ে ক্যাপশনে `/msg USER_ID লেখা` লিখলে শুধু ওই ইউজারই পাবে।"""
    if update.effective_user.id != ADMIN_ID:
        return
    msg = update.message
    parts = (msg.caption or "").split(maxsplit=2)
    try:
        tid = int(parts[1])
    except (IndexError, ValueError):
        await msg.reply_text(
            "ব্যবহার: ভয়েস/ভিডিও পাঠিয়ে ক্যাপশনে লিখুন:\n/msg [User_ID] [লেখা]"
        )
        return
    text = parts[2] if len(parts) > 2 else ""
    caption = f"📩 এডমিনের মেসেজ:\n\n{text}" if text else "📩 এডমিনের মেসেজ"
    try:
        await context.bot.copy_message(
            chat_id=tid, from_chat_id=msg.chat_id, message_id=msg.message_id, caption=caption
        )
        await msg.reply_text(f"✅ ইউজার {tid}-কে পাঠানো হয়েছে।")
    except Exception:
        await msg.reply_text("❌ পাঠানো যায়নি। User_ID ঠিক আছে কি না বা ইউজার বট ব্লক করেছে কি না দেখুন।")


async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    user = update.effective_user
    save_user_id(user.id)
    lang = get_lang(user.id)
    if text.startswith("/"):
        return

    # রেফারেল
    if is_btn(text, "referral"):
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={user.id}"
        st = get_user_stats(user.id)
        if lang == "en":
            msg = (
                "🎁 Referral & Earn Program 🚀\n\nInvite friends & earn!\n\n"
                f"👥 Link:\n{ref_link}\n\n"
                f"💰 Per referral: {format_amount(settings['referral_bonus'])} Taka\n\n"
                f"• Referrals: {st['ref_count']}\n• Earnings: {st['ref_earnings']} Taka"
            )
        else:
            msg = (
                "🎁 রেফারেল প্রোগ্রাম 🚀\n\nবন্ধুদের ইনভাইট করে ইনকাম করুন!\n\n"
                f"👥 লিংক:\n{ref_link}\n\n"
                f"💰 ১ জনকে রেফার করলে পাবেন: {format_amount(settings['referral_bonus'])} টাকা\n\n"
                f"• মোট রেফার: {st['ref_count']} জন\n• মোট আয়: {st['ref_earnings']} টাকা"
            )
        extra = settings.get("referral_extra", "").strip()
        if extra:
            msg += f"\n\n{extra}"
        await update.message.reply_text(msg, reply_markup=get_main_keyboard(lang))
        return

    # কাজ ক্যাটাগরি
    if is_btn(text, "tasks"):
        msg = "🛠️ কাজের ক্যাটাগরি:" if lang == "bn" else "🛠️ Task Categories:"
        await update.message.reply_text(msg, reply_markup=get_task_sub_keyboard(lang))
        return

    if is_btn(text, "cancel"):
        await update.message.reply_text(
            "প্রধান মেনু:" if lang == "bn" else "Main Menu:",
            reply_markup=get_main_keyboard(lang),
        )
        return

    if is_btn(text, "gmail"):
        price = settings["gmail_price"]
        label = (
            f"একটি জিমেইল একাউন্টের দাম {price} টাকা 💸"
            if lang == "bn"
            else f"One Gmail account price is {price} Taka 💸"
        )
        kb = [[InlineKeyboardButton(label, callback_data="gmail_details")]]
        await update.message.reply_text(
            "📧 জিমেইল কাজ তথ্য:" if lang == "bn" else "📧 Gmail Task Info:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if is_btn(text, "facebook"):
        price = settings["fb_price"]
        label = (
            f"একটি ফেসবুক একাউন্টের দাম {price} টাকা 💸"
            if lang == "bn"
            else f"One Facebook account price is {price} Taka 💸"
        )
        kb = [[InlineKeyboardButton(label, callback_data="fb_details")]]
        await update.message.reply_text(
            "📘 ফেসবুক কাজ তথ্য:" if lang == "bn" else "📘 Facebook Task Info:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if is_btn(text, "instagram"):
        price = settings["ig_price"]
        label = (
            f"একটি ইনস্টাগ্রাম একাউন্টের দাম {price} টাকা 💸"
            if lang == "bn"
            else f"One Instagram account price is {price} Taka 💸"
        )
        kb = [[InlineKeyboardButton(label, callback_data="insta_details")]]
        await update.message.reply_text(
            "📷 ইনস্টাগ্রাম কাজ তথ্য:" if lang == "bn" else "📷 Instagram Task Info:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if is_btn(text, "old_gmail"):
        price = settings["old_gmail_price"]
        label = (
            f"পুরাতন জিমেইলের দাম {price} টাকা 💸"
            if lang == "bn"
            else f"Old Gmail price is {price} Taka 💸"
        )
        kb = [[ibtn(label, "old_gmail_info", GREEN)]]
        await update.message.reply_text(
            "📮 পুরাতন জিমেইল বিক্রি:" if lang == "bn" else "📮 Sell Old Gmail:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if is_btn(text, "language"):
        kb = [
            [ibtn("🇧🇩 বাংলা", "lang_bn", BLUE)],
            [ibtn("🇬🇧 English", "lang_en", GREEN)],
        ]
        await update.message.reply_text(
            "ভাষা নির্বাচন করুন / Select language:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if is_btn(text, "sell_usd"):
        kb = [[InlineKeyboardButton(f"১ ডলার = {settings['usd_rate']} টাকা 💸", callback_data="sell_usd_rate")]]
        await update.message.reply_text(
            "🪙 ডলার বিক্রি সার্ভিস:", reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    if is_btn(text, "micro"):
        if not app_tasks:
            await update.message.reply_text(
                "⚠️ কোনো কাজ নেই!" if lang == "bn" else "⚠️ No tasks!",
                reply_markup=get_task_sub_keyboard(lang),
            )
            return
        task_text = app_tasks[0]
        context.user_data["current_task_text"] = task_text
        context.user_data["current_task_type"] = "app"
        head = "📲 ছোট কাজের বিবরণ:" if lang == "bn" else "📲 Micro Task:"
        sent = await update.message.reply_text(
            f"{head}\n\n{task_text}", reply_markup=get_task_keyboard(lang)
        )
        context.user_data["last_task_msg_id"] = sent.message_id
        return

    # ব্যালেন্স
    if is_btn(text, "balance"):
        st = get_user_stats(user.id)
        if lang == "en":
            msg = (
                "👤 Your info:\n\n"
                f"✅ Completed: {st['completed']}\n⏳ Pending: {st['pending']}\n"
                f"💰 Balance: {st['balance']} Taka"
            )
        else:
            msg = (
                "👤 আপনার তথ্য:\n\n"
                f"✅ কাজ করেছেন: {st['completed']}\n⏳ পেন্ডিং: {st['pending']}\n"
                f"💰 ব্যালেন্স: {st['balance']} টাকা"
            )
        await update.message.reply_text(msg, reply_markup=get_main_keyboard(lang))
        return

    # উইথড্র
    if is_btn(text, "withdraw"):
        kb = [
            [InlineKeyboardButton("বিকাশ", callback_data="withdraw_bkash")],
            [InlineKeyboardButton("নগদ", callback_data="withdraw_nagad")],
            [InlineKeyboardButton("মোবাইল রিচার্জ", callback_data="withdraw_recharge")],
            [InlineKeyboardButton("Binance", callback_data="withdraw_binance")],
        ]
        await update.message.reply_text("💳 টাকা তুলুন:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if is_btn(text, "support"):
        await update.message.reply_text(
            get_support_message(), reply_markup=get_main_keyboard(lang)
        )
        return

    if is_btn(text, "newbie"):
        kb = [[InlineKeyboardButton("চ্যানেল 🔗", url="https://t.me/EasyIncomeBD_Payment")]]
        await update.message.reply_text("পেমেন্ট প্রুফ চ্যানেল:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if is_btn(text, "cancel_task"):
        for mid in (update.message.message_id, context.user_data.get("last_task_msg_id")):
            if mid:
                try:
                    await context.bot.delete_message(chat_id=user.id, message_id=mid)
                except Exception:
                    pass
        clear_task_state(context)
        await update.message.reply_text(
            "প্রধান মেনু:" if lang == "bn" else "Main Menu:",
            reply_markup=get_main_keyboard(lang),
        )
        return

    # মেনুর কোনো অপশনের সাথে মিল নেই: /msg একমুখী, তাই ইউজারকে কিছু ফরোয়ার্ড করা হয় না
    return


# ============ সাধারণ বাটন (কনভারসেশনের বাইরে) ============
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user
    lang = get_lang(user.id)

    if data.startswith("lang_"):
        sl = data.split("_")[1]
        get_user_stats(user.id)["lang"] = sl
        save_user_stats()
        await query.message.reply_text(
            "ভাষা সেট করা হয়েছে।" if sl == "bn" else "Language set.",
            reply_markup=get_main_keyboard(sl),
        )
        return

    if data == "old_gmail_info":
        await query.message.reply_text(
            f"✅ জিমেইল বিক্রি করতে @{settings['support_username']}\n"
            "এই আইডিতে মেসেজ দিয়েন",
            reply_markup=get_task_sub_keyboard(lang),
        )
        return

    if data == "sell_usd_rate":
        kb = [[InlineKeyboardButton("ডলার বিক্রি শুরু 💵", callback_data="sell_usd_start")]]
        await query.message.reply_text(
            f"১ ডলার = {settings['usd_rate']} টাকা 💸", reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    if data == "gmail_details":
        if not gmail_tasks:
            await query.message.reply_text(
                "⚠️ জিমেইল কাজ নেই!", reply_markup=get_task_sub_keyboard(lang)
            )
            return
        task_text = gmail_tasks[0]
        context.user_data["current_task_text"] = task_text
        context.user_data["current_task_type"] = "gmail"
        sent = await query.message.reply_text(
            f"📌 জিমেইল কাজ:\n\n{task_text}", reply_markup=get_task_keyboard(lang)
        )
        context.user_data["last_task_msg_id"] = sent.message_id
        return

    if data == "fb_details":
        if not facebook_tasks:
            await query.message.reply_text(
                "⚠️ ফেসবুক কাজ নেই!", reply_markup=get_task_sub_keyboard(lang)
            )
            return
        task_text = facebook_tasks[0]
        context.user_data["current_task_text"] = task_text
        context.user_data["current_task_type"] = "facebook"
        fb_kb = [[InlineKeyboardButton("✅ Done", callback_data="fb_done")]]
        await query.message.reply_text(
            f"📘 ফেসবুক কাজের নির্দেশিকা:\n\n{task_text}",
            reply_markup=InlineKeyboardMarkup(fb_kb),
        )
        return

    if data == "fb_complete":
        st = get_user_stats(user.id)
        st["pending"] += 1
        save_user_stats()

        tdesc = context.user_data.get("current_task_text", "বিবরণ নেই")
        fb_num = context.user_data.get("fb_number", "দেওয়া হয়নি")
        if tdesc in facebook_tasks:
            facebook_tasks.remove(tdesc)
            save_json_file(FACEBOOK_FILE, facebook_tasks)

        admin_text = (
            "📘 ফেসবুক কাজ জমা হয়েছে\n"
            f"ইউজার: {esc(user.first_name)} (<code>{user.id}</code>)\n"
            f"ফেসবুক নাম্বার: <code>{esc(fb_num)}</code>\n"
            f"কাজের বিবরণ:\n{esc(tdesc[:600])}"
        )
        await notify_admin(context, admin_text)
        clear_task_state(context)
        context.user_data.pop("fb_number", None)

        done_msg = (
            "✅ আপনার কাজ সম্পন্ন হয়েছে!\n\n"
            "এটি এডমিন প্যানেলে রিভিউতে আছে। সব ঠিক থাকলে ১২-৭২ ঘণ্টার মধ্যে "
            "পেমেন্ট আপনার ব্যালেন্সে যুক্ত করে দেওয়া হবে।"
        )
        await query.message.reply_text(done_msg, reply_markup=get_main_keyboard(lang))
        return

    if data == "insta_details":
        if not instagram_tasks:
            await query.message.reply_text(
                "⚠️ ইনস্টাগ্রাম কাজ নেই!", reply_markup=get_task_sub_keyboard(lang)
            )
            return
        task_text = instagram_tasks[0]
        context.user_data["current_task_text"] = task_text
        context.user_data["current_task_type"] = "instagram"
        ig_kb = [[InlineKeyboardButton("✅ Done", callback_data="insta_done")]]
        await query.message.reply_text(
            f"📷 ইনস্টাগ্রাম কাজের নির্দেশিকা:\n\n{task_text}",
            reply_markup=InlineKeyboardMarkup(ig_kb),
        )
        return

    if data == "insta_done":
        st = get_user_stats(user.id)
        st["pending"] += 1
        save_user_stats()

        tdesc = context.user_data.get("current_task_text", "বিবরণ নেই")
        if tdesc in instagram_tasks:
            instagram_tasks.remove(tdesc)
            save_json_file(INSTAGRAM_FILE, instagram_tasks)

        admin_text = (
            "📷 ইনস্টাগ্রাম কাজ জমা হয়েছে\n"
            f"ইউজার: {esc(user.first_name)} (<code>{user.id}</code>)\n"
            f"কাজের বিবরণ:\n{esc(tdesc[:600])}"
        )
        await notify_admin(context, admin_text)
        clear_task_state(context)

        done_msg = (
            "✅ আপনার কাজ সম্পন্ন হয়েছে!\n\n"
            "আপনি ২৪ ঘণ্টার ভিতরে আপনার ব্যালেন্সে টাকা পেয়ে যাবেন, যদি কাজটি সঠিকভাবে হয়ে থাকে।"
        )
        await query.message.reply_text(done_msg, reply_markup=get_main_keyboard(lang))
        return


# ============ কনভারসেশন এন্ট্রি ============
async def conv_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user
    lang = get_lang(user.id)
    st = get_user_stats(user.id)

    if data.startswith("setprice_"):
        if user.id != ADMIN_ID:
            return ConversationHandler.END
        key = data[len("setprice_"):]
        if key in TEXT_LABELS:
            context.user_data["price_key"] = key
            await query.message.reply_text(
                f"{TEXT_LABELS[key]}\nবর্তমান: @{settings[key]}\n\n"
                "নতুন ইউজারনেম লিখুন (@ সহ বা ছাড়া, বাতিল করতে /cancel):"
            )
            return PRICE_INPUT
        if key not in PRICE_LABELS:
            return ConversationHandler.END
        context.user_data["price_key"] = key
        await query.message.reply_text(
            f"{PRICE_LABELS[key]}\nবর্তমান: {settings[key]} টাকা\n\n"
            "নতুন সংখ্যা লিখুন (বাতিল করতে /cancel):"
        )
        return PRICE_INPUT

    if data == "fb_done":
        if not context.user_data.get("current_task_text"):
            await query.message.reply_text(
                "⚠️ আপনার কোনো সক্রিয় ফেসবুক কাজ নেই। আগে কাজ নিন।",
                reply_markup=get_main_keyboard(lang),
            )
            return ConversationHandler.END
        await query.message.reply_text("🔥ফেসবুক একাউন্টের নাম্বার দিন:")
        return FB_NUMBER

    if data == "sell_usd_start":
        await query.message.reply_text("কত ডলার বিক্রি করবেন লিখুন:")
        return SELL_USD_AMT

    if data == "sell_usd_done":
        await query.message.reply_text("পেমেন্টের স্ক্রিনশট দিন:")
        return SELL_USD_PROOF

    if data in ("withdraw_bkash", "withdraw_nagad", "withdraw_recharge"):
        if st["balance"] < settings["withdraw_min"]:
            await query.message.reply_text(
                f"❌ সর্বনিম্ন {settings['withdraw_min']}৳ হওয়া লাগবে।",
                reply_markup=get_main_keyboard(lang),
            )
            return ConversationHandler.END
        method = {
            "withdraw_bkash": "বিকাশ",
            "withdraw_nagad": "নগদ",
            "withdraw_recharge": "মোবাইল রিচার্জ",
        }[data]
        context.user_data["withdraw_method"] = method
        context.user_data["withdraw_min"] = settings["withdraw_min"]
        await query.message.reply_text(f"আপনার {method} নাম্বার দিন:")
        return WITHDRAW_NUM

    if data == "withdraw_binance":
        if st["balance"] < settings["withdraw_min_binance"]:
            await query.message.reply_text(
                f"❌ সর্বনিম্ন ৳{settings['withdraw_min_binance']} হওয়া লাগবে।",
                reply_markup=get_main_keyboard(lang),
            )
            return ConversationHandler.END
        context.user_data["withdraw_method"] = "Binance"
        context.user_data["withdraw_min"] = settings["withdraw_min_binance"]
        await query.message.reply_text("USDT (BEP-20) অ্যাড্রেস দিন:")
        return WITHDRAW_NUM

    return ConversationHandler.END


# ============ ডলার বিক্রি ============
async def receive_sell_usd_amt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amt = float(update.message.text)
        if amt <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("সঠিক সংখ্যায় লিখুন:")
        return SELL_USD_AMT
    context.user_data["sell_usd_amt"] = amt
    kb = [[InlineKeyboardButton("Payment Done ✅", callback_data="sell_usd_done")]]
    await update.message.reply_text(
        f"ডলার পাঠান: 1150427115\nটাকা পাবেন: {amt * settings['usd_rate']} BDT",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return ConversationHandler.END


async def receive_sell_usd_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        context.user_data["sell_usd_photo"] = update.message.photo[-1].file_id
        await update.message.reply_text("আপনার বিকাশ/নগদ নাম্বার দিন:")
        return SELL_USD_ACC
    await update.message.reply_text("স্ক্রিনশট পাঠান:")
    return SELL_USD_PROOF


async def receive_sell_usd_acc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    acc = update.message.text
    amt = context.user_data.get("sell_usd_amt", 0)
    text = (
        "🪙 ডলার সেল রিকোয়েস্ট\n"
        f"ইউজার: {esc(user.first_name)} (<code>{user.id}</code>)\n"
        f"পরিমাণ: {amt} USD\n"
        f"অ্যাকাউন্ট: <code>{esc(acc)}</code>"
    )
    await notify_admin(context, text, photo=context.user_data.get("sell_usd_photo"))
    context.user_data.pop("sell_usd_photo", None)
    await update.message.reply_text(
        "✅ রিকোয়েস্ট জমা হয়েছে!", reply_markup=get_main_keyboard(get_lang(user.id))
    )
    return ConversationHandler.END


# ============ ডিপোজিট ============




# ============ উইথড্র ============
async def receive_withdraw_num(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["withdraw_info"] = update.message.text
    await update.message.reply_text("কত টাকা উইথড্র করবেন লিখুন:")
    return WITHDRAW_AMT


async def receive_withdraw_amt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    lang = get_lang(user.id)
    try:
        amt = float(update.message.text)
    except ValueError:
        await update.message.reply_text("সঠিক সংখ্যা লিখুন:")
        return WITHDRAW_AMT

    min_amt = context.user_data.get("withdraw_min", settings["withdraw_min"])
    if amt < min_amt:
        await update.message.reply_text(f"❌ সর্বনিম্ন {min_amt}৳ উইথড্র করতে হবে। আবার লিখুন:")
        return WITHDRAW_AMT

    st = get_user_stats(user.id)
    if amt > st["balance"]:
        await update.message.reply_text(
            "❌ পর্যাপ্ত ব্যালেন্স নেই।", reply_markup=get_main_keyboard(lang)
        )
        return ConversationHandler.END

    st["balance"] -= amt
    save_user_stats()
    text = (
        "💸 উইথড্র রিকোয়েস্ট\n"
        f"ইউজার: {esc(user.first_name)} (<code>{user.id}</code>)\n"
        f"মাধ্যম: {esc(context.user_data.get('withdraw_method', ''))}\n"
        f"পরিমাণ: {amt}\n"
        f"তথ্য: <code>{esc(context.user_data.get('withdraw_info'))}</code>"
    )
    await notify_admin(context, text)
    await update.message.reply_text(
        f"✅ {amt} টাকার উইথড্র রিকোয়েস্ট জমা হয়েছে!",
        reply_markup=get_main_keyboard(lang),
    )
    return ConversationHandler.END


# ============ কাজের স্ক্রিনশট জমা ============
async def ask_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    if not context.user_data.get("current_task_text"):
        await update.message.reply_text(
            "⚠️ আপনার কোনো সক্রিয় কাজ নেই। আগে কাজ নিন।",
            reply_markup=get_main_keyboard(lang),
        )
        return ConversationHandler.END
    await update.message.reply_text("📸 স্ক্রিনশট জমা দিন:")
    return PROOF_SUBMIT


async def check_referral_bonus(context, user_id):
    """গোপন শর্ত: নতুন ইউজার ২টা জিমেইল সাবমিট করলে তবেই রেফারকারী বোনাস পায়।
    বোনাসের পরিমাণ অ্যাডমিন সেটিং (referral_bonus) থেকে আসে।"""
    st = get_user_stats(user_id)
    if st.get("ref_bonus_paid") or st.get("gmail_submitted", 0) < 2:
        return
    rid = st.get("referred_by")
    if not rid:
        return
    st["ref_bonus_paid"] = True
    bonus = float(settings.get("referral_bonus", 5))
    rst = get_user_stats(rid)
    rst["balance"] += bonus
    rst["ref_earnings"] += bonus
    save_user_stats()
    await safe_send(
        context, rid, f"🎉 আপনার সফল রেফারেলের জন্য {format_amount(bonus)} টাকা বোনাস যোগ হয়েছে! 💸"
    )


async def receive_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    lang = get_lang(user.id)

    if not update.message.photo:
        await update.message.reply_text("দয়া করে স্ক্রিনশট (ছবি) পাঠান:")
        return PROOF_SUBMIT

    photo = update.message.photo[-1].file_id
    st = get_user_stats(user.id)
    st["pending"] += 1

    ttype = context.user_data.get("current_task_type", "gmail")
    tdesc = context.user_data.get("current_task_text", "বিবরণ নেই")
    if ttype == "gmail":
        st["gmail_submitted"] = st.get("gmail_submitted", 0) + 1
    save_user_stats()

    if ttype == "gmail" and tdesc in gmail_tasks:
        gmail_tasks.remove(tdesc)
        save_json_file(GMAIL_FILE, gmail_tasks)
    elif ttype == "app" and tdesc in app_tasks:
        app_tasks.remove(tdesc)
        save_json_file(APP_FILE, app_tasks)

    text = (
        "📩 স্ক্রিনশট জমা হয়েছে\n"
        f"ইউজার: {esc(user.first_name)} (<code>{user.id}</code>)\n"
        f"কাজের বিবরণ:\n{esc(tdesc[:600])}"
    )
    await notify_admin(context, text, photo=photo)
    clear_task_state(context)
    await update.message.reply_text("✅ স্ক্রিনশট সাবমিট হয়েছে!", reply_markup=get_main_keyboard(lang))
    if ttype == "gmail":
        await check_referral_bonus(context, user.id)
    return ConversationHandler.END


# ============ ফেসবুক কাজ ============
async def receive_fb_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    number = (update.message.text or "").strip()
    if not number:
        await update.message.reply_text("🔥ফেসবুক একাউন্টের নাম্বার দিন:")
        return FB_NUMBER
    context.user_data["fb_number"] = number
    kb = [[InlineKeyboardButton("✅ কাজ সম্পন্ন", callback_data="fb_complete")]]
    await update.message.reply_text(
        "✅ নাম্বার গ্রহণ করা হয়েছে। কাজ সম্পন্ন করতে নিচের বাটনে চাপ দিন।",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return ConversationHandler.END


# ============ ক্যান্সেল / রাউটিং ============
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    await update.message.reply_text("বাতিল করা হয়েছে।", reply_markup=get_main_keyboard(lang))
    return ConversationHandler.END


async def cancel_and_route(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """কনভারসেশনের মাঝে মেনু বাটন চাপলে সেটা চালু করে দেয়।"""
    if update.message.text in SUBMIT_TEXTS:
        return await ask_proof(update, context)
    await handle_text_messages(update, context)
    return ConversationHandler.END


async def setup_menu_commands(application):
    """চ্যাটবক্সের পাশের ☰ বাটনে কমান্ড লিস্ট দেখানোর জন্য।"""
    user_commands = [
        BotCommand("start", "বট শুরু করুন"),
    ]
    admin_commands = [
        BotCommand("start", "বট শুরু করুন"),
        BotCommand("prices", "⚙️ দাম, উইথড্র লিমিট ও সাপোর্ট আইডি বদলান"),
        BotCommand("setprice", "✏️ কমান্ডে দাম বদলান (যেমন: /setprice old 12)"),
        BotCommand("backup", "💾 সব ইউজারের ডাটা এক ফাইলে ডাউনলোড করুন"),
        BotCommand("info", "ইউজারের ব্যালেন্স/কাজ/রেফার দেখুন"),
        BotCommand("users", "মোট ইউজার ও কাজের সংখ্যা"),
        BotCommand("add", "ইউজারের ব্যালেন্স যোগ করুন"),
        BotCommand("cut", "ইউজারের ব্যালেন্স কাটুন"),
        BotCommand("msg", "ইউজারকে মেসেজ (ভয়েস/ভিডিও পাঠিয়ে ক্যাপশনেও চলে)"),
        BotCommand("block", "ইউজারকে ব্লক করুন"),
        BotCommand("unblock", "ইউজারকে আনব্লক করুন"),
        BotCommand("addtask", "জিমেইল কাজ যোগ করুন"),
        BotCommand("cleartasks", "সব জিমেইল কাজ মুছুন"),
        BotCommand("addfbtask", "ফেসবুক কাজ যোগ করুন"),
        BotCommand("clearfbtasks", "সব ফেসবুক কাজ মুছুন"),
        BotCommand("addigtask", "ইনস্টাগ্রাম কাজ যোগ করুন"),
        BotCommand("clearigtasks", "সব ইনস্টাগ্রাম কাজ মুছুন"),
        BotCommand("showprices", "বর্তমান সব দাম দেখুন"),
        BotCommand("setsupportuser", "সাপোর্ট ইউজারনেম বদলান"),
        BotCommand("setsupportmsg", "সাপোর্ট বাটনের পুরো বার্তা কাস্টম করুন"),
        BotCommand("resetsupportmsg", "সাপোর্ট বার্তা ডিফল্টে ফেরান"),
        BotCommand("setreferraltext", "রেফারেল বার্তায় অতিরিক্ত লেখা যোগ করুন"),
        BotCommand("clearreferraltext", "রেফারেলের অতিরিক্ত লেখা মুছুন"),
        BotCommand("addapptask", "মাইক্রো কাজ যোগ করুন"),
        BotCommand("clearapptasks", "সব মাইক্রো কাজ মুছুন"),
        BotCommand("rejectg", "জিমেইল কাজ রিজেক্ট করুন"),
        BotCommand("rejectfb", "ফেসবুক কাজ রিজেক্ট করুন"),
        BotCommand("rejectig", "ইনস্টাগ্রাম কাজ রিজেক্ট করুন"),
        BotCommand("paid", "⚠️ ছবির ক্যাপশনে লিখুন: পেমেন্ট প্রুফ পাঠাতে"),
        BotCommand("online", "সব ইউজারকে অনলাইন নোটিশ"),
        BotCommand("notice", "সব ইউজারকে নোটিশ (ভিডিও/ভয়েস পাঠিয়ে ক্যাপশনেও চলে)"),
    ]
    application.create_task(auto_backup_loop(application))
    if os.getenv("RENDER") and not MONGODB_URI:
        try:
            await application.bot.send_message(
                ADMIN_ID,
                "⚠️ MONGODB_URI সেট করা নেই! Render রিস্টার্ট বা আপডেটে সব ইউজারের "
                "ব্যালেন্স মুছে যাবে। এখনই MongoDB সেট করুন।",
            )
        except Exception:
            pass
    await application.bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    await application.bot.set_my_commands(
        admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID)
    )


# ============ মেইন ============
def main():
    start_keepalive()
    import telegram

    print("MOMIN_NEW v2 চালু হচ্ছে | python-telegram-bot", telegram.__version__)
    app = ApplicationBuilder().token(TOKEN).post_init(setup_menu_commands).build()

    # ব্লক করা ইউজারকে সবকিছুর আগে আটকে দিতে group=-1 (সবার আগে চলে)
    app.add_handler(MessageHandler(filters.ALL, check_blocked), group=-3)
    app.add_handler(CallbackQueryHandler(check_blocked), group=-3)
    # চ্যানেলে জয়েন না থাকলে মেনু/বাটন কিছুই কাজ করবে না
    app.add_handler(MessageHandler(filters.ALL, join_gate), group=-2)
    app.add_handler(CallbackQueryHandler(join_gate), group=-2)

    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                conv_callback,
                pattern=r"^(withdraw_|sell_usd_start$|sell_usd_done$|fb_done$|setprice_)",
            ),
            MessageHandler(
                filters.Regex("^(" + "|".join(re.escape(t) for t in SUBMIT_TEXTS) + ")$"),
                ask_proof,
            ),
        ],
        states={
            WITHDRAW_NUM: [MessageHandler(TEXT_INPUT, receive_withdraw_num)],
            WITHDRAW_AMT: [MessageHandler(TEXT_INPUT, receive_withdraw_amt)],
            PRICE_INPUT: [MessageHandler(TEXT_INPUT, receive_price_input)],
            SELL_USD_AMT: [MessageHandler(TEXT_INPUT, receive_sell_usd_amt)],
            SELL_USD_PROOF: [MessageHandler(filters.PHOTO | TEXT_INPUT, receive_sell_usd_proof)],
            SELL_USD_ACC: [MessageHandler(TEXT_INPUT, receive_sell_usd_acc)],
            PROOF_SUBMIT: [MessageHandler(filters.PHOTO | TEXT_INPUT, receive_proof)],
            FB_NUMBER: [MessageHandler(TEXT_INPUT, receive_fb_number)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(MENU_FILTER, cancel_and_route),
        ],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(
        MessageHandler(filters.Document.ALL & filters.CaptionRegex(r"^/restore"), restore_command)
    )
    app.add_handler(CommandHandler("online", online_notice))
    app.add_handler(CommandHandler("notice", notice_command))
    app.add_handler(
        MessageHandler(
            (filters.VIDEO | filters.VOICE | filters.PHOTO | filters.AUDIO | filters.Document.ALL) & filters.CaptionRegex(r"^/notice"), media_notice
        )
    )
    app.add_handler(
        MessageHandler(
            (filters.VIDEO | filters.VOICE | filters.PHOTO | filters.AUDIO | filters.Document.ALL) & filters.CaptionRegex(r"^/msg"), media_to_user
        )
    )
    app.add_handler(CommandHandler("settings", prices_panel))
    app.add_handler(CommandHandler("addtask", add_task))
    app.add_handler(CommandHandler("cleartasks", clear_tasks))
    app.add_handler(CommandHandler("addfbtask", add_fb_task))
    app.add_handler(CommandHandler("clearfbtasks", clear_fb_tasks))
    app.add_handler(CommandHandler("addigtask", add_ig_task))
    app.add_handler(CommandHandler("clearigtasks", clear_ig_tasks))
    app.add_handler(CommandHandler("setprice", set_price))
    app.add_handler(CommandHandler("showprices", prices_panel))
    app.add_handler(CommandHandler("prices", prices_panel))
    app.add_handler(CommandHandler("setsupportuser", set_support_username))
    app.add_handler(CommandHandler("setsupportmsg", set_support_message))
    app.add_handler(CommandHandler("resetsupportmsg", reset_support_message))
    app.add_handler(CommandHandler("setreferraltext", set_referral_text))
    app.add_handler(CommandHandler("clearreferraltext", clear_referral_text))
    app.add_handler(CommandHandler("addapptask", add_app_task))
    app.add_handler(CommandHandler("clearapptasks", clear_app_tasks))
    app.add_handler(CommandHandler("rejectg", reject_gmail))
    app.add_handler(CommandHandler("rejectfb", reject_facebook))
    app.add_handler(CommandHandler("rejectig", reject_instagram))
    app.add_handler(CommandHandler("add", add_balance))
    app.add_handler(CommandHandler("cut", cut_balance))
    app.add_handler(CommandHandler("users", total_users))
    app.add_handler(CommandHandler("info", user_info))
    app.add_handler(CommandHandler("block", block_user_cmd))
    app.add_handler(CommandHandler("unblock", unblock_user_cmd))
    app.add_handler(CommandHandler("msg", admin_send_message))
    app.add_handler(
        MessageHandler(filters.PHOTO & filters.User(user_id=ADMIN_ID), handle_admin_photo)
    )
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    print("Bot is running successfully with all features...")
    app.run_polling()


if __name__ == "__main__":
    main()
