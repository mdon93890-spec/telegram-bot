import asyncio, inspect
asyncio.iscoroutinefunction = inspect.iscoroutinefunction
import html
import json
import logging
import os
import re
import shutil
import threading
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable সেট করা নেই (Render > Environment-এ দিন)")
ADMIN_ID = int(os.getenv("ADMIN_ID") or 8361020821)

# ডেটা ফোল্ডার: হোস্টিং-এ পারসিস্টেন্ট ভলিউম থাকলে DATA_DIR ENV দিয়ে সেই পাথ দিন (যেমন /data)
DATA_DIR = os.getenv("DATA_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data"
)
os.makedirs(DATA_DIR, exist_ok=True)
BACKUP_INTERVAL_HOURS = float(os.getenv("BACKUP_INTERVAL_HOURS") or 6)

USERS_FILE = os.path.join(DATA_DIR, "users.json")
GMAIL_FILE = os.path.join(DATA_DIR, "gmail_tasks.json")
APP_FILE = os.path.join(DATA_DIR, "app_tasks.json")
FACEBOOK_FILE = os.path.join(DATA_DIR, "facebook_tasks.json")
INSTAGRAM_FILE = os.path.join(DATA_DIR, "instagram_tasks.json")
USER_STATS_FILE = os.path.join(DATA_DIR, "user_stats.json")
BLOCKED_FILE = os.path.join(DATA_DIR, "blocked_users.json")
ALL_DATA_FILES = [
    USERS_FILE, GMAIL_FILE, APP_FILE, FACEBOOK_FILE,
    INSTAGRAM_FILE, USER_STATS_FILE, BLOCKED_FILE,
]

(
    PROOF_SUBMIT,
    WITHDRAW_NUM,
    WITHDRAW_AMT,
    DEPOSIT_AMT,
    DEPOSIT_TRX,
    SELL_USD_AMT,
    SELL_USD_PROOF,
    SELL_USD_ACC,
    FB_NUMBER,
) = range(9)

USD_RATE = 115
FB_PRICE_TEXT_BN = "একটি ফেসবুক একাউন্টের দাম ৩ টাকা 💸"
FB_PRICE_TEXT_EN = "One Facebook account price is 3 Taka 💸"
IG_PRICE_TEXT_BN = "একটি ইনস্টাগ্রাম একাউন্টের দাম ২ টাকা 💸"
IG_PRICE_TEXT_EN = "One Instagram account price is 2 Taka 💸"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


# ============ ফাইল সেভ/লোড ============
_save_lock = threading.Lock()


def load_json_file(filename, default_val):
    # মূল ফাইল নষ্ট হলে .bak ফাইল থেকে ডেটা ফিরিয়ে আনে
    for candidate in (filename, filename + ".bak"):
        if os.path.exists(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logging.error("Load failed for %s: %s", candidate, e)
    return default_val


def save_json_file(filename, data):
    """অ্যাটমিক সেভ: আগে টেম্প ফাইলে লিখে তারপর রিপ্লেস করে, তাই মাঝপথে রিস্টার্ট হলেও ডেটা নষ্ট হয় না।"""
    try:
        with _save_lock:
            tmp = filename + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(filename):
                try:
                    shutil.copy2(filename, filename + ".bak")
                except Exception:
                    pass
            os.replace(tmp, filename)
    except Exception as e:
        logging.error("Save failed for %s: %s", filename, e)


gmail_tasks = load_json_file(GMAIL_FILE, [])
app_tasks = load_json_file(APP_FILE, [])
facebook_tasks = load_json_file(FACEBOOK_FILE, [])
instagram_tasks = load_json_file(INSTAGRAM_FILE, [])
user_data_db = load_json_file(USER_STATS_FILE, {})
blocked_users = set(int(uid) for uid in load_json_file(BLOCKED_FILE, []))


def save_blocked_users():
    save_json_file(BLOCKED_FILE, list(blocked_users))


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
        "lang": "bn",
    }
    for k, v in defaults.items():
        if k not in user_data_db[key]:
            user_data_db[key][k] = v
    return user_data_db[key]


def save_user_stats():
    save_json_file(USER_STATS_FILE, user_data_db)


def get_lang(user_id):
    return get_user_stats(user_id).get("lang", "bn")


# ============ কিবোর্ড ============
def get_main_keyboard(lang="bn"):
    if lang == "en":
        rows = [
            ["🛠️ 1. Tasks", "💰 2. My Balance"],
            ["💳 3. Withdraw", "📞 4. Support"],
            ["💎 5. Deposit Money", "🎥 6. How to Work"],
            ["🛍️ 7. Buy"],
            ["🎁 8. Referral"],
        ]
    else:
        rows = [
            ["🛠️ ১. কাজ", "💰 ২. আমার ব্যালেন্স"],
            ["💳 ৩. টাকা তুলুন", "📞 ৪. সাপোর্ট"],
            ["💎 ৫. টাকা জমা করুন", "🎥 ৬. কিভাবে কাজ করবো"],
            ["🛍️ ৭. কিনুন"],
            ["🎁 ৮. রেফারেল"],
        ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_task_sub_keyboard(lang="bn"):
    if lang == "en":
        rows = [
            ["📧 1. Gmail Task", "📘 2. Facebook Task"],
            ["📷 3. Instagram Task", "🪙 4. Sell Dollar"],
            ["🤑 5. Earn by Doing Micro Tasks"],
            ["❌ Cancel"],
        ]
    else:
        rows = [
            ["📧 ১. জিমেইল কাজ", "📘 ২. ফেসবুক কাজ"],
            ["📷 ৩. ইনস্টাগ্রাম কাজ", "🪙 ৪. ডলার বিক্রি করুন"],
            ["🤑 ৫. ছোট ছোট কাজ করে ইনকাম করুন"],
            ["❌ বাতিল করুন"],
        ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_buy_sub_keyboard(lang="bn"):
    if lang == "en":
        rows = [
            ["🎵 1. TikTok", "📘 2. Facebook"],
            ["📷 3. Instagram", "▶️ 4. YouTube"],
            ["⚡ 5. Super Proxy"],
            ["❌ Cancel"],
        ]
    else:
        rows = [
            ["🎵 ১. TikTok", "📘 ২. Facebook"],
            ["📷 ৩. Instagram", "▶️ ৪. YouTube"],
            ["⚡ ৫. Super Proxy"],
            ["❌ বাতিল করুন"],
        ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_task_keyboard(lang="bn"):
    if lang == "en":
        rows = [["📥 Submit", "❌ Cancel Task"]]
    else:
        rows = [["📥 জমা দিন", "❌ কাজটি বাতিল করুন"]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _collect_menu_texts():
    texts = set()
    for lang in ("bn", "en"):
        for kb in (
            get_main_keyboard(lang),
            get_task_sub_keyboard(lang),
            get_buy_sub_keyboard(lang),
            get_task_keyboard(lang),
        ):
            for row in kb.keyboard:
                for btn in row:
                    texts.add(btn.text if hasattr(btn, "text") else str(btn))
    return texts


MENU_TEXTS = _collect_menu_texts()
MENU_REGEX = "^(" + "|".join(re.escape(t) for t in MENU_TEXTS) + ")$"
MENU_FILTER = filters.Regex(MENU_REGEX)
SUBMIT_TEXTS = ("📥 জমা দিন", "📥 Submit")
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
                    f"👥 আপনার লিংকে নতুন একজন ইউজার জয়েন করেছে! ({user.first_name})\n"
                    "সে প্রথম কাজ সম্পন্ন করলে আপনি ৫ টাকা বোনাস পাবেন।",
                )
        except Exception:
            pass

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


async def reject_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        tid = int(context.args[0])
    except Exception:
        await update.message.reply_text(
            "ব্যবহার: /rejectdep [User_ID] [কারণ (ঐচ্ছিক)]"
        )
        return
    reason = " ".join(context.args[1:]).strip()
    msg = "❌ আপনার ডিপোজিট রিকোয়েস্টটি বাতিল করা হয়েছে। সঠিক তথ্য দিয়ে আবার চেষ্টা করুন।"
    if reason:
        msg += f"\n📌 কারণ: {reason}"
    ok = await safe_send(context, tid, msg)
    if ok:
        await update.message.reply_text(f"✅ ইউজার {tid}-কে ডিপোজিট বাতিলের নোটিশ পাঠানো হয়েছে।")
    else:
        await update.message.reply_text("❌ মেসেজ পাঠানো যায়নি। ইউজার আইডি ঠিক আছে কি না দেখুন।")


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
        rid = st.get("referred_by")
        if rid:
            rst = get_user_stats(rid)
            rst["balance"] += 5.0
            rst["ref_earnings"] += 5.0
            await safe_send(
                context, rid, "🎉 আপনার সফল রেফারেলের জন্য ৫.০ টাকা বোনাস যোগ হয়েছে! 💸"
            )
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


async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    user = update.effective_user
    save_user_id(user.id)
    lang = get_lang(user.id)
    if text.startswith("/"):
        return

    # রেফারেল
    if text in ("🎁 ৮. রেফারেল", "🎁 8. Referral"):
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={user.id}"
        st = get_user_stats(user.id)
        if lang == "en":
            msg = (
                "🎁 Referral & Earn Program 🚀\n\nInvite friends & earn!\n\n"
                f"👥 Link:\n{ref_link}\n\n"
                f"• Referrals: {st['ref_count']}\n• Earnings: {st['ref_earnings']} Taka"
            )
        else:
            msg = (
                "🎁 রেফারেল প্রোগ্রাম 🚀\n\nবন্ধুদের ইনভাইট করে ইনকাম করুন!\n\n"
                f"👥 লিংক:\n{ref_link}\n\n"
                f"• মোট রেফার: {st['ref_count']} জন\n• মোট আয়: {st['ref_earnings']} টাকা"
            )
        await update.message.reply_text(msg, reply_markup=get_main_keyboard(lang))
        return

    # কাজ ক্যাটাগরি
    if text in ("🛠️ ১. কাজ", "🛠️ 1. Tasks"):
        msg = "🛠️ কাজের ক্যাটাগরি:" if lang == "bn" else "🛠️ Task Categories:"
        await update.message.reply_text(msg, reply_markup=get_task_sub_keyboard(lang))
        return

    if text in ("❌ বাতিল করুন", "❌ Cancel"):
        await update.message.reply_text(
            "প্রধান মেনু:" if lang == "bn" else "Main Menu:",
            reply_markup=get_main_keyboard(lang),
        )
        return

    if text in ("📧 ১. জিমেইল কাজ", "📧 1. Gmail Task"):
        label = (
            "একটি জিমেইল একাউন্টের দাম ১৪ টাকা 💸"
            if lang == "bn"
            else "One Gmail account price is 14 Taka 💸"
        )
        kb = [[InlineKeyboardButton(label, callback_data="gmail_details")]]
        await update.message.reply_text(
            "📧 জিমেইল কাজ তথ্য:" if lang == "bn" else "📧 Gmail Task Info:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if text in ("📘 ২. ফেসবুক কাজ", "📘 2. Facebook Task"):
        label = FB_PRICE_TEXT_BN if lang == "bn" else FB_PRICE_TEXT_EN
        kb = [[InlineKeyboardButton(label, callback_data="fb_details")]]
        await update.message.reply_text(
            "📘 ফেসবুক কাজ তথ্য:" if lang == "bn" else "📘 Facebook Task Info:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if text in ("📷 ৩. ইনস্টাগ্রাম কাজ", "📷 3. Instagram Task"):
        label = IG_PRICE_TEXT_BN if lang == "bn" else IG_PRICE_TEXT_EN
        kb = [[InlineKeyboardButton(label, callback_data="insta_details")]]
        await update.message.reply_text(
            "📷 ইনস্টাগ্রাম কাজ তথ্য:" if lang == "bn" else "📷 Instagram Task Info:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if text in ("🪙 ৪. ডলার বিক্রি করুন", "🪙 4. Sell Dollar"):
        kb = [[InlineKeyboardButton(f"১ ডলার = {USD_RATE} টাকা 💸", callback_data="sell_usd_rate")]]
        await update.message.reply_text(
            "🪙 ডলার বিক্রি সার্ভিস:", reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    if text in ("🤑 ৫. ছোট ছোট কাজ করে ইনকাম করুন", "🤑 5. Earn by Doing Micro Tasks"):
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

    # কিনুন
    if text in ("🛍️ ৭. কিনুন", "🛍️ 7. Buy"):
        await update.message.reply_text(
            "🛍️ সার্ভিস ক্যাটাগরি:" if lang == "bn" else "🛍️ Categories:",
            reply_markup=get_buy_sub_keyboard(lang),
        )
        return

    if text in ("🎵 ১. TikTok", "🎵 1. TikTok"):
        kb = [
            [InlineKeyboardButton("🎬 টিকটক অল সার্ভিস", callback_data="buy_tiktok_all")],
            [InlineKeyboardButton("🪙 টিকটক কয়েন", callback_data="buy_tiktok_coin")],
        ]
        await update.message.reply_text("🎵 TikTok Service:", reply_markup=InlineKeyboardMarkup(kb))
        return

    buy_map = {
        ("📘 ২. Facebook", "📘 2. Facebook"): "📘 Facebook Services",
        ("📷 ৩. Instagram", "📷 3. Instagram"): "📷 Instagram Services",
        ("▶️ ৪. YouTube", "▶️ 4. YouTube"): "▶️ YouTube Services",
        ("⚡ ৫. Super Proxy", "⚡ 5. Super Proxy"): "⚡ Proxy Services",
    }
    for keys, title in buy_map.items():
        if text in keys:
            await update.message.reply_text(
                f"{title}\nযোগাযোগ: @mominboss006",
                reply_markup=get_buy_sub_keyboard(lang),
            )
            return

    # ব্যালেন্স
    if text in ("💰 ২. আমার ব্যালেন্স", "💰 2. My Balance"):
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
    if text in ("💳 ৩. টাকা তুলুন", "💳 3. Withdraw"):
        kb = [
            [InlineKeyboardButton("বিকাশ", callback_data="withdraw_bkash")],
            [InlineKeyboardButton("নগদ", callback_data="withdraw_nagad")],
            [InlineKeyboardButton("মোবাইল রিচার্জ", callback_data="withdraw_recharge")],
            [InlineKeyboardButton("Binance", callback_data="withdraw_binance")],
        ]
        await update.message.reply_text("💳 টাকা তুলুন:", reply_markup=InlineKeyboardMarkup(kb))
        return

    # ডিপোজিট
    if text in ("💎 ৫. টাকা জমা করুন", "💎 5. Deposit Money"):
        kb = [
            [InlineKeyboardButton("বিকাশ", callback_data="deposit_bkash")],
            [InlineKeyboardButton("নগদ", callback_data="deposit_nagad")],
            [InlineKeyboardButton("Binance", callback_data="deposit_binance")],
        ]
        await update.message.reply_text("💎 ডিপোজিট:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if text in ("📞 ৪. সাপোর্ট", "📞 4. Support"):
        await update.message.reply_text(
            "সাপোর্ট: @mominboss006", reply_markup=get_main_keyboard(lang)
        )
        return

    if text in ("🎥 ৬. কিভাবে কাজ করবো", "🎥 6. How to Work"):
        kb = [[InlineKeyboardButton("চ্যানেল 🔗", url="https://t.me/EasyIncomeBD_Payment")]]
        await update.message.reply_text("পেমেন্ট প্রুফ চ্যানেল:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if text in ("❌ কাজটি বাতিল করুন", "❌ Cancel Task"):
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

    if data == "sell_usd_rate":
        kb = [[InlineKeyboardButton("ডলার বিক্রি শুরু 💵", callback_data="sell_usd_start")]]
        await query.message.reply_text(
            f"১ ডলার = {USD_RATE} টাকা 💸", reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    if data == "buy_tiktok_all":
        await query.message.reply_text("টিকটক সার্ভিস যোগাযোগ: @mominboss006")
        return
    if data == "buy_tiktok_coin":
        await query.message.reply_text("টিকটক কয়েন মূল্য তালিকা... যোগাযোগ: @mominboss006")
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

    if data in ("deposit_bkash", "deposit_nagad", "deposit_binance"):
        method = {"deposit_bkash": "Bkash", "deposit_nagad": "Nagad", "deposit_binance": "Binance"}[data]
        context.user_data["deposit_method"] = method
        await query.message.reply_text(f"পরিমাণ লিখুন ({method}):")
        return DEPOSIT_AMT

    if data == "deposit_done":
        await query.message.reply_text("TrxID দিন:")
        return DEPOSIT_TRX

    if data in ("withdraw_bkash", "withdraw_nagad", "withdraw_recharge"):
        if st["balance"] < 25:
            await query.message.reply_text(
                "❌ সর্বনিম্ন ২৫৳ হওয়া লাগবে।", reply_markup=get_main_keyboard(lang)
            )
            return ConversationHandler.END
        method = {
            "withdraw_bkash": "বিকাশ",
            "withdraw_nagad": "নগদ",
            "withdraw_recharge": "মোবাইল রিচার্জ",
        }[data]
        context.user_data["withdraw_method"] = method
        context.user_data["withdraw_min"] = 25
        await query.message.reply_text(f"আপনার {method} নাম্বার দিন:")
        return WITHDRAW_NUM

    if data == "withdraw_binance":
        if st["balance"] < 30:
            await query.message.reply_text(
                "❌ সর্বনিম্ন ৳৩০ হওয়া লাগবে।", reply_markup=get_main_keyboard(lang)
            )
            return ConversationHandler.END
        context.user_data["withdraw_method"] = "Binance"
        context.user_data["withdraw_min"] = 30
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
        f"ডলার পাঠান: 1150427115\nটাকা পাবেন: {amt * USD_RATE} BDT",
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
async def receive_deposit_amt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amt = float(update.message.text)
        if amt <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("সঠিক সংখ্যা লিখুন:")
        return DEPOSIT_AMT
    method = context.user_data.get("deposit_method", "Bkash")
    context.user_data["deposit_amt"] = amt
    num = {"Bkash": "01312676271", "Nagad": "01300342615"}.get(method, "1150427115")
    kb = [[InlineKeyboardButton("Payment Done ✅", callback_data="deposit_done")]]
    await update.message.reply_text(
        f"ডিপোজিট নাম্বার: `{num}`\nটাকা পাঠান এবং নিচের বাটনে ক্লিক করুন:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def receive_deposit_trx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    trx = update.message.text
    method = context.user_data.get("deposit_method", "Bkash")
    amt = context.user_data.get("deposit_amt", 0)
    text = (
        "💎 ডিপোজিট রিকোয়েস্ট\n"
        f"ইউজার: {esc(user.first_name)} (<code>{user.id}</code>)\n"
        f"মাধ্যম: {method}\nপরিমাণ: {amt}\n"
        f"TrxID: <code>{esc(trx)}</code>"
    )
    await notify_admin(context, text)
    await update.message.reply_text(
        "✅ ডিপোজিট রিকোয়েস্ট সফলভাবে জমা হয়েছে!",
        reply_markup=get_main_keyboard(get_lang(user.id)),
    )
    return ConversationHandler.END


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

    min_amt = context.user_data.get("withdraw_min", 25)
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


async def receive_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    lang = get_lang(user.id)

    if not update.message.photo:
        await update.message.reply_text("দয়া করে স্ক্রিনশট (ছবি) পাঠান:")
        return PROOF_SUBMIT

    photo = update.message.photo[-1].file_id
    st = get_user_stats(user.id)
    st["pending"] += 1
    save_user_stats()

    ttype = context.user_data.get("current_task_type", "gmail")
    tdesc = context.user_data.get("current_task_text", "বিবরণ নেই")

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


# ============ অটো ব্যাকআপ / রিস্টোর ============
async def send_backup(bot, reason="অটো ব্যাকআপ"):
    sent = 0
    for path in ALL_DATA_FILES:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                await bot.send_document(
                    chat_id=ADMIN_ID,
                    document=f,
                    filename=os.path.basename(path),
                    caption=f"💾 {reason}",
                )
            sent += 1
        except Exception as e:
            logging.error("Backup send failed for %s: %s", path, e)
    return sent


async def backup_loop(application):
    interval = max(BACKUP_INTERVAL_HOURS, 0.1) * 3600
    while True:
        await asyncio.sleep(interval)
        try:
            await send_backup(application.bot)
        except Exception as e:
            logging.error("Backup loop error: %s", e)


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    n = await send_backup(context.bot, "ম্যানুয়াল ব্যাকআপ")
    await update.message.reply_text(f"✅ {n}টি ডেটা ফাইল ব্যাকআপ পাঠানো হয়েছে।")


def reload_data_from_disk():
    """ডিস্ক থেকে ডেটা মেমরিতে রিলোড (রিস্টোরের পর)।"""
    gmail_tasks[:] = load_json_file(GMAIL_FILE, [])
    app_tasks[:] = load_json_file(APP_FILE, [])
    facebook_tasks[:] = load_json_file(FACEBOOK_FILE, [])
    instagram_tasks[:] = load_json_file(INSTAGRAM_FILE, [])
    user_data_db.clear()
    user_data_db.update(load_json_file(USER_STATS_FILE, {}))
    blocked_users.clear()
    blocked_users.update(int(u) for u in load_json_file(BLOCKED_FILE, []))


async def restore_from_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """অ্যাডমিন ব্যাকআপ ফাইল (যেমন user_stats.json) বটে পাঠালেই রিস্টোর হবে।"""
    doc = update.message.document
    name = os.path.basename(doc.file_name or "")
    target = next((p for p in ALL_DATA_FILES if os.path.basename(p) == name), None)
    if not target:
        return
    tg_file = await doc.get_file()
    raw = await tg_file.download_as_bytearray()
    try:
        json.loads(bytes(raw).decode("utf-8"))
    except Exception:
        await update.message.reply_text("❌ ফাইলটি সঠিক JSON নয়।")
        return
    save_json_file(target, json.loads(bytes(raw).decode("utf-8")))
    reload_data_from_disk()
    await update.message.reply_text(f"✅ {name} রিস্টোর হয়েছে।")


async def setup_menu_commands(application):
    """চ্যাটবক্সের পাশের ☰ বাটনে কমান্ড লিস্ট দেখানোর জন্য।"""
    user_commands = [
        BotCommand("start", "বট শুরু করুন"),
    ]
    admin_commands = [
        BotCommand("start", "বট শুরু করুন"),
        BotCommand("info", "ইউজারের ব্যালেন্স/কাজ/রেফার দেখুন"),
        BotCommand("users", "মোট ইউজার ও কাজের সংখ্যা"),
        BotCommand("add", "ইউজারের ব্যালেন্স যোগ করুন"),
        BotCommand("cut", "ইউজারের ব্যালেন্স কাটুন"),
        BotCommand("msg", "ইউজারকে সরাসরি মেসেজ পাঠান"),
        BotCommand("block", "ইউজারকে ব্লক করুন"),
        BotCommand("unblock", "ইউজারকে আনব্লক করুন"),
        BotCommand("addtask", "জিমেইল কাজ যোগ করুন"),
        BotCommand("cleartasks", "সব জিমেইল কাজ মুছুন"),
        BotCommand("addfbtask", "ফেসবুক কাজ যোগ করুন"),
        BotCommand("clearfbtasks", "সব ফেসবুক কাজ মুছুন"),
        BotCommand("addigtask", "ইনস্টাগ্রাম কাজ যোগ করুন"),
        BotCommand("clearigtasks", "সব ইনস্টাগ্রাম কাজ মুছুন"),
        BotCommand("addapptask", "মাইক্রো কাজ যোগ করুন"),
        BotCommand("clearapptasks", "সব মাইক্রো কাজ মুছুন"),
        BotCommand("rejectg", "জিমেইল কাজ রিজেক্ট করুন"),
        BotCommand("rejectfb", "ফেসবুক কাজ রিজেক্ট করুন"),
        BotCommand("rejectig", "ইনস্টাগ্রাম কাজ রিজেক্ট করুন"),
        BotCommand("rejectdep", "ডিপোজিট রিজেক্ট করুন"),
        BotCommand("paid", "⚠️ ছবির ক্যাপশনে লিখুন: পেমেন্ট প্রুফ পাঠাতে"),
        BotCommand("online", "সব ইউজারকে অনলাইন নোটিশ"),
        BotCommand("notice", "সব ইউজারকে নোটিশ পাঠান"),
        BotCommand("backup", "ডেটা ব্যাকআপ ফাইল পাঠান"),
    ]
    await application.bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    await application.bot.set_my_commands(
        admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID)
    )
    application.bot_data["backup_task"] = asyncio.create_task(backup_loop(application))


# ============ Render-এর জন্য হেলথ সার্ভার ============
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is running")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def start_health_server():
    port = int(os.getenv("PORT") or 10000)
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logging.info("Health server listening on port %s", port)


# ============ মেইন ============
def main():
    start_health_server()
    app = ApplicationBuilder().token(TOKEN).post_init(setup_menu_commands).build()

    # ব্লক করা ইউজারকে সবকিছুর আগে আটকে দিতে group=-1 (সবার আগে চলে)
    app.add_handler(MessageHandler(filters.ALL, check_blocked), group=-1)
    app.add_handler(CallbackQueryHandler(check_blocked), group=-1)

    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                conv_callback,
                pattern=r"^(withdraw_|deposit_|sell_usd_start$|sell_usd_done$|fb_done$)",
            ),
            MessageHandler(
                filters.Regex("^(" + "|".join(re.escape(t) for t in SUBMIT_TEXTS) + ")$"),
                ask_proof,
            ),
        ],
        states={
            WITHDRAW_NUM: [MessageHandler(TEXT_INPUT, receive_withdraw_num)],
            WITHDRAW_AMT: [MessageHandler(TEXT_INPUT, receive_withdraw_amt)],
            DEPOSIT_AMT: [MessageHandler(TEXT_INPUT, receive_deposit_amt)],
            DEPOSIT_TRX: [MessageHandler(TEXT_INPUT, receive_deposit_trx)],
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
    app.add_handler(CommandHandler("online", online_notice))
    app.add_handler(CommandHandler("notice", notice_command))
    app.add_handler(CommandHandler("addtask", add_task))
    app.add_handler(CommandHandler("cleartasks", clear_tasks))
    app.add_handler(CommandHandler("addfbtask", add_fb_task))
    app.add_handler(CommandHandler("clearfbtasks", clear_fb_tasks))
    app.add_handler(CommandHandler("addigtask", add_ig_task))
    app.add_handler(CommandHandler("clearigtasks", clear_ig_tasks))
    app.add_handler(CommandHandler("addapptask", add_app_task))
    app.add_handler(CommandHandler("clearapptasks", clear_app_tasks))
    app.add_handler(CommandHandler("rejectg", reject_gmail))
    app.add_handler(CommandHandler("rejectfb", reject_facebook))
    app.add_handler(CommandHandler("rejectig", reject_instagram))
    app.add_handler(CommandHandler("rejectdep", reject_deposit))
    app.add_handler(CommandHandler("add", add_balance))
    app.add_handler(CommandHandler("cut", cut_balance))
    app.add_handler(CommandHandler("users", total_users))
    app.add_handler(CommandHandler("info", user_info))
    app.add_handler(CommandHandler("block", block_user_cmd))
    app.add_handler(CommandHandler("unblock", unblock_user_cmd))
    app.add_handler(CommandHandler("msg", admin_send_message))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(
        MessageHandler(filters.Document.ALL & filters.User(user_id=ADMIN_ID), restore_from_document)
    )
    app.add_handler(
        MessageHandler(filters.PHOTO & filters.User(user_id=ADMIN_ID), handle_admin_photo)
    )
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    print("Bot is running successfully with all features...")
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling()


if __name__ == "__main__":
    main()
