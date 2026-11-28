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
    TypeHandler,
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
FACEBOOK_FILE = "facebook_tasks.json"
FB_ACCOUNTS_FILE = "fb_accounts.json"
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
    FB_UID,
    FB_COOKIES,
) = range(10)



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
facebook_tasks = load_json_file(FACEBOOK_FILE, [])
fb_accounts = load_json_file(FB_ACCOUNTS_FILE, [])
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
    "referral_gmail_required": 4,   # রেফার করা ইউজার এতগুলো জিমেইল জমা দিলে রেফারকারী বোনাস পায়
    "cut_notify": True,             # False হলে /cut করলে ইউজারকে কোনো মেসেজ যায় না
}
settings = load_json_file(SETTINGS_FILE, {})
for _k, _v in SETTINGS_DEFAULTS.items():
    settings.setdefault(_k, _v)


def save_settings():
    save_json_file(SETTINGS_FILE, settings)


def get_support_message(lang="bn"):
    """অ্যাডমিন /setsupportmsg দিয়ে পুরো বার্তা কাস্টম করলে সেটাই দেখাবে,
    নইলে /setsupportuser দিয়ে বসানো ইউজারনেম দিয়ে স্বয়ংক্রিয়ভাবে বার্তা তৈরি হবে।"""
    custom = settings.get("support_message", "").strip()
    if custom:
        return custom
    return tr(lang, "support", user=settings["support_username"])


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
        "gmail_completed": 0,   # অ্যাডমিন "সম্পূর্ণ" বাটন চাপা জিমেইল সংখ্যা (রেফার বোনাসের হিসাব)
        "gmail_credit": 0,      # সম্পূর্ণ করা কিন্তু এখনও /add দেওয়া হয়নি এমন কাজ — জিমেইল/ফেসবুক/ইনস্টাগ্রাম (দ্বিগুণ গণনা ঠেকাতে)
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
    "tasks": ("📝 কাজ ▸", "📝 Tasks ▸", "📝 المهام ▸", "📝 काम ▸"),
    "balance": ("💵 ব্যালেন্স", "💵 Balance", "💵 الرصيد", "💵 बैलेंस"),
    "withdraw": ("💰 টাকা উত্তোলন", "💰 Withdraw", "💰 سحب الأموال", "💰 पैसे निकालें"),
    "referral": ("🎁 My Referrals", "🎁 My Referrals", "🎁 إحالاتي", "🎁 मेरे रेफरल"),
    "newbie": ("🆕 আমি নতুন", "🆕 I'm New", "🆕 أنا جديد", "🆕 मैं नया हूँ"),
    "support": ("👮 এডমিন সাপোর্ট", "👮 Admin Support", "👮 دعم الإدارة", "👮 एडमिन सपोर्ट"),
    "language": (
        "🌐 Language / ভাষা",
        "🌐 Language / ভাষা",
        "🌐 Language / ভাষা",
        "🌐 Language / ভাষা",
    ),
    "gmail": ("📧 জিমেইল কাজ", "📧 Gmail Task", "📧 مهمة Gmail", "📧 Gmail काम"),
    "facebook": ("📘 ফেসবুক কাজ", "📘 Facebook Task", "📘 مهمة فيسبوك", "📘 फेसबुक काम"),
    "instagram": ("📷 ইনস্টাগ্রাম কাজ", "📷 Instagram Task", "📷 مهمة إنستغرام", "📷 इंस्टाग्राम काम"),
    "sell_usd": ("🪙 ডলার বিক্রি করুন", "🪙 Sell Dollar", "🪙 بيع الدولار", "🪙 डॉलर बेचें"),
    "old_gmail": ("📮 পুরাতন জিমেইল বিক্রি", "📮 Sell Old Gmail", "📮 بيع Gmail قديم", "📮 पुराना Gmail बेचें"),
    "cancel": ("❌ বাতিল করুন", "❌ Cancel", "❌ إلغاء", "❌ रद्द करें"),
    "submit": ("📥 জমা দিন", "📥 Submit", "📥 إرسال", "📥 जमा करें"),
    "cancel_task": ("❌ কাজটি বাতিল করুন", "❌ Cancel Task", "❌ إلغاء المهمة", "❌ काम रद्द करें"),
}

# টেলিগ্রামের রঙিন বাটন: primary = নীল, success = সবুজ, danger = লাল
BLUE, GREEN, RED = "primary", "success", "danger"


def L(key, lang="bn"):
    """বাটনের লেখা ভাষা অনুযায়ী দেয়।"""
    return BTN[key][LANG_INDEX.get(lang, 0)]


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


# ============ ভাষা (বাংলা, English, العربية, हिन्दी) ============
LANGS = ("bn", "en", "ar", "hi")
LANG_INDEX = {"bn": 0, "en": 1, "ar": 2, "hi": 3}

# ডলার সেল: ইউজার এই Binance UID ও USDT (BEP20) অ্যাড্রেসে ডলার পাঠাবে
BINANCE_UID = "1150427115"
USDT_BEP20_ADDRESS = "0xfd771797f51a9850c0210e04d79d679766905337"

# ইউজারকে দেখানো সব বার্তা এখানে। নতুন ভাষা যোগ করতে প্রতিটির সাথে একটা লাইন বাড়ালেই হবে।
TR = {
    "lang_set": {
        "bn": "ভাষা সেট করা হয়েছে।",
        "en": "Language set.",
        "ar": "تم تعيين اللغة.",
        "hi": "भाषा सेट कर दी गई है।",
    },
    "referral": {
        "bn": "🎁 রেফারেল প্রোগ্রাম 🚀\n\nবন্ধুদের ইনভাইট করে ইনকাম করুন!\n\n👥 লিংক:\n{link}\n\n💰 ১ জনকে রেফার করলে পাবেন: {bonus} টাকা\n\n• মোট রেফার: {count} জন\n• মোট আয়: {earn} টাকা",
        "en": "🎁 Referral & Earn Program 🚀\n\nInvite friends & earn!\n\n👥 Link:\n{link}\n\n💰 Per referral: {bonus} Taka\n\n• Referrals: {count}\n• Earnings: {earn} Taka",
        "ar": "🎁 برنامج الإحالة 🚀\n\nادعُ أصدقاءك واكسب!\n\n👥 الرابط:\n{link}\n\n💰 مكافأة كل إحالة: {bonus} تاكا\n\n• إجمالي الإحالات: {count}\n• إجمالي الأرباح: {earn} تاكا",
        "hi": "🎁 रेफरल प्रोग्राम 🚀\n\nदोस्तों को आमंत्रित करें और कमाएँ!\n\n👥 लिंक:\n{link}\n\n💰 एक रेफर पर मिलेगा: {bonus} टका\n\n• कुल रेफर: {count}\n• कुल कमाई: {earn} टका",
    },
    "tasks_cat": {
        "bn": "🛠️ কাজের ক্যাটাগরি:",
        "en": "🛠️ Task Categories:",
        "ar": "🛠️ فئات المهام:",
        "hi": "🛠️ काम की श्रेणियाँ:",
    },
    "main_menu": {
        "bn": "প্রধান মেনু:",
        "en": "Main Menu:",
        "ar": "القائمة الرئيسية:",
        "hi": "मुख्य मेनू:",
    },
    "gmail_label": {
        "bn": "একটি জিমেইল একাউন্টের দাম {price} টাকা 💸",
        "en": "One Gmail account price is {price} Taka 💸",
        "ar": "سعر حساب Gmail واحد {price} تاكا 💸",
        "hi": "एक Gmail अकाउंट की कीमत {price} टका 💸",
    },
    "gmail_info": {
        "bn": "📧 জিমেইল কাজ তথ্য:",
        "en": "📧 Gmail Task Info:",
        "ar": "📧 معلومات مهمة Gmail:",
        "hi": "📧 Gmail काम की जानकारी:",
    },
    "fb_label": {
        "bn": "একটি ফেসবুক একাউন্টের দাম {price} টাকা 💸",
        "en": "One Facebook account price is {price} Taka 💸",
        "ar": "سعر حساب فيسبوك واحد {price} تاكا 💸",
        "hi": "एक फेसबुक अकाउंट की कीमत {price} टका 💸",
    },
    "fb_info": {
        "bn": "📘 ফেসবুক কাজ তথ্য:",
        "en": "📘 Facebook Task Info:",
        "ar": "📘 معلومات مهمة فيسبوك:",
        "hi": "📘 फेसबुक काम की जानकारी:",
    },
    "ig_label": {
        "bn": "একটি ইনস্টাগ্রাম একাউন্টের দাম {price} টাকা 💸",
        "en": "One Instagram account price is {price} Taka 💸",
        "ar": "سعر حساب إنستغرام واحد {price} تاكا 💸",
        "hi": "एक इंस्टाग्राम अकाउंट की कीमत {price} टका 💸",
    },
    "ig_info": {
        "bn": "📷 ইনস্টাগ্রাম কাজ তথ্য:",
        "en": "📷 Instagram Task Info:",
        "ar": "📷 معلومات مهمة إنستغرام:",
        "hi": "📷 इंस्टाग्राम काम की जानकारी:",
    },
    "old_label": {
        "bn": "পুরাতন জিমেইলের দাম {price} টাকা 💸",
        "en": "Old Gmail price is {price} Taka 💸",
        "ar": "سعر Gmail القديم {price} تاكا 💸",
        "hi": "पुराने Gmail की कीमत {price} टका 💸",
    },
    "old_info": {
        "bn": "📮 পুরাতন জিমেইল বিক্রি:",
        "en": "📮 Sell Old Gmail:",
        "ar": "📮 بيع Gmail قديم:",
        "hi": "📮 पुराना Gmail बेचें:",
    },
    "old_gmail_contact": {
        "bn": "✅ জিমেইল বিক্রি করতে @{user}\nএই আইডিতে মেসেজ দিয়েন",
        "en": "✅ To sell Gmail, message this ID:\n@{user}",
        "ar": "✅ لبيع Gmail راسل هذا المعرف:\n@{user}",
        "hi": "✅ Gmail बेचने के लिए इस आईडी पर मैसेज करें:\n@{user}",
    },
    "usd_rate": {
        "bn": "১ ডলার = {rate} টাকা 💸",
        "en": "1 Dollar = {rate} Taka 💸",
        "ar": "1 دولار = {rate} تاكا 💸",
        "hi": "1 डॉलर = {rate} टका 💸",
    },
    "usd_service": {
        "bn": "🪙 ডলার বিক্রি সার্ভিস:",
        "en": "🪙 Dollar Sell Service:",
        "ar": "🪙 خدمة بيع الدولار:",
        "hi": "🪙 डॉलर बिक्री सेवा:",
    },
    "usd_start_btn": {
        "bn": "ডলার বিক্রি শুরু 💵",
        "en": "Start Selling Dollar 💵",
        "ar": "ابدأ بيع الدولار 💵",
        "hi": "डॉलर बेचना शुरू करें 💵",
    },
    "usd_ask_amt": {
        "bn": "কত ডলার বিক্রি করবেন লিখুন:",
        "en": "Enter how many dollars you want to sell:",
        "ar": "اكتب كم دولارًا تريد بيعه:",
        "hi": "लिखें कि आप कितने डॉलर बेचेंगे:",
    },
    "usd_bad_num": {
        "bn": "সঠিক সংখ্যায় লিখুন:",
        "en": "Enter a valid number:",
        "ar": "اكتب رقمًا صحيحًا:",
        "hi": "सही संख्या लिखें:",
    },
    "usd_send": {
        "bn": "ডলার পাঠান:\n\n🆔 Binance UID: <code>{uid}</code>\n\n📥 USDT (BEP20) অ্যাড্রেস:\n<code>{addr}</code>\n\n💰 টাকা পাবেন: {amt} BDT",
        "en": "Send dollars to:\n\n🆔 Binance UID: <code>{uid}</code>\n\n📥 USDT (BEP20) Address:\n<code>{addr}</code>\n\n💰 You will get: {amt} BDT",
        "ar": "أرسل الدولارات إلى:\n\n🆔 Binance UID: <code>{uid}</code>\n\n📥 عنوان USDT (BEP20):\n<code>{addr}</code>\n\n💰 ستحصل على: {amt} BDT",
        "hi": "डॉलर यहाँ भेजें:\n\n🆔 Binance UID: <code>{uid}</code>\n\n📥 USDT (BEP20) एड्रेस:\n<code>{addr}</code>\n\n💰 आपको मिलेगा: {amt} BDT",
    },
    "usd_ask_proof": {
        "bn": "পেমেন্টের স্ক্রিনশট দিন:",
        "en": "Send the payment screenshot:",
        "ar": "أرسل لقطة شاشة الدفع:",
        "hi": "पेमेंट का स्क्रीनशॉट भेजें:",
    },
    "usd_need_photo": {
        "bn": "স্ক্রিনশট পাঠান:",
        "en": "Send a screenshot:",
        "ar": "أرسل لقطة شاشة:",
        "hi": "स्क्रीनशॉट भेजें:",
    },
    "usd_ask_acc": {
        "bn": "আপনার বিকাশ/নগদ নাম্বার দিন:",
        "en": "Enter your bKash/Nagad number:",
        "ar": "أدخل رقم بكاش/نقد الخاص بك:",
        "hi": "अपना बिकैश/नगद नंबर दें:",
    },
    "usd_req_ok": {
        "bn": "✅ রিকোয়েস্ট জমা হয়েছে!",
        "en": "✅ Request submitted!",
        "ar": "✅ تم إرسال الطلب!",
        "hi": "✅ अनुरोध जमा हो गया!",
    },
    "balance": {
        "bn": "👤 আপনার তথ্য:\n\n✅ কাজ করেছেন: {completed}\n⏳ পেন্ডিং: {pending}\n💰 ব্যালেন্স: {balance} টাকা",
        "en": "👤 Your info:\n\n✅ Completed: {completed}\n⏳ Pending: {pending}\n💰 Balance: {balance} Taka",
        "ar": "👤 معلوماتك:\n\n✅ المكتمل: {completed}\n⏳ قيد الانتظار: {pending}\n💰 الرصيد: {balance} تاكا",
        "hi": "👤 आपकी जानकारी:\n\n✅ पूरे किए: {completed}\n⏳ पेंडिंग: {pending}\n💰 बैलेंस: {balance} टका",
    },
    "wd_title": {
        "bn": "💳 টাকা তুলুন:",
        "en": "💳 Withdraw money:",
        "ar": "💳 سحب الأموال:",
        "hi": "💳 पैसे निकालें:",
    },
    "wd_bkash": {"bn": "বিকাশ", "en": "bKash", "ar": "بكاش", "hi": "बिकैश"},
    "wd_nagad": {"bn": "নগদ", "en": "Nagad", "ar": "نقد", "hi": "नगद"},
    "wd_recharge": {
        "bn": "মোবাইল রিচার্জ",
        "en": "Mobile Recharge",
        "ar": "شحن الجوال",
        "hi": "मोबाइल रिचार्ज",
    },
    "wd_min_err": {
        "bn": "❌ সর্বনিম্ন {min}৳ হওয়া লাগবে।",
        "en": "❌ Minimum {min} Taka is required.",
        "ar": "❌ الحد الأدنى المطلوب {min} تاكا.",
        "hi": "❌ कम से कम {min} टका होना ज़रूरी है।",
    },
    "wd_ask_number": {
        "bn": "আপনার {method} নাম্বার দিন:",
        "en": "Enter your {method} number:",
        "ar": "أدخل رقم {method} الخاص بك:",
        "hi": "अपना {method} नंबर दें:",
    },
    "wd_ask_binance": {
        "bn": "USDT (BEP-20) অ্যাড্রেস দিন:",
        "en": "Enter your USDT (BEP-20) address:",
        "ar": "أدخل عنوان USDT (BEP-20) الخاص بك:",
        "hi": "अपना USDT (BEP-20) एड्रेस दें:",
    },
    "wd_ask_amt": {
        "bn": "কত টাকা উইথড্র করবেন লিখুন:",
        "en": "Enter how much you want to withdraw:",
        "ar": "اكتب المبلغ الذي تريد سحبه:",
        "hi": "लिखें कि आप कितना निकालना चाहते हैं:",
    },
    "wd_bad_num": {
        "bn": "সঠিক সংখ্যা লিখুন:",
        "en": "Enter a valid number:",
        "ar": "اكتب رقمًا صحيحًا:",
        "hi": "सही संख्या लिखें:",
    },
    "wd_min_again": {
        "bn": "❌ সর্বনিম্ন {min}৳ উইথড্র করতে হবে। আবার লিখুন:",
        "en": "❌ Minimum withdrawal is {min} Taka. Enter again:",
        "ar": "❌ الحد الأدنى للسحب {min} تاكا. اكتب مرة أخرى:",
        "hi": "❌ न्यूनतम निकासी {min} टका है। फिर से लिखें:",
    },
    "wd_no_balance": {
        "bn": "❌ পর্যাপ্ত ব্যালেন্স নেই।",
        "en": "❌ Insufficient balance.",
        "ar": "❌ الرصيد غير كافٍ.",
        "hi": "❌ पर्याप्त बैलेंस नहीं है।",
    },
    "wd_ok": {
        "bn": "✅ {amt} টাকার উইথড্র রিকোয়েস্ট জমা হয়েছে!",
        "en": "✅ Withdrawal request for {amt} Taka submitted!",
        "ar": "✅ تم إرسال طلب سحب {amt} تاكا!",
        "hi": "✅ {amt} टका की निकासी का अनुरोध जमा हो गया!",
    },
    "newbie_title": {
        "bn": "পেমেন্ট প্রুফ চ্যানেল:",
        "en": "Payment proof channel:",
        "ar": "قناة إثبات الدفع:",
        "hi": "पेमेंट प्रूफ चैनल:",
    },
    "newbie_btn": {
        "bn": "চ্যানেল 🔗",
        "en": "Channel 🔗",
        "ar": "القناة 🔗",
        "hi": "चैनल 🔗",
    },
    "support": {
        "bn": "সাপোর্ট: @{user}",
        "en": "Support: @{user}",
        "ar": "الدعم: @{user}",
        "hi": "सपोर्ट: @{user}",
    },
    "no_gmail": {
        "bn": "⚠️ জিমেইল কাজ নেই!",
        "en": "⚠️ No Gmail tasks!",
        "ar": "⚠️ لا توجد مهام Gmail!",
        "hi": "⚠️ कोई Gmail काम नहीं है!",
    },
    "no_fb": {
        "bn": "⚠️ ফেসবুক কাজ নেই!",
        "en": "⚠️ No Facebook tasks!",
        "ar": "⚠️ لا توجد مهام فيسبوك!",
        "hi": "⚠️ कोई फेसबुक काम नहीं है!",
    },
    "no_ig": {
        "bn": "⚠️ ইনস্টাগ্রাম কাজ নেই!",
        "en": "⚠️ No Instagram tasks!",
        "ar": "⚠️ لا توجد مهام إنستغرام!",
        "hi": "⚠️ कोई इंस्टाग्राम काम नहीं है!",
    },
    "gmail_task": {
        "bn": "📌 জিমেইল কাজ:\n\n{task}",
        "en": "📌 Gmail Task:\n\n{task}",
        "ar": "📌 مهمة Gmail:\n\n{task}",
        "hi": "📌 Gmail काम:\n\n{task}",
    },
    "ig_task": {
        "bn": "📷 ইনস্টাগ্রাম কাজের নির্দেশিকা:\n\n{task}",
        "en": "📷 Instagram Task Instructions:\n\n{task}",
        "ar": "📷 تعليمات مهمة إنستغرام:\n\n{task}",
        "hi": "📷 इंस्टाग्राम काम के निर्देश:\n\n{task}",
    },
    "ig_done": {
        "bn": "✅ আপনার কাজ সম্পন্ন হয়েছে!\n\nআপনি ২৪ ঘণ্টার ভিতরে আপনার ব্যালেন্সে টাকা পেয়ে যাবেন, যদি কাজটি সঠিকভাবে হয়ে থাকে।",
        "en": "✅ Your task is complete!\n\nIf the task was done correctly, you will receive the money in your balance within 24 hours.",
        "ar": "✅ تم إكمال مهمتك!\n\nإذا تمت المهمة بشكل صحيح ستصلك الأموال في رصيدك خلال 24 ساعة.",
        "hi": "✅ आपका काम पूरा हो गया!\n\nअगर काम सही हुआ है तो 24 घंटे के अंदर पैसे आपके बैलेंस में आ जाएँगे।",
    },
    "fb_account": {
        "bn": "📘 ফেসবুক অ্যাকাউন্টের তথ্য:\n\n👤 First Name: <code>{first}</code>\n👤 Last Name: <code>{last}</code>\n🔑 Password: <code>{pw}</code>\n\nকাজটি সম্পন্ন করতে নিচে <b>UID দিন</b> বাটনে ক্লিক করুন অথবা ফিরে যেতে <b>বাতিল করুন</b> চাপুন।",
        "en": "📘 Facebook account details:\n\n👤 First Name: <code>{first}</code>\n👤 Last Name: <code>{last}</code>\n🔑 Password: <code>{pw}</code>\n\nTo complete the task tap <b>Enter UID</b> below, or tap <b>Cancel</b> to go back.",
        "ar": "📘 بيانات حساب فيسبوك:\n\n👤 First Name: <code>{first}</code>\n👤 Last Name: <code>{last}</code>\n🔑 Password: <code>{pw}</code>\n\nلإكمال المهمة اضغط <b>أدخل UID</b> بالأسفل، أو اضغط <b>إلغاء</b> للرجوع.",
        "hi": "📘 फेसबुक अकाउंट की जानकारी:\n\n👤 First Name: <code>{first}</code>\n👤 Last Name: <code>{last}</code>\n🔑 Password: <code>{pw}</code>\n\nकाम पूरा करने के लिए नीचे <b>UID दें</b> बटन दबाएँ, या वापस जाने के लिए <b>रद्द करें</b> दबाएँ।",
    },
    "uid_btn": {
        "bn": "UID দিন",
        "en": "Enter UID",
        "ar": "أدخل UID",
        "hi": "UID दें",
    },
    "fb_no_active": {
        "bn": "⚠️ আপনার কোনো সক্রিয় ফেসবুক কাজ নেই। আগে কাজ নিন।",
        "en": "⚠️ You have no active Facebook task. Take a task first.",
        "ar": "⚠️ ليس لديك مهمة فيسبوك نشطة. ابدأ مهمة أولًا.",
        "hi": "⚠️ आपका कोई सक्रिय फेसबुक काम नहीं है। पहले काम लें।",
    },
    "fb_ask_uid": {
        "bn": "📝 আপনার ফেসবুক অ্যাকাউন্টের ১৪ থেকে ১৬ সংখ্যার UID-টি দিন:\n(বাতিল করতে /cancel)",
        "en": "📝 Enter the 14 to 16 digit UID of your Facebook account:\n(Send /cancel to cancel)",
        "ar": "📝 أدخل UID حساب فيسبوك المكوّن من 14 إلى 16 رقمًا:\n(أرسل /cancel للإلغاء)",
        "hi": "📝 अपने फेसबुक अकाउंट का 14 से 16 अंकों का UID दें:\n(रद्द करने के लिए /cancel भेजें)",
    },
    "fb_bad_uid": {
        "bn": "⚠️ UID ঠিক নেই! ১৪ থেকে ১৬ সংখ্যার UID দিন (শুধু সংখ্যা):",
        "en": "⚠️ Invalid UID! Enter a 14 to 16 digit UID (numbers only):",
        "ar": "⚠️ UID غير صحيح! أدخل UID من 14 إلى 16 رقمًا (أرقام فقط):",
        "hi": "⚠️ UID सही नहीं है! 14 से 16 अंकों का UID दें (सिर्फ़ संख्या):",
    },
    "fb_ask_cookies": {
        "bn": "🍪 এবার আপনার ফেসবুক অ্যাকাউন্টের Cookies-টি দিন:",
        "en": "🍪 Now enter the Cookies of your Facebook account:",
        "ar": "🍪 الآن أدخل Cookies حساب فيسبوك الخاص بك:",
        "hi": "🍪 अब अपने फेसबुक अकाउंट की Cookies दें:",
    },
    "fb_bad_cookies": {
        "bn": "⚠️ Cookies ঠিক নেই! পুরো Cookies কপি করে দিন:",
        "en": "⚠️ Invalid Cookies! Copy and send the full Cookies:",
        "ar": "⚠️ Cookies غير صحيحة! انسخ Cookies كاملة وأرسلها:",
        "hi": "⚠️ Cookies सही नहीं हैं! पूरी Cookies कॉपी करके भेजें:",
    },
    "fb_submitted": {
        "bn": "✅ আপনার UID এবং Cookies সফলভাবে জমা হয়েছে!\n\nএটি এডমিন প্যানেলে রিভিউতে আছে। সব ঠিক থাকলে ১২-৭২ ঘণ্টার মধ্যে পেমেন্ট আপনার ব্যালেন্সে যুক্ত করে দেওয়া হবে।",
        "en": "✅ Your UID and Cookies were submitted successfully!\n\nIt is under admin review. If everything is correct, payment will be added to your balance within 12-72 hours.",
        "ar": "✅ تم إرسال UID و Cookies بنجاح!\n\nالطلب قيد مراجعة الإدارة. إذا كان كل شيء صحيحًا سيُضاف المبلغ إلى رصيدك خلال 12-72 ساعة.",
        "hi": "✅ आपका UID और Cookies सफलतापूर्वक जमा हो गए!\n\nयह एडमिन की समीक्षा में है। सब सही होने पर 12-72 घंटे में पेमेंट आपके बैलेंस में जुड़ जाएगा।",
    },
    "fb_cancelled": {
        "bn": "❌ কাজটি বাতিল করা হয়েছে। আপনি আগের মেনুতে ফিরে এসেছেন।",
        "en": "❌ Task cancelled. You are back at the previous menu.",
        "ar": "❌ تم إلغاء المهمة. عدت إلى القائمة السابقة.",
        "hi": "❌ काम रद्द कर दिया गया। आप पिछले मेनू में लौट आए हैं।",
    },
    "no_active_task": {
        "bn": "⚠️ আপনার কোনো সক্রিয় কাজ নেই। আগে কাজ নিন।",
        "en": "⚠️ You have no active task. Take a task first.",
        "ar": "⚠️ ليس لديك مهمة نشطة. ابدأ مهمة أولًا.",
        "hi": "⚠️ आपका कोई सक्रिय काम नहीं है। पहले काम लें।",
    },
    "ask_screenshot": {
        "bn": "📸 স্ক্রিনশট জমা দিন:",
        "en": "📸 Submit your screenshot:",
        "ar": "📸 أرسل لقطة الشاشة:",
        "hi": "📸 अपना स्क्रीनशॉट जमा करें:",
    },
    "need_photo": {
        "bn": "দয়া করে স্ক্রিনশট (ছবি) পাঠান:",
        "en": "Please send a screenshot (photo):",
        "ar": "من فضلك أرسل لقطة شاشة (صورة):",
        "hi": "कृपया स्क्रीनशॉट (फ़ोटो) भेजें:",
    },
    "shot_ok": {
        "bn": "✅ স্ক্রিনশট সাবমিট হয়েছে!",
        "en": "✅ Screenshot submitted!",
        "ar": "✅ تم إرسال لقطة الشاشة!",
        "hi": "✅ स्क्रीनशॉट जमा हो गया!",
    },
    "cancelled": {
        "bn": "বাতিল করা হয়েছে।",
        "en": "Cancelled.",
        "ar": "تم الإلغاء.",
        "hi": "रद्द कर दिया गया।",
    },
    "ref_joined": {
        "bn": "👥 আপনার লিংকে নতুন একজন ইউজার জয়েন করেছে! ({name})",
        "en": "👥 A new user joined using your link! ({name})",
        "ar": "👥 انضم مستخدم جديد عبر رابطك! ({name})",
        "hi": "👥 आपके लिंक से एक नया यूज़र जुड़ा है! ({name})",
    },
    "ref_bonus": {
        "bn": "🎉 আপনার সফল রেফারেলের জন্য {amt} টাকা বোনাস যোগ হয়েছে! 💸",
        "en": "🎉 {amt} Taka bonus added for your successful referral! 💸",
        "ar": "🎉 تمت إضافة مكافأة {amt} تاكا مقابل إحالتك الناجحة! 💸",
        "hi": "🎉 आपके सफल रेफरल के लिए {amt} टका बोनस जोड़ा गया! 💸",
    },
    "approved": {
        "bn": "🎉 আপনার কাজটি অনুমোদিত হয়েছে এবং অ্যাকাউন্টে {amt} টাকা যোগ করা হয়েছে!",
        "en": "🎉 Your task was approved and {amt} Taka was added to your account!",
        "ar": "🎉 تمت الموافقة على مهمتك وإضافة {amt} تاكا إلى حسابك!",
        "hi": "🎉 आपका काम मंज़ूर हो गया और आपके अकाउंट में {amt} टका जोड़े गए!",
    },
    "gmail_bad": {
        "bn": "❌😔 দুঃখিত! আপনার জিমেইল অ্যাকাউন্টটি নষ্ট হয়ে গেছে (ব্যবহারের অযোগ্য)। 🚫📧\n\n🔄 নতুন করে সঠিকভাবে কাজ করে আবার জমা দিন। 💪✨",
        "en": "❌😔 Sorry! Your Gmail account is dead (not usable). 🚫📧\n\n🔄 Please do the task again correctly and resubmit. 💪✨",
        "ar": "❌😔 عذرًا! حساب Gmail الخاص بك تالف (غير صالح للاستخدام). 🚫📧\n\n🔄 يرجى تنفيذ المهمة مرة أخرى بشكل صحيح وإعادة الإرسال. 💪✨",
        "hi": "❌😔 क्षमा करें! आपका Gmail अकाउंट खराब हो गया है (इस्तेमाल के लायक नहीं)। 🚫📧\n\n🔄 कृपया काम सही तरीके से दोबारा करके फिर से जमा करें। 💪✨",
    },
    "fb_bad": {
        "bn": "❌😔 দুঃখিত! আপনার ফেসবুক অ্যাকাউন্টটি নষ্ট হয়ে গেছে (ব্যবহারের অযোগ্য)। 🚫📘\n\n🔄 নতুন করে সঠিকভাবে কাজ করে আবার জমা দিন। 💪✨",
        "en": "❌😔 Sorry! Your Facebook account is dead (not usable). 🚫📘\n\n🔄 Please do the task again correctly and resubmit. 💪✨",
        "ar": "❌😔 عذرًا! حساب فيسبوك الخاص بك تالف (غير صالح للاستخدام). 🚫📘\n\n🔄 يرجى تنفيذ المهمة مرة أخرى بشكل صحيح وإعادة الإرسال. 💪✨",
        "hi": "❌😔 क्षमा करें! आपका फेसबुक अकाउंट खराब हो गया है (इस्तेमाल के लायक नहीं)। 🚫📘\n\n🔄 कृपया काम सही तरीके से दोबारा करके फिर से जमा करें। 💪✨",
    },
    "fb_ok": {
        "bn": "✅🎉 অভিনন্দন! আপনার ফেসবুক অ্যাকাউন্টটি সঠিক এবং এডমিনের কাছে পৌঁছে গেছে। 📘👍\n\n👨‍💼 এডমিন চেক করে আপনার ব্যালেন্সে টাকা এড করে দেবে। 💰⏳\n\nধৈর্য ধরে অপেক্ষা করুন। 😊🙏",
        "en": "✅🎉 Congratulations! Your Facebook account is correct and has reached the admin. 📘👍\n\n👨‍💼 The admin will check it and add the money to your balance. 💰⏳\n\nPlease wait patiently. 😊🙏",
        "ar": "✅🎉 تهانينا! حساب فيسبوك الخاص بك صحيح ووصل إلى المشرف. 📘👍\n\n👨‍💼 سيقوم المشرف بالتحقق وإضافة المبلغ إلى رصيدك. 💰⏳\n\nيرجى الانتظار بصبر. 😊🙏",
        "hi": "✅🎉 बधाई हो! आपका फेसबुक अकाउंट सही है और एडमिन के पास पहुँच गया है। 📘👍\n\n👨‍💼 एडमिन जाँच करके आपके बैलेंस में पैसे जोड़ देगा। 💰⏳\n\nकृपया धैर्य रखें। 😊🙏",
    },
    "ig_bad": {
        "bn": "❌😔 দুঃখিত! আপনার ইনস্টাগ্রাম অ্যাকাউন্টটি নষ্ট হয়ে গেছে (ব্যবহারের অযোগ্য)। 🚫📷\n\n🔄 নতুন করে সঠিকভাবে কাজ করে আবার জমা দিন। 💪✨",
        "en": "❌😔 Sorry! Your Instagram account is dead (not usable). 🚫📷\n\n🔄 Please do the task again correctly and resubmit. 💪✨",
        "ar": "❌😔 عذرًا! حساب إنستغرام الخاص بك تالف (غير صالح للاستخدام). 🚫📷\n\n🔄 يرجى تنفيذ المهمة مرة أخرى بشكل صحيح وإعادة الإرسال. 💪✨",
        "hi": "❌😔 क्षमा करें! आपका इंस्टाग्राम अकाउंट खराब हो गया है (इस्तेमाल के लायक नहीं)। 🚫📷\n\n🔄 कृपया काम सही तरीके से दोबारा करके फिर से जमा करें। 💪✨",
    },
    "ig_ok": {
        "bn": "✅🎉 অভিনন্দন! আপনার ইনস্টাগ্রাম অ্যাকাউন্টটি সঠিক এবং এডমিনের কাছে পৌঁছে গেছে। 📷👍\n\n👨‍💼 এডমিন চেক করে আপনার ব্যালেন্সে টাকা এড করে দেবে। 💰⏳\n\nধৈর্য ধরে অপেক্ষা করুন। 😊🙏",
        "en": "✅🎉 Congratulations! Your Instagram account is correct and has reached the admin. 📷👍\n\n👨‍💼 The admin will check it and add the money to your balance. 💰⏳\n\nPlease wait patiently. 😊🙏",
        "ar": "✅🎉 تهانينا! حساب إنستغرام الخاص بك صحيح ووصل إلى المشرف. 📷👍\n\n👨‍💼 سيقوم المشرف بالتحقق وإضافة المبلغ إلى رصيدك. 💰⏳\n\nيرجى الانتظار بصبر. 😊🙏",
        "hi": "✅🎉 बधाई हो! आपका इंस्टाग्राम अकाउंट सही है और एडमिन के पास पहुँच गया है। 📷👍\n\n👨‍💼 एडमिन जाँच करके आपके बैलेंस में पैसे जोड़ देगा। 💰⏳\n\nकृपया धैर्य रखें। 😊🙏",
    },
    "gmail_ok": {
        "bn": "✅🎉 অভিনন্দন! আপনার জিমেইল অ্যাকাউন্টটি সঠিক এবং এডমিনের কাছে পৌঁছে গেছে। 📧👍\n\n👨‍💼 এডমিন চেক করে আপনার ব্যালেন্সে টাকা এড করে দেবে। 💰⏳\n\nধৈর্য ধরে অপেক্ষা করুন। 😊🙏",
        "en": "✅🎉 Congratulations! Your Gmail account is correct and has reached the admin. 📧👍\n\n👨‍💼 The admin will check it and add the money to your balance. 💰⏳\n\nPlease wait patiently. 😊🙏",
        "ar": "✅🎉 تهانينا! حساب Gmail الخاص بك صحيح ووصل إلى المشرف. 📧👍\n\n👨‍💼 سيقوم المشرف بالتحقق وإضافة المبلغ إلى رصيدك. 💰⏳\n\nيرجى الانتظار بصبر. 😊🙏",
        "hi": "✅🎉 बधाई हो! आपका Gmail अकाउंट सही है और एडमिन के पास पहुँच गया है। 📧👍\n\n👨‍💼 एडमिन जाँच करके आपके बैलेंस में पैसे जोड़ देगा। 💰⏳\n\nकृपया धैर्य रखें। 😊🙏",
    },
    "refund": {
        "bn": "💸✅ আপনার {amt} টাকা ফেরত দেওয়া হয়েছে! 🎉 ব্যালেন্সে যোগ হয়ে গেছে, চেক করে নিন। 😊💰",
        "en": "💸✅ Your {amt} Taka has been refunded! 🎉 It has been added to your balance, please check. 😊💰",
        "ar": "💸✅ تمت إعادة {amt} تاكا إليك! 🎉 تمت إضافتها إلى رصيدك، يرجى التحقق. 😊💰",
        "hi": "💸✅ आपके {amt} टका वापस कर दिए गए हैं! 🎉 बैलेंस में जोड़ दिए गए हैं, कृपया चेक करें। 😊💰",
    },
    "cut": {
        "bn": "⚠️ আপনার অ্যাকাউন্ট থেকে {amt} টাকা কেটে নেওয়া হয়েছে।",
        "en": "⚠️ {amt} Taka was deducted from your account.",
        "ar": "⚠️ تم خصم {amt} تاكا من حسابك.",
        "hi": "⚠️ आपके अकाउंट से {amt} टका काटे गए हैं।",
    },
    "rejected_gmail": {
        "bn": "❌ আপনার জিমেইল কাজটি বাতিল করা হয়েছে।",
        "en": "❌ Your Gmail task was rejected.",
        "ar": "❌ تم رفض مهمة Gmail الخاصة بك.",
        "hi": "❌ आपका Gmail काम रद्द कर दिया गया है।",
    },
    "rejected_fb": {
        "bn": "❌ আপনার ফেসবুক কাজটি বাতিল করা হয়েছে।",
        "en": "❌ Your Facebook task was rejected.",
        "ar": "❌ تم رفض مهمة فيسبوك الخاصة بك.",
        "hi": "❌ आपका फेसबुक काम रद्द कर दिया गया है।",
    },
    "rejected_ig": {
        "bn": "❌ আপনার ইনস্টাগ্রাম কাজটি বাতিল করা হয়েছে।",
        "en": "❌ Your Instagram task was rejected.",
        "ar": "❌ تم رفض مهمة إنستغرام الخاصة بك.",
        "hi": "❌ आपका इंस्टाग्राम काम रद्द कर दिया गया है।",
    },
    "blocked": {
        "bn": "🚫 আপনাকে এই বট ব্যবহার থেকে ব্লক করা হয়েছে।",
        "en": "🚫 You have been blocked from using this bot.",
        "ar": "🚫 تم حظرك من استخدام هذا البوت.",
        "hi": "🚫 आपको इस बॉट का उपयोग करने से ब्लॉक कर दिया गया है।",
    },
    "unblocked": {
        "bn": "✅ আপনাকে আনব্লক করা হয়েছে, আপনি আবার বট ব্যবহার করতে পারবেন।",
        "en": "✅ You have been unblocked. You can use the bot again.",
        "ar": "✅ تم رفع الحظر عنك. يمكنك استخدام البوت مرة أخرى.",
        "hi": "✅ आपको अनब्लॉक कर दिया गया है। आप फिर से बॉट इस्तेमाल कर सकते हैं।",
    },
    "online": {
        "bn": "🔔 বট অনলাইনে আছে! কাজ শুরু করুন।",
        "en": "🔔 The bot is online! Start working.",
        "ar": "🔔 البوت متصل الآن! ابدأ العمل.",
        "hi": "🔔 बॉट ऑनलाइन है! काम शुरू करें।",
    },
    "notice": {
        "bn": "📢 নোটিশ:\n\n{text}",
        "en": "📢 Notice:\n\n{text}",
        "ar": "📢 إشعار:\n\n{text}",
        "hi": "📢 सूचना:\n\n{text}",
    },
    "admin_msg": {
        "bn": "📩 এডমিনের মেসেজ:\n\n{text}",
        "en": "📩 Message from admin:\n\n{text}",
        "ar": "📩 رسالة من الإدارة:\n\n{text}",
        "hi": "📩 एडमिन का संदेश:\n\n{text}",
    },
    "ai_msg": {
        "bn": "Ai মেসেজ ⚡\n\n{text}",
        "en": "Ai Message ⚡\n\n{text}",
        "ar": "رسالة Ai ⚡\n\n{text}",
        "hi": "Ai संदेश ⚡\n\n{text}",
    },
    "choose_lang": {
        "bn": "ভাষা নির্বাচন করুন / Select language:",
        "en": "ভাষা নির্বাচন করুন / Select language:",
        "ar": "ভাষা নির্বাচন করুন / Select language:",
        "hi": "ভাষা নির্বাচন করুন / Select language:",
    },
}


def tr(lang, key, **kw):
    """ইউজারের ভাষা অনুযায়ী বার্তা দেয়; ভাষা না থাকলে বাংলা।"""
    entry = TR[key]
    text = entry.get(lang) or entry["bn"]
    return text.format(**kw) if kw else text


def lang_keyboard():
    return InlineKeyboardMarkup(
        [
            [ibtn("🇧🇩 বাংলা", "lang_bn", BLUE), ibtn("🇬🇧 English", "lang_en", GREEN)],
            [ibtn("🇸🇦 العربية", "lang_ar", GREEN), ibtn("🇮🇳 हिन्दी", "lang_hi", BLUE)],
        ]
    )


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
        [kb_btn(L("old_gmail", lang), GREEN)],
        [kb_btn(L("cancel", lang), RED)],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_task_keyboard(lang="bn"):
    rows = [[kb_btn(L("submit", lang), GREEN), kb_btn(L("cancel_task", lang), RED)]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _collect_menu_texts():
    texts = set()
    for lang in LANGS:
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


async def notify_admin(context, text, photo=None, reply_markup=None):
    try:
        if photo:
            await context.bot.send_photo(
                chat_id=ADMIN_ID, photo=photo, caption=text[:1000], parse_mode="HTML",
                reply_markup=reply_markup,
            )
        else:
            await context.bot.send_message(
                chat_id=ADMIN_ID, text=text, parse_mode="HTML", reply_markup=reply_markup
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
    FACEBOOK_FILE,
    FB_ACCOUNTS_FILE,
    INSTAGRAM_FILE,
]


def build_backup_dict():
    return {
        "users.json": list(load_registered_users()),
        "user_stats.json": user_data_db,
        "blocked_users.json": list(blocked_users),
        "settings.json": settings,
        "gmail_tasks.json": gmail_tasks,
        "facebook_tasks.json": facebook_tasks,
        "fb_accounts.json": fb_accounts,
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
        ("facebook_tasks.json", facebook_tasks, FACEBOOK_FILE),
        ("fb_accounts.json", fb_accounts, FB_ACCOUNTS_FILE),
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
                    tr(get_lang(ref_id), "ref_joined", name=user.first_name),
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

    await update.message.reply_text(
        f"স্বাগতম / Welcome / مرحبًا / स्वागत है, {user.first_name}!\n\n"
        "ভাষা নির্বাচন করুন / Select your language\n"
        "اختر لغتك / अपनी भाषा चुनें",
        reply_markup=lang_keyboard(),
    )


# ============ অ্যাডমিন কমান্ড ============
async def online_notice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    for uid in load_registered_users():
        await safe_send(context, uid, tr(get_lang(uid), "online"))
    await update.message.reply_text("✅ অনলাইন নোটিশ পাঠানো হয়েছে!")


async def notice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    txt = update.message.text.replace("/notice", "", 1).strip()
    if not txt:
        await update.message.reply_text("ব্যবহার: /notice [লেখা]")
        return
    for uid in load_registered_users():
        await safe_send(context, uid, tr(get_lang(uid), "notice", text=txt))
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


async def add_fb_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/addfb নাম | পদবি | পাসওয়ার্ড  (একসাথে অনেক লাইনে অনেকগুলো দেওয়া যায়)"""
    if update.effective_user.id != ADMIN_ID:
        return
    parts_msg = (update.message.text or "").split(None, 1)
    body = parts_msg[1] if len(parts_msg) > 1 else ""
    added, bad = 0, []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [x.strip() for x in re.split(r"[|।｜]", line)]
        if len(parts) != 3 or not all(parts):
            bad.append(line)
            continue
        fb_accounts.append(
            {"first_name": parts[0], "last_name": parts[1], "password": parts[2]}
        )
        added += 1
    if added:
        save_json_file(FB_ACCOUNTS_FILE, fb_accounts)
    if not added and not bad:
        await update.message.reply_text(
            "ব্যবহার: /addfb প্রথম নাম | শেষ নাম | পাসওয়ার্ড\n"
            "(আলাদা করতে | অথবা ।  দুটোই চলবে)\n\n"
            "একসাথে অনেকগুলো দিতে প্রতি লাইনে একটা করে লিখুন:\n"
            "/addfb রাকিব | হাসান | Pass@123\n"
            "তানভীর | আহমেদ | Fb@98765"
        )
        return
    msg = f"✅ {added} টি ফেসবুক অ্যাকাউন্ট যোগ হয়েছে! স্টকে মোট: {len(fb_accounts)} টি।"
    if bad:
        msg += "\n\n⚠️ ভুল ফরম্যাটের লাইন (যোগ হয়নি):\n" + "\n".join(bad[:10])
    await update.message.reply_text(msg)


async def list_fb_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not fb_accounts:
        await update.message.reply_text("📭 স্টকে কোনো ফেসবুক অ্যাকাউন্ট নেই। /addfb দিয়ে যোগ করুন।")
        return
    lines = [
        f"{i}. {a['first_name']} {a['last_name']} | {a['password']}"
        for i, a in enumerate(fb_accounts[:30], 1)
    ]
    more = f"\n... আরও {len(fb_accounts) - 30} টি" if len(fb_accounts) > 30 else ""
    await update.message.reply_text(
        f"📘 স্টকে ফেসবুক অ্যাকাউন্ট: {len(fb_accounts)} টি\n\n" + "\n".join(lines) + more
    )


async def clear_fb_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    fb_accounts.clear()
    save_json_file(FB_ACCOUNTS_FILE, fb_accounts)
    await update.message.reply_text("🗑️ স্টকের সব ফেসবুক অ্যাকাউন্ট মুছে ফেলা হয়েছে!")


# ফেসবুক কাজের সময় ইউজারকে দেওয়া অ্যাকাউন্ট বাতিল হলে আবার স্টকে ফেরত যায়
def release_fb_account(context):
    acc = context.user_data.pop("fb_account", None)
    context.user_data.pop("fb_uid", None)
    if acc:
        fb_accounts.insert(0, acc)
        save_json_file(FB_ACCOUNTS_FILE, fb_accounts)


def fb_account_text(acc, lang="bn"):
    return tr(
        lang,
        "fb_account",
        first=esc(acc["first_name"]),
        last=esc(acc["last_name"]),
        pw=esc(acc["password"]),
    )


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


def make_reject_handler(label, usage, kind):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            return
        try:
            tid = int(context.args[0])
            st = get_user_stats(tid)
            if st["pending"] > 0:
                st["pending"] -= 1
            save_user_stats()
            await safe_send(context, tid, tr(get_lang(tid), "rejected_" + kind))
            await update.message.reply_text(
                f"✅ ইউজার {tid} এর {label}কাজ রিজেক্ট করা হয়েছে।"
            )
        except Exception:
            await update.message.reply_text(f"ব্যবহার: {usage} [User_ID]")

    return handler


reject_gmail = make_reject_handler("জিমেইল ", "/rejectg", "gmail")
reject_facebook = make_reject_handler("ফেসবুক ", "/rejectfb", "fb")
reject_instagram = make_reject_handler("ইনস্টাগ্রাম ", "/rejectig", "ig")




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
    if st.get("gmail_credit", 0) > 0:
        # এই জিমেইল কাজ "সম্পূর্ণ" বাটন থেকেই আগে গোনা হয়েছে, তাই দ্বিতীয়বার গোনা হবে না
        st["gmail_credit"] -= 1
    else:
        if st["pending"] > 0:
            st["pending"] -= 1
        st["completed"] += 1

    if not st["has_completed_first_task"]:
        st["has_completed_first_task"] = True
    save_user_stats()

    await update.message.reply_text(
        f"✅ ব্যালেন্স যোগ সফল। বর্তমান ব্যালেন্স: {st['balance']} টাকা।"
    )
    await safe_send(context, tid, tr(get_lang(tid), "approved", amt=amt))


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
    notify = bool(settings.get("cut_notify", True))
    await update.message.reply_text(
        f"✅ ব্যালেন্স কাটা সফল। বর্তমান ব্যালেন্স: {st['balance']} টাকা।\n"
        + ("🔔 ইউজারকে মেসেজ পাঠানো হয়েছে।" if notify else "🔕 ইউজারকে কোনো মেসেজ পাঠানো হয়নি।")
    )
    if notify:
        await safe_send(context, tid, tr(get_lang(tid), "cut", amt=amt))


async def refund_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ব্যবহার: /refund [User_ID] [টাকা] — ইউজারের ব্যালেন্সে টাকা ফেরত দেয় এবং ইউজারকে ফেরতের মেসেজ পাঠায়।
    (কাজের পরিসংখ্যান pending/completed বদলায় না)"""
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        tid = int(context.args[0])
        amt = float(context.args[1])
        if amt <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("ব্যবহার: /refund [User_ID] [টাকা]")
        return
    st = get_user_stats(tid)
    st["balance"] += amt
    save_user_stats()
    await update.message.reply_text(
        f"✅ ফেরত যোগ সফল। বর্তমান ব্যালেন্স: {st['balance']} টাকা।"
    )
    await safe_send(context, tid, tr(get_lang(tid), "refund", amt=format_amount(amt)))


async def cut_notice_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ব্যবহার: /cutnotice on|off
    off থাকলে /cut দিয়ে ব্যালেন্স কাটলে ইউজার কোনো মেসেজ পাবে না; সে নিজে ব্যালেন্স বাটনে চাপলেই দেখবে।"""
    if update.effective_user.id != ADMIN_ID:
        return
    arg = (context.args[0].lower() if context.args else "")
    if arg in ("on", "yes", "1", "চালু"):
        settings["cut_notify"] = True
        save_settings()
    elif arg in ("off", "no", "0", "বন্ধ"):
        settings["cut_notify"] = False
        save_settings()
    elif arg:
        await update.message.reply_text("ব্যবহার: /cutnotice on  অথবা  /cutnotice off")
        return
    on = bool(settings.get("cut_notify", True))
    await update.message.reply_text(
        "🔔 টাকা কাটলে ইউজারকে মেসেজ যাবে (চালু)" if on
        else "🔕 টাকা কাটলে ইউজারকে কোনো মেসেজ যাবে না (বন্ধ) — ইউজার ব্যালেন্সে চাপলেই দেখবে।"
    )


async def set_ref_gmail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ব্যবহার: /setrefgmail [সংখ্যা] — রেফার করা ইউজার কতটি জিমেইল জমা দিলে রেফারকারী বোনাস পাবে"""
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        n = int(context.args[0])
        if n < 1:
            raise ValueError
    except Exception:
        await update.message.reply_text(
            f"ব্যবহার: /setrefgmail [সংখ্যা]\nবর্তমান: {settings.get('referral_gmail_required', 4)} টি"
        )
        return
    settings["referral_gmail_required"] = n
    save_settings()
    await update.message.reply_text(f"✅ এখন থেকে রেফার করা ইউজার {n} টি জিমেইল জমা দিলে রেফারকারী বোনাস পাবে।")


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
        f"📧 জিমেইল সাবমিট: {st.get('gmail_submitted', 0)} টি | সম্পূর্ণ: {st.get('gmail_completed', 0)} টি | রেফার বোনাস দেওয়া হয়েছে: {'হ্যাঁ' if st.get('ref_bonus_paid') else 'না'}\n"
        f"🔖 স্ট্যাটাস: {status}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def total_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        f"📊 মোট ইউজার: {len(load_registered_users())} জন\n"
        f"📂 জিমেইল কাজ: {len(gmail_tasks)} টি\n"
        f"📘 ফেসবুক অ্যাকাউন্ট (স্টকে): {len(fb_accounts)} টি\n"
        f"📷 ইনস্টাগ্রাম কাজ: {len(instagram_tasks)} টি"
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
    await safe_send(context, tid, tr(get_lang(tid), "blocked"))


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
        await safe_send(context, tid, tr(get_lang(tid), "unblocked"))
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
            await update.message.reply_text(tr(get_lang(user.id), "blocked"))
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
    ok = await safe_send(context, tid, tr(get_lang(tid), "admin_msg", text=text))
    if ok:
        await update.message.reply_text(f"✅ ইউজার {tid}-কে মেসেজ পাঠানো হয়েছে।")
    else:
        await update.message.reply_text("❌ মেসেজ পাঠানো যায়নি। User_ID ঠিক আছে কি না দেখুন।")


async def admin_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """অ্যাডমিন /aimsg [User_ID] [লেখা] দিলে ইউজার সবার উপরে "Ai মেসেজ ⚡" দেখে,
    তার নিচে অ্যাডমিনের লেখা মেসেজটা দেখে।"""
    if update.effective_user.id != ADMIN_ID:
        return
    parts = (update.message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await update.message.reply_text("ব্যবহার: /aimsg [User_ID] [লেখা]")
        return
    try:
        tid = int(parts[1])
    except ValueError:
        await update.message.reply_text("সঠিক User_ID দিন।")
        return
    text = parts[2]
    ok = await safe_send(context, tid, tr(get_lang(tid), "ai_msg", text=text))
    if ok:
        await update.message.reply_text(f"✅ ইউজার {tid}-কে Ai মেসেজ পাঠানো হয়েছে।")
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
        msg = tr(
            lang,
            "referral",
            link=ref_link,
            bonus=format_amount(settings["referral_bonus"]),
            count=st["ref_count"],
            earn=st["ref_earnings"],
        )
        extra = settings.get("referral_extra", "").strip()
        if extra:
            msg += f"\n\n{extra}"
        await update.message.reply_text(msg, reply_markup=get_main_keyboard(lang))
        return

    # কাজ ক্যাটাগরি
    if is_btn(text, "tasks"):
        await update.message.reply_text(
            tr(lang, "tasks_cat"), reply_markup=get_task_sub_keyboard(lang)
        )
        return

    if is_btn(text, "cancel"):
        await update.message.reply_text(
            tr(lang, "main_menu"), reply_markup=get_main_keyboard(lang)
        )
        return

    if is_btn(text, "gmail"):
        label = tr(lang, "gmail_label", price=settings["gmail_price"])
        kb = [[InlineKeyboardButton(label, callback_data="gmail_details")]]
        await update.message.reply_text(
            tr(lang, "gmail_info"), reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    if is_btn(text, "facebook"):
        label = tr(lang, "fb_label", price=settings["fb_price"])
        kb = [[InlineKeyboardButton(label, callback_data="fb_details")]]
        await update.message.reply_text(
            tr(lang, "fb_info"), reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    if is_btn(text, "instagram"):
        label = tr(lang, "ig_label", price=settings["ig_price"])
        kb = [[InlineKeyboardButton(label, callback_data="insta_details")]]
        await update.message.reply_text(
            tr(lang, "ig_info"), reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    if is_btn(text, "old_gmail"):
        label = tr(lang, "old_label", price=settings["old_gmail_price"])
        kb = [[ibtn(label, "old_gmail_info", GREEN)]]
        await update.message.reply_text(
            tr(lang, "old_info"), reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    if is_btn(text, "language"):
        await update.message.reply_text(
            tr(lang, "choose_lang"), reply_markup=lang_keyboard()
        )
        return

    if is_btn(text, "sell_usd"):
        kb = [
            [
                InlineKeyboardButton(
                    tr(lang, "usd_rate", rate=settings["usd_rate"]),
                    callback_data="sell_usd_rate",
                )
            ]
        ]
        await update.message.reply_text(
            tr(lang, "usd_service"), reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    # ব্যালেন্স
    if is_btn(text, "balance"):
        st = get_user_stats(user.id)
        msg = tr(
            lang,
            "balance",
            completed=st["completed"],
            pending=st["pending"],
            balance=st["balance"],
        )
        await update.message.reply_text(msg, reply_markup=get_main_keyboard(lang))
        return

    # উইথড্র
    if is_btn(text, "withdraw"):
        kb = [
            [InlineKeyboardButton(tr(lang, "wd_bkash"), callback_data="withdraw_bkash")],
            [InlineKeyboardButton(tr(lang, "wd_nagad"), callback_data="withdraw_nagad")],
            [InlineKeyboardButton(tr(lang, "wd_recharge"), callback_data="withdraw_recharge")],
            [InlineKeyboardButton("Binance", callback_data="withdraw_binance")],
        ]
        await update.message.reply_text(
            tr(lang, "wd_title"), reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    if is_btn(text, "support"):
        await update.message.reply_text(
            get_support_message(lang), reply_markup=get_main_keyboard(lang)
        )
        return

    if is_btn(text, "newbie"):
        kb = [[InlineKeyboardButton(tr(lang, "newbie_btn"), url="https://t.me/EasyIncomeBD_Payment")]]
        await update.message.reply_text(
            tr(lang, "newbie_title"), reply_markup=InlineKeyboardMarkup(kb)
        )
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
            tr(lang, "main_menu"), reply_markup=get_main_keyboard(lang)
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
        if sl not in LANGS:
            return
        get_user_stats(user.id)["lang"] = sl
        save_user_stats()
        await query.message.reply_text(
            tr(sl, "lang_set"), reply_markup=get_main_keyboard(sl)
        )
        return

    if data == "old_gmail_info":
        await query.message.reply_text(
            tr(lang, "old_gmail_contact", user=settings["support_username"]),
            reply_markup=get_task_sub_keyboard(lang),
        )
        return

    if data == "sell_usd_rate":
        kb = [[InlineKeyboardButton(tr(lang, "usd_start_btn"), callback_data="sell_usd_start")]]
        await query.message.reply_text(
            tr(lang, "usd_rate", rate=settings["usd_rate"]),
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if data == "gmail_details":
        if not gmail_tasks:
            await query.message.reply_text(
                tr(lang, "no_gmail"), reply_markup=get_task_sub_keyboard(lang)
            )
            return
        task_text = gmail_tasks[0]
        context.user_data["current_task_text"] = task_text
        context.user_data["current_task_type"] = "gmail"
        sent = await query.message.reply_text(
            tr(lang, "gmail_task", task=task_text), reply_markup=get_task_keyboard(lang)
        )
        context.user_data["last_task_msg_id"] = sent.message_id
        return

    if data == "fb_details":
        # একই ইউজার আবার চাপলে নতুন অ্যাকাউন্ট না দিয়ে আগেরটাই দেখাবে
        acc = context.user_data.get("fb_account")
        if not acc:
            if not fb_accounts:
                await query.message.reply_text(
                    tr(lang, "no_fb"), reply_markup=get_task_sub_keyboard(lang)
                )
                return
            acc = fb_accounts.pop(0)
            save_json_file(FB_ACCOUNTS_FILE, fb_accounts)
            context.user_data["fb_account"] = acc
        fb_kb = [
            [
                InlineKeyboardButton(tr(lang, "uid_btn"), callback_data="fb_uid"),
                InlineKeyboardButton(L("cancel", lang), callback_data="fb_cancel"),
            ]
        ]
        await query.message.reply_text(
            fb_account_text(acc, lang),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(fb_kb),
        )
        return

    if data == "insta_details":
        if not instagram_tasks:
            await query.message.reply_text(
                tr(lang, "no_ig"), reply_markup=get_task_sub_keyboard(lang)
            )
            return
        task_text = instagram_tasks[0]
        context.user_data["current_task_text"] = task_text
        context.user_data["current_task_type"] = "instagram"
        ig_kb = [[InlineKeyboardButton("✅ Done", callback_data="insta_done")]]
        await query.message.reply_text(
            tr(lang, "ig_task", task=task_text),
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
        ig_review_kb = InlineKeyboardMarkup([[
            ibtn("❌ রিজেক্ট", callback_data=f"igx:{user.id}", style=RED),
            ibtn("✅ সম্পূর্ণ", callback_data=f"igok:{user.id}", style=GREEN),
        ]])
        await notify_admin(context, admin_text, reply_markup=ig_review_kb)
        clear_task_state(context)
        await query.message.reply_text(
            tr(lang, "ig_done"), reply_markup=get_main_keyboard(lang)
        )
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

    if data == "fb_uid":
        if not context.user_data.get("fb_account"):
            await query.message.reply_text(
                tr(lang, "fb_no_active"), reply_markup=get_main_keyboard(lang)
            )
            return ConversationHandler.END
        await query.message.reply_text(tr(lang, "fb_ask_uid"))
        return FB_UID

    if data == "fb_cancel":
        release_fb_account(context)
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.reply_text(
            tr(lang, "fb_cancelled"), reply_markup=get_main_keyboard(lang)
        )
        return ConversationHandler.END

    if data == "sell_usd_start":
        await query.message.reply_text(tr(lang, "usd_ask_amt"))
        return SELL_USD_AMT

    if data == "sell_usd_done":
        await query.message.reply_text(tr(lang, "usd_ask_proof"))
        return SELL_USD_PROOF

    if data in ("withdraw_bkash", "withdraw_nagad", "withdraw_recharge"):
        if st["balance"] < settings["withdraw_min"]:
            await query.message.reply_text(
                tr(lang, "wd_min_err", min=settings["withdraw_min"]),
                reply_markup=get_main_keyboard(lang),
            )
            return ConversationHandler.END
        # অ্যাডমিনকে সবসময় বাংলা নাম যায়; ইউজারকে তার ভাষার নাম দেখানো হয়
        method = {
            "withdraw_bkash": "বিকাশ",
            "withdraw_nagad": "নগদ",
            "withdraw_recharge": "মোবাইল রিচার্জ",
        }[data]
        method_key = {
            "withdraw_bkash": "wd_bkash",
            "withdraw_nagad": "wd_nagad",
            "withdraw_recharge": "wd_recharge",
        }[data]
        context.user_data["withdraw_method"] = method
        context.user_data["withdraw_min"] = settings["withdraw_min"]
        await query.message.reply_text(
            tr(lang, "wd_ask_number", method=tr(lang, method_key))
        )
        return WITHDRAW_NUM

    if data == "withdraw_binance":
        if st["balance"] < settings["withdraw_min_binance"]:
            await query.message.reply_text(
                tr(lang, "wd_min_err", min=settings["withdraw_min_binance"]),
                reply_markup=get_main_keyboard(lang),
            )
            return ConversationHandler.END
        context.user_data["withdraw_method"] = "Binance"
        context.user_data["withdraw_min"] = settings["withdraw_min_binance"]
        await query.message.reply_text(tr(lang, "wd_ask_binance"))
        return WITHDRAW_NUM

    return ConversationHandler.END


# ============ ডলার বিক্রি ============
async def receive_sell_usd_amt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    try:
        amt = float(update.message.text)
        if amt <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(tr(lang, "usd_bad_num"))
        return SELL_USD_AMT
    context.user_data["sell_usd_amt"] = amt
    kb = [[InlineKeyboardButton("Payment Done ✅", callback_data="sell_usd_done")]]
    await update.message.reply_text(
        tr(
            lang,
            "usd_send",
            uid=BINANCE_UID,
            addr=USDT_BEP20_ADDRESS,
            amt=amt * settings["usd_rate"],
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return ConversationHandler.END


async def receive_sell_usd_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    if update.message.photo:
        context.user_data["sell_usd_photo"] = update.message.photo[-1].file_id
        await update.message.reply_text(tr(lang, "usd_ask_acc"))
        return SELL_USD_ACC
    await update.message.reply_text(tr(lang, "usd_need_photo"))
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
    lang = get_lang(user.id)
    await update.message.reply_text(
        tr(lang, "usd_req_ok"), reply_markup=get_main_keyboard(lang)
    )
    return ConversationHandler.END


# ============ উইথড্র ============
async def receive_withdraw_num(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    context.user_data["withdraw_info"] = update.message.text
    await update.message.reply_text(tr(lang, "wd_ask_amt"))
    return WITHDRAW_AMT


async def receive_withdraw_amt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    lang = get_lang(user.id)
    try:
        amt = float(update.message.text)
    except ValueError:
        await update.message.reply_text(tr(lang, "wd_bad_num"))
        return WITHDRAW_AMT

    min_amt = context.user_data.get("withdraw_min", settings["withdraw_min"])
    if amt < min_amt:
        await update.message.reply_text(tr(lang, "wd_min_again", min=min_amt))
        return WITHDRAW_AMT

    st = get_user_stats(user.id)
    if amt > st["balance"]:
        await update.message.reply_text(
            tr(lang, "wd_no_balance"), reply_markup=get_main_keyboard(lang)
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
        tr(lang, "wd_ok", amt=amt),
        reply_markup=get_main_keyboard(lang),
    )
    return ConversationHandler.END


# ============ কাজের স্ক্রিনশট জমা ============
async def ask_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    if not context.user_data.get("current_task_text"):
        await update.message.reply_text(
            tr(lang, "no_active_task"),
            reply_markup=get_main_keyboard(lang),
        )
        return ConversationHandler.END
    await update.message.reply_text(tr(lang, "ask_screenshot"))
    return PROOF_SUBMIT


async def check_referral_bonus(context, user_id):
    """শর্ত: নতুন ইউজারের নির্ধারিত সংখ্যক (ডিফল্ট ৪টি) জিমেইল অ্যাডমিন "সম্পূর্ণ" করলে তবেই রেফারকারী বোনাস পায়।
    রিজেক্ট করা জিমেইল গোনা হয় না।
    সংখ্যা /setrefgmail দিয়ে বদলানো যায়; বোনাসের পরিমাণ অ্যাডমিন সেটিং (referral_bonus) থেকে আসে।"""
    st = get_user_stats(user_id)
    need = int(settings.get("referral_gmail_required", 4))
    if st.get("ref_bonus_paid") or st.get("gmail_completed", 0) < need:
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
        context, rid, tr(get_lang(rid), "ref_bonus", amt=format_amount(bonus))
    )


_gmail_review_done = set()


async def gmail_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """অ্যাডমিনের কাছে আসা জিমেইল / ফেসবুক / ইনস্টাগ্রাম জমার নিচের ❌ রিজেক্ট / ✅ সম্পূর্ণ বাটনের কাজ।"""
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID:
        await query.answer()
        return
    try:
        code, uid_s = query.data.split(":")
        tid = int(uid_s)
        prefix = code[:2]                     # gm / fb / ig
        action = code[2:]                     # ok / x
        kind = {"gm": "gmail", "fb": "fb", "ig": "ig"}[prefix]
    except Exception:
        await query.answer()
        return

    key = (query.message.chat_id, query.message.message_id)
    if key in _gmail_review_done:
        await query.answer("এই জমাটি আগেই সম্পন্ন করা হয়েছে।", show_alert=True)
        return
    _gmail_review_done.add(key)

    st = get_user_stats(tid)
    if st["pending"] > 0:
        st["pending"] -= 1

    msg_key = {"gmail": "gmail", "fb": "fb", "ig": "ig"}[kind]
    if action == "ok":
        st["completed"] += 1
        # সম্পূর্ণ করা কাজ পরে /add দিলে আবার গোনা হবে না (সব ধরনের কাজের জন্য)
        st["gmail_credit"] = st.get("gmail_credit", 0) + 1
        if kind == "gmail":
            # শুধু জিমেইল রেফার বোনাসের হিসাবে গোনা হয়
            st["gmail_completed"] = st.get("gmail_completed", 0) + 1
        save_user_stats()
        await safe_send(context, tid, tr(get_lang(tid), msg_key + "_ok"))
        if kind == "gmail":
            await check_referral_bonus(context, tid)
        result = "✅ <b>সম্পূর্ণ করা হয়েছে</b> (ইউজারকে মেসেজ গেছে)"
        await query.answer("✅ সম্পূর্ণ করা হয়েছে")
    else:
        save_user_stats()
        await safe_send(context, tid, tr(get_lang(tid), msg_key + "_bad"))
        result = "❌ <b>রিজেক্ট করা হয়েছে</b> (ইউজারকে মেসেজ গেছে)"
        await query.answer("❌ রিজেক্ট করা হয়েছে")

    try:
        if query.message.caption is not None:
            await query.edit_message_caption(
                caption=(query.message.caption_html or "") + "\n\n" + result,
                parse_mode="HTML",
                reply_markup=None,
            )
        else:
            await query.edit_message_text(
                text=(query.message.text_html or "") + "\n\n" + result,
                parse_mode="HTML",
                reply_markup=None,
            )
    except Exception:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass


async def receive_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    lang = get_lang(user.id)

    if not update.message.photo:
        await update.message.reply_text(tr(lang, "need_photo"))
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

    text = (
        "📩 স্ক্রিনশট জমা হয়েছে\n"
        f"ইউজার: {esc(user.first_name)} (<code>{user.id}</code>)\n"
        f"কাজের বিবরণ:\n{esc(tdesc[:600])}"
    )
    review_kb = None
    if ttype == "gmail":
        review_kb = InlineKeyboardMarkup([[
            ibtn("❌ রিজেক্ট", callback_data=f"gmx:{user.id}", style=RED),
            ibtn("✅ সম্পূর্ণ", callback_data=f"gmok:{user.id}", style=GREEN),
        ]])
    await notify_admin(context, text, photo=photo, reply_markup=review_kb)
    clear_task_state(context)
    await update.message.reply_text(tr(lang, "shot_ok"), reply_markup=get_main_keyboard(lang))
    return ConversationHandler.END


# ============ ফেসবুক কাজ ============
BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


async def receive_fb_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    uid = (update.message.text or "").translate(BN_DIGITS).strip().replace(" ", "")
    if not re.fullmatch(r"[0-9]{14,16}", uid):
        await update.message.reply_text(tr(lang, "fb_bad_uid"))
        return FB_UID
    context.user_data["fb_uid"] = uid
    await update.message.reply_text(tr(lang, "fb_ask_cookies"))
    return FB_COOKIES


async def receive_fb_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    lang = get_lang(user.id)
    cookies = (update.message.text or "").strip()
    acc = context.user_data.get("fb_account")
    uid = context.user_data.get("fb_uid")
    if not acc or not uid:
        await update.message.reply_text(
            tr(lang, "fb_no_active"),
            reply_markup=get_main_keyboard(lang),
        )
        return ConversationHandler.END
    if len(cookies) < 20:
        await update.message.reply_text(tr(lang, "fb_bad_cookies"))
        return FB_COOKIES

    st = get_user_stats(user.id)
    st["pending"] += 1
    save_user_stats()

    username = f"@{user.username}" if user.username else "নেই"
    price = settings["fb_price"]
    send_as_file = len(cookies) > 2500
    report = (
        "📘 নতুন ফেসবুক কাজ জমা হয়েছে\n\n"
        f"👤 ইউজার: {esc(user.first_name)} ({esc(username)})\n"
        f"🆔 ইউজার ID: <code>{user.id}</code>\n\n"
        "📋 দেওয়া অ্যাকাউন্ট:\n"
        f"• First Name: <code>{esc(acc['first_name'])}</code>\n"
        f"• Last Name: <code>{esc(acc['last_name'])}</code>\n"
        f"• Password: <code>{esc(acc['password'])}</code>\n\n"
        f"📝 UID: <code>{esc(uid)}</code>\n"
    )
    if send_as_file:
        report += "🍪 Cookies: (বড় হওয়ায় নিচের ফাইলে দেওয়া হয়েছে)\n"
    else:
        report += f"🍪 Cookies:\n<code>{esc(cookies)}</code>\n"
    report += (
        f"\n✅ অনুমোদন: <code>/add {user.id} {price}</code>\n"
        f"❌ রিজেক্ট: <code>/rejectfb {user.id}</code>"
    )
    fb_review_kb = InlineKeyboardMarkup([[
        ibtn("❌ রিজেক্ট", callback_data=f"fbx:{user.id}", style=RED),
        ibtn("✅ সম্পূর্ণ", callback_data=f"fbok:{user.id}", style=GREEN),
    ]])
    await notify_admin(context, report, reply_markup=fb_review_kb)
    if send_as_file:
        try:
            await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=io.BytesIO(cookies.encode("utf-8")),
                filename=f"cookies_{uid}.txt",
            )
        except Exception as e:
            logging.error("Cookies file send failed: %s", e)

    context.user_data.pop("fb_account", None)
    context.user_data.pop("fb_uid", None)
    await update.message.reply_text(
        tr(lang, "fb_submitted"),
        reply_markup=get_main_keyboard(lang),
    )
    return ConversationHandler.END


# ============ ক্যান্সেল / রাউটিং ============
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    release_fb_account(context)
    lang = get_lang(update.effective_user.id)
    await update.message.reply_text(tr(lang, "cancelled"), reply_markup=get_main_keyboard(lang))
    return ConversationHandler.END


async def cancel_and_route(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """কনভারসেশনের মাঝে মেনু বাটন চাপলে সেটা চালু করে দেয়।"""
    release_fb_account(context)
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
        BotCommand("refund", "ইউজারকে টাকা ফেরত দিন (মেসেজসহ)"),
        BotCommand("cutnotice", "টাকা কাটলে ইউজারকে মেসেজ যাবে কি না (on/off)"),
        BotCommand("setrefgmail", "রেফার বোনাসের জন্য কয়টি জিমেইল লাগবে"),
        BotCommand("msg", "ইউজারকে মেসেজ (ভয়েস/ভিডিও পাঠিয়ে ক্যাপশনেও চলে)"),
        BotCommand("aimsg", "ইউজারকে \"Ai মেসেজ ⚡\" হেডিং সহ মেসেজ পাঠান"),
        BotCommand("block", "ইউজারকে ব্লক করুন"),
        BotCommand("unblock", "ইউজারকে আনব্লক করুন"),
        BotCommand("addtask", "জিমেইল কাজ যোগ করুন"),
        BotCommand("cleartasks", "সব জিমেইল কাজ মুছুন"),
        BotCommand("addfb", "ফেসবুক অ্যাকাউন্ট যোগ করুন (নাম | পদবি | পাসওয়ার্ড)"),
        BotCommand("fblist", "স্টকের ফেসবুক অ্যাকাউন্ট দেখুন"),
        BotCommand("clearfb", "স্টকের সব ফেসবুক অ্যাকাউন্ট মুছুন"),
        BotCommand("addigtask", "ইনস্টাগ্রাম কাজ যোগ করুন"),
        BotCommand("clearigtasks", "সব ইনস্টাগ্রাম কাজ মুছুন"),
        BotCommand("showprices", "বর্তমান সব দাম দেখুন"),
        BotCommand("setsupportuser", "সাপোর্ট ইউজারনেম বদলান"),
        BotCommand("setsupportmsg", "সাপোর্ট বাটনের পুরো বার্তা কাস্টম করুন"),
        BotCommand("resetsupportmsg", "সাপোর্ট বার্তা ডিফল্টে ফেরান"),
        BotCommand("setreferraltext", "রেফারেল বার্তায় অতিরিক্ত লেখা যোগ করুন"),
        BotCommand("clearreferraltext", "রেফারেলের অতিরিক্ত লেখা মুছুন"),
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


# ============ টেলিগ্রাম বিজনেস (Chat Automation / Secretary Mode) অটো-রিপ্লাই ============
# আপনার নিজের টেলিগ্রাম অ্যাকাউন্টের প্রাইভেট চ্যাটে কেউ মেসেজ দিলে বট `business_message` হিসেবে
# সেটা পায় এবং `business_connection_id` দিয়ে আপনার হয়ে রিপ্লাই পাঠায়।
#   • বট/কাজ/জিমেইল/ফেসবুক/উইথড্র ইত্যাদি সম্পর্কিত মেসেজ  -> বটের নিয়ম অনুযায়ী সঠিক উত্তর
#   • অন্য যেকোনো মেসেজ (ভয়েস, ছবি, সাধারণ কথা)             -> ব্যস্ততার অটো-রিপ্লাই
# সাধারণ বট চ্যাট (ইউজার যখন সরাসরি বটে মেসেজ দেয়) আগের মতোই কাজ করে; এই অংশ শুধু বিজনেস আপডেটে চলে।
# দরকার: python-telegram-bot ২১.১ বা তার নতুন ভার্সন (Bot API 7.2+ এর বিজনেস সাপোর্ট)।
BUSINESS_BOT_USERNAME = os.getenv("BUSINESS_BOT_USERNAME", "@EasyIncome_MAX_bot").strip()
# একই চ্যাটে ব্যস্ততার অটো-রিপ্লাই কতক্ষণ পর পর যাবে (সেকেন্ড)। ঘন ঘন মেসেজ দিলেও স্প্যাম হবে না।
BIZ_GENERIC_COOLDOWN = int(os.getenv("BIZ_GENERIC_COOLDOWN") or 600)
# একই চ্যাটে একই বিষয়ের উত্তর কতক্ষণ পর পর যাবে (সেকেন্ড)
BIZ_TOPIC_COOLDOWN = int(os.getenv("BIZ_TOPIC_COOLDOWN") or 60)
# আপনি নিজে ওই চ্যাটে রিপ্লাই দিলে এত সেকেন্ড বট চুপ থাকবে (আপনি নিজে কথা বলছেন বলে)
BIZ_OWNER_ACTIVE_SECONDS = int(os.getenv("BIZ_OWNER_ACTIVE_SECONDS") or 900)

BUSY_AUTO_REPLY = (
    "আসসালামু আলাইকুম! 👋✨ আপনার মেসেজটির জন্য অসংখ্য ধন্যবাদ। 🙏 "
    "আমি এই মুহূর্তে একটু ব্যস্ত থাকায় আমার অটো-রিপ্লাই সিস্টেম থেকে এই উত্তরটি পাঠানো হয়েছে। 🤖💬 "
    "ফ্রি হয়েই আপনার মেসেজটি দেখে সরাসরি রিপ্লাই দেব। 😊⏳"
)

_biz_owner_cache = {}      # business_connection_id -> মালিকের user id
_biz_owner_last = {}       # chat_id -> আপনি সর্বশেষ কখন ওই চ্যাটে মেসেজ দিয়েছেন
_biz_last_reply = {}       # (chat_id, topic) -> সর্বশেষ অটো-রিপ্লাইয়ের সময়
_biz_seen = {}             # (chat_id, message_id) -> একই মেসেজে দুবার রিপ্লাই ঠেকাতে


def _kw_regex(words):
    """ইংরেজি শব্দে সীমানা মিলিয়ে, বাংলা শব্দে সরাসরি খুঁজে এক রেজেক্স বানায়।"""
    parts = []
    for w in words:
        w = w.lower()
        if w.isascii():
            left = r"(?<![a-z0-9])"
            right = r"(?![a-z0-9])" if len(w) <= 3 else ""
            parts.append(left + re.escape(w) + right)
        else:
            parts.append(re.escape(w))
    return re.compile("|".join(parts))


def _biz_rate_lines():
    return (
        f"• জিমেইল: {format_amount(settings['gmail_price'])}৳\n"
        f"• ফেসবুক: {format_amount(settings['fb_price'])}৳\n"
        f"• ইনস্টাগ্রাম: {format_amount(settings['ig_price'])}৳\n"
        f"• পুরাতন জিমেইল বিক্রি: {format_amount(settings['old_gmail_price'])}৳"
    )


def _biz_footer():
    return (
        f"\n\n🤖 বট: {BUSINESS_BOT_USERNAME}\n"
        f"📢 চ্যানেল: {FORCE_URL}"
    )


def _biz_reply_bot_info():
    return (
        "🤖 আমাদের বট সম্পর্কে জানুন:\n\n"
        "এই বটে জিমেইল, ফেসবুক ও ইনস্টাগ্রাম অ্যাকাউন্টের কাজ করে ইনকাম করা যায়।\n\n"
        "📌 কাজ শুরুর নিয়ম:\n"
        f"1️⃣ আগে আমাদের চ্যানেলে জয়েন করুন: {FORCE_URL}\n"
        f"2️⃣ {BUSINESS_BOT_USERNAME} এ গিয়ে /start দিন এবং ভাষা বেছে নিন\n"
        "3️⃣ \"📝 কাজ ▸\" বাটন থেকে জিমেইল / ফেসবুক / ইনস্টাগ্রাম কাজ বেছে নিন\n"
        "4️⃣ বট যে নির্দেশনা দেবে হুবহু সেভাবে কাজ করে জমা দিন\n"
        "5️⃣ এডমিন যাচাই করে ঠিক থাকলে আপনার ব্যালেন্সে টাকা যোগ করে দেবেন\n"
        "6️⃣ \"💰 টাকা উত্তোলন\" থেকে বিকাশ / নগদ / মোবাইল রিচার্জ / Binance এ টাকা তুলুন\n\n"
        f"💸 বর্তমান রেট:\n{_biz_rate_lines()}\n\n"
        f"💳 সর্বনিম্ন উইথড্র: বিকাশ/নগদ/রিচার্জ {format_amount(settings['withdraw_min'])}৳, "
        f"Binance {format_amount(settings['withdraw_min_binance'])}৳\n\n"
        "⚠️ ভুল বা অসম্পূর্ণ কাজ দিলে এডমিন কাজটি বাতিল (রিজেক্ট) করতে পারেন, তাই নির্দেশনা ভালো করে পড়ে কাজ করুন।"
        + _biz_footer()
    )


def _biz_reply_howto():
    return (
        "✅ কাজ করার নিয়ম খুব সহজ:\n\n"
        f"1️⃣ প্রথমে চ্যানেলে জয়েন করুন: {FORCE_URL}\n"
        f"2️⃣ {BUSINESS_BOT_USERNAME} এ /start দিয়ে ভাষা বেছে নিন\n"
        "3️⃣ \"📝 কাজ ▸\" চাপুন এবং যে কাজটি করতে চান (জিমেইল / ফেসবুক / ইনস্টাগ্রাম) সেটি বেছে নিন\n"
        "4️⃣ কাজের দাম লেখা বাটনে চাপলে কাজের নির্দেশনা বা অ্যাকাউন্টের তথ্য আসবে\n"
        "5️⃣ নির্দেশনা মেনে কাজ শেষ করে জমা দিন (স্ক্রিনশট / UID / Cookies, কাজ অনুযায়ী)\n"
        "6️⃣ অনুমোদন হলে ব্যালেন্সে টাকা যোগ হবে, তারপর উইথড্র করতে পারবেন\n\n"
        f"💸 বর্তমান রেট:\n{_biz_rate_lines()}\n\n"
        "ধাপে ধাপে বুঝতে না পারলে বটের \"🆕 আমি নতুন\" বাটনে পেমেন্ট প্রুফ চ্যানেল দেখে নিন।"
        + _biz_footer()
    )


def _biz_reply_gmail():
    return (
        "📧 জিমেইল কাজের নিয়ম:\n\n"
        f"• {BUSINESS_BOT_USERNAME} এ \"📝 কাজ ▸\" → \"📧 জিমেইল কাজ\" চাপুন\n"
        f"• দাম লেখা বাটনে ({format_amount(settings['gmail_price'])}৳) চাপলে কাজের নির্দেশনা আসবে\n"
        "• নির্দেশনায় যা যা দেওয়া আছে হুবহু সেভাবে জিমেইল অ্যাকাউন্ট খুলুন বা কাজটি করুন\n"
        "• কাজ শেষে \"📥 জমা দিন\" চেপে স্ক্রিনশট পাঠান\n"
        f"• এডমিন অনুমোদন করলে {format_amount(settings['gmail_price'])}৳ আপনার ব্যালেন্সে যোগ হবে\n"
        "• \"জিমেইল কাজ নেই\" দেখালে কিছুক্ষণ পর আবার চেষ্টা করুন, নতুন কাজ যোগ হলে পাবেন\n\n"
        "🔎 কোনো সমস্যা হলে নির্দেশনাটি আরেকবার মিলিয়ে দেখুন। তবুও না হলে ঠিক কোথায় আটকাচ্ছে "
        "(স্ক্রিনশটসহ) লিখে পাঠান, ফ্রি হয়ে আমি সরাসরি দেখব।"
        + _biz_footer()
    )


def _biz_reply_old_gmail():
    return (
        "📮 পুরাতন জিমেইল বিক্রির নিয়ম:\n\n"
        f"• পুরাতন জিমেইলের বর্তমান দাম: {format_amount(settings['old_gmail_price'])}৳ (প্রতিটি)\n"
        f"• বটে \"📝 কাজ ▸\" → \"📮 পুরাতন জিমেইল বিক্রি\" চাপুন\n"
        f"• জিমেইল বিক্রি করতে @{settings['support_username']} এই আইডিতে মেসেজ দিতে হবে\n\n"
        "ফ্রি হয়ে আমিও আপনার মেসেজ দেখে রিপ্লাই দেব।"
        + _biz_footer()
    )


def _biz_reply_facebook():
    return (
        "📘 ফেসবুক কাজের নিয়ম:\n\n"
        f"• {BUSINESS_BOT_USERNAME} এ \"📝 কাজ ▸\" → \"📘 ফেসবুক কাজ\" চাপুন\n"
        f"• দাম লেখা বাটনে ({format_amount(settings['fb_price'])}৳) চাপলে বট আপনাকে একটি ফেসবুক অ্যাকাউন্টের "
        "তথ্য (First Name, Last Name, Password) দেবে\n"
        "• সেই তথ্য দিয়ে কাজটি সম্পন্ন করুন\n"
        "• তারপর বটের \"UID দিন\" বাটনে চেপে ১৪ থেকে ১৬ সংখ্যার UID দিন, এরপর Cookies দিন\n"
        "• এডমিন রিভিউ করে সব ঠিক থাকলে ১২-৭২ ঘণ্টার মধ্যে ব্যালেন্সে "
        f"{format_amount(settings['fb_price'])}৳ যোগ করে দেবেন\n"
        "• \"ফেসবুক কাজ নেই\" দেখালে স্টক শেষ, কিছুক্ষণ পর আবার চেষ্টা করুন\n\n"
        "🔎 সমস্যা হলে ঠিক কোথায় আটকাচ্ছে লিখে পাঠান, ফ্রি হয়ে আমি সরাসরি দেখব।"
        + _biz_footer()
    )


def _biz_reply_instagram():
    return (
        "📷 ইনস্টাগ্রাম কাজের নিয়ম:\n\n"
        f"• {BUSINESS_BOT_USERNAME} এ \"📝 কাজ ▸\" → \"📷 ইনস্টাগ্রাম কাজ\" চাপুন\n"
        f"• দাম লেখা বাটনে ({format_amount(settings['ig_price'])}৳) চাপলে কাজের নির্দেশনা আসবে\n"
        "• নির্দেশনা মেনে কাজ শেষ করে \"✅ Done\" বাটনে চাপুন\n"
        "• কাজ ঠিক থাকলে ২৪ ঘণ্টার ভিতরে ব্যালেন্সে টাকা যোগ হবে\n"
        "• \"ইনস্টাগ্রাম কাজ নেই\" দেখালে কিছুক্ষণ পর আবার চেষ্টা করুন\n\n"
        "🔎 সমস্যা হলে ঠিক কোথায় আটকাচ্ছে লিখে পাঠান, ফ্রি হয়ে আমি সরাসরি দেখব।"
        + _biz_footer()
    )


def _biz_reply_withdraw():
    return (
        "💰 টাকা উত্তোলনের (উইথড্র) নিয়ম:\n\n"
        f"• বটে \"💰 টাকা উত্তোলন\" চাপুন এবং বিকাশ / নগদ / মোবাইল রিচার্জ / Binance বেছে নিন\n"
        f"• বিকাশ, নগদ ও রিচার্জে সর্বনিম্ন {format_amount(settings['withdraw_min'])}৳ লাগবে\n"
        f"• Binance (USDT BEP-20) এ সর্বনিম্ন {format_amount(settings['withdraw_min_binance'])}৳ লাগবে\n"
        "• নাম্বার (বা অ্যাড্রেস) দিন, তারপর কত টাকা তুলবেন লিখুন\n"
        "• রিকোয়েস্ট জমা হলে এডমিন যাচাই করে পেমেন্ট পাঠিয়ে দেবেন\n\n"
        "💵 বর্তমান ব্যালেন্স দেখতে বটে \"💵 ব্যালেন্স\" বাটনে চাপুন। কাজ অনুমোদনের পরই ব্যালেন্স যোগ হয়।\n\n"
        "⏳ পেমেন্ট পেতে দেরি হলে আপনার নাম্বার ও কত টাকা তুলেছেন লিখে পাঠান, ফ্রি হয়ে আমি দেখব।"
        + _biz_footer()
    )


def _biz_reply_dollar():
    return (
        "🪙 ডলার বিক্রির নিয়ম:\n\n"
        f"• বর্তমান রেট: ১ ডলার = {format_amount(settings['usd_rate'])} টাকা\n"
        f"• বটে \"📝 কাজ ▸\" → \"🪙 ডলার বিক্রি করুন\" চাপুন\n"
        "• কত ডলার বিক্রি করবেন লিখুন, বট কোথায় ডলার পাঠাতে হবে তা দেখাবে\n"
        "• ডলার পাঠিয়ে পেমেন্টের স্ক্রিনশট দিন এবং আপনার বিকাশ/নগদ নাম্বার দিন\n"
        "• এডমিন যাচাই করে আপনাকে টাকা পাঠিয়ে দেবেন"
        + _biz_footer()
    )


def _biz_reply_referral():
    return (
        "🎁 রেফারেল প্রোগ্রাম:\n\n"
        f"• {BUSINESS_BOT_USERNAME} এ \"🎁 My Referrals\" বাটনে চাপলে আপনার নিজস্ব রেফার লিংক পাবেন\n"
        "• বন্ধুদের সেই লিংকে জয়েন করান\n"
        f"• প্রতি সফল রেফারে পাবেন {format_amount(settings['referral_bonus'])}৳ বোনাস\n"
        "• শর্ত পূরণ হলে বোনাস আপনার ব্যালেন্সে যোগ হয়"
        + _biz_footer()
    )


def _biz_reply_rates():
    return (
        "💸 বর্তমান রেট:\n\n"
        f"{_biz_rate_lines()}\n"
        f"• ডলার বিক্রি: ১ ডলার = {format_amount(settings['usd_rate'])}৳\n\n"
        f"💳 সর্বনিম্ন উইথড্র: বিকাশ/নগদ/রিচার্জ {format_amount(settings['withdraw_min'])}৳, "
        f"Binance {format_amount(settings['withdraw_min_binance'])}৳"
        + _biz_footer()
    )


def _biz_reply_channel():
    return (
        "📢 বট ব্যবহার করতে আমাদের চ্যানেলে জয়েন থাকা বাধ্যতামূলক।\n\n"
        f"👉 {FORCE_URL}\n\n"
        f"জয়েন করে {BUSINESS_BOT_USERNAME} এ গিয়ে \"✅ জয়েন করেছি\" বাটনে চাপুন। "
        "চ্যানেলে পেমেন্ট প্রুফও দেখতে পাবেন।"
    )


# (বিষয়, রেজেক্স, উত্তর বানানোর ফাংশন) — ওপরের সারি আগে মেলানো হয়।
# কোনো নতুন প্রশ্নের ধরন যোগ করতে চাইলে এখানে শব্দ বাড়ালেই হবে।
BIZ_TOPICS = [
    (
        "old_gmail",
        _kw_regex([
            "পুরাতন জিমেইল", "পুরনো জিমেইল", "পুরাতন জিমেল", "পুরনো জিমেল", "পুরাতন gmail",
            "পুরনো gmail", "old gmail", "old mail", "পুরাতন মেইল", "পুরনো মেইল",
        ]),
        _biz_reply_old_gmail,
    ),
    (
        "dollar",
        _kw_regex([
            "ডলার", "dollar", "usdt", "usd", "ডলার বিক্রি", "ডলার সেল", "dollar sell",
        ]),
        _biz_reply_dollar,
    ),
    (
        "withdraw",
        _kw_regex([
            "withdraw", "withdrawal", "উইথড্র", "উইড্র", "উত্তোলন", "টাকা তুল", "টাকা তোল",
            "টাকা পাই", "টাকা আসে", "টাকা আসেনি", "টাকা দিচ্ছ", "টাকা দেন", "টাকা দিবেন",
            "পেমেন্ট", "payment", "বিকাশ", "bkash", "bikash", "নগদ", "nagad", "রিচার্জ",
            "recharge", "binance", "বাইনান্স", "ব্যালেন্স", "balance", "বেলেন্স", "টাকা কবে",
            "টাকা পাইনি", "টাকা পাই নাই", "টাকা পাব", "টাকা পাবো",
        ]),
        _biz_reply_withdraw,
    ),
    (
        "facebook",
        _kw_regex([
            "facebook", "ফেসবুক", "ফেইসবুক", "ফেসবুকে", "fb", "face book", "ফেজবুক", "uid", "ইউআইডি",
            "cookies", "cookie", "কুকিজ", "কুকি",
        ]),
        _biz_reply_facebook,
    ),
    (
        "instagram",
        _kw_regex([
            "instagram", "ইনস্টাগ্রাম", "ইন্সটাগ্রাম", "ইনস্টা", "ইন্সটা", "insta", "ig",
        ]),
        _biz_reply_instagram,
    ),
    (
        "gmail",
        _kw_regex([
            "gmail", "জিমেইল", "জিমেল", "জি-মেইল", "জিমেইলে", "g-mail", "gmial", "gmale", "মেইল", "mail",
            "জিমেইল একাউন্ট", "জিমেইল অ্যাকাউন্ট",
        ]),
        _biz_reply_gmail,
    ),
    (
        "referral",
        _kw_regex([
            "রেফার", "রেফারেল", "refer", "referral", "referrals", "invite", "ইনভাইট",
        ]),
        _biz_reply_referral,
    ),
    (
        "rates",
        _kw_regex([
            "দাম কত", "রেট কত", "কত টাকা করে", "কত করে", "price", "rate", "রেট", "দাম",
        ]),
        _biz_reply_rates,
    ),
    (
        "channel",
        _kw_regex([
            "চ্যানেল", "channel", "জয়েন", "join", "প্রুফ", "proof",
        ]),
        _biz_reply_channel,
    ),
]

# "বট বন্ধ কেন", "আমার বটে কি হয়েছে" ধরনের মেসেজ নিয়মের উত্তর নয়, তাই এগুলো সাধারণ অটো-রিপ্লাইতে যাবে
BIZ_STATUS_RE = _kw_regex([
    "বন্ধ", "কি হয়েছে", "কী হয়েছে", "কি হইছে", "কী হইছে", "কাজ করছে না", "কাজ করতেছে না",
    "কাজ করে না", "চলছে না", "চলতেছে না", "not working", "stopped", "off", "down",
])

# কাজ শুরু/নিয়ম/বট সম্পর্কিত সাধারণ প্রশ্ন
BIZ_HOWTO_RE = _kw_regex([
    "কিভাবে কাজ", "কীভাবে কাজ", "কিভাবে কাজ", "কাজ করব", "কাজ করবো", "কাজ করি", "কাজ করা", "কাজ শুরু",
    "কাজ কি", "কাজ কী", "কি কাজ", "কী কাজ", "কাজের নিয়ম", "নিয়ম", "নিয়মকানুন", "নিয়ম-কানুন",
    "কিভাবে ইনকাম", "কীভাবে ইনকাম", "ইনকাম", "income", "কিভাবে শুরু", "কীভাবে শুরু", "কিভাবে জয়েন",
    "একাউন্ট খুল", "অ্যাকাউন্ট খুল", "আইডি খুল", "একাউন্ট খোল", "অ্যাকাউন্ট খোল", "আইডি খোল",
    "খুলব", "খুলবো", "খুলতে", "কিভাবে খুল", "open account", "create account", "how to work",
    "how it works", "how to start", "how to earn", "kivabe kaj", "kibhabe kaj", "kivabe", "kibhabe",
    "kemne kaj", "rules", "rule", "টাস্ক", "task", "বট", "bot", "কাজ আছে", "কাজ দেন", "কাজ দিন",
    "কাজ পাব", "কাজ পাবো",
])


def _biz_classify(text):
    """মেসেজের লেখা দেখে বিষয় ঠিক করে। বটের নিয়ম সম্পর্কিত না হলে None ফেরত দেয়।"""
    t = (text or "").lower().strip()
    if not t:
        return None
    for topic, rx, _fn in BIZ_TOPICS:
        if rx.search(t):
            return topic
    if BIZ_STATUS_RE.search(t):
        return None
    if BIZ_HOWTO_RE.search(t):
        return "howto"
    return None


def _biz_build_reply(topic):
    if topic == "howto":
        return _biz_reply_howto()
    if topic == "botinfo":
        return _biz_reply_bot_info()
    for name, _rx, fn in BIZ_TOPICS:
        if name == topic:
            return fn()
    return None


# বট নিজের সম্পর্কে জানতে চাইলে (যেমন "এটা কিসের বট?", "বট কী?") পূর্ণ তথ্য
BIZ_BOTINFO_RE = _kw_regex([
    "কিসের বট", "কিসের বোট", "কি বট", "কী বট", "বট কি", "বট কী", "বট সম্পর্কে", "বট সম্পর্কিত",
    "বটের তথ্য", "বটের নিয়ম", "বট নিয়ম", "what is this bot", "about bot", "about this bot",
    "bot info", "bot rules", "বট কিভাবে", "বট কীভাবে", "বট কেমন",
])


async def _biz_send(bot, chat_id, text, connection_id):
    """business_connection_id দিয়ে মেসেজ পাঠায় (পুরনো PTB তে api_kwargs দিয়ে চেষ্টা করে)।"""
    try:
        await bot.send_message(
            chat_id=chat_id, text=text, business_connection_id=connection_id
        )
    except TypeError:
        await bot.send_message(
            chat_id=chat_id, text=text, api_kwargs={"business_connection_id": connection_id}
        )


async def _biz_owner_id(bot, connection_id):
    """এই কানেকশনের মালিক (অর্থাৎ আপনি) কে — যাতে আপনার নিজের মেসেজে বট রিপ্লাই না দেয়।"""
    if connection_id in _biz_owner_cache:
        return _biz_owner_cache[connection_id]
    owner = None
    try:
        conn = await bot.get_business_connection(connection_id)
        owner = conn.user.id
        _biz_owner_cache[connection_id] = owner
    except Exception as e:
        logging.warning("Business connection lookup failed: %s", e)
    return owner


def _biz_cleanup(now):
    if len(_biz_seen) > 2000:
        for k in [k for k, ts in _biz_seen.items() if now - ts > 3600]:
            _biz_seen.pop(k, None)
    if len(_biz_last_reply) > 2000:
        for k in [k for k, ts in _biz_last_reply.items() if now - ts > 86400]:
            _biz_last_reply.pop(k, None)


async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.business_message
    conn_id = getattr(msg, "business_connection_id", None)
    sender = msg.from_user
    chat_id = msg.chat.id
    now = time.time()

    if not conn_id or not sender:
        return

    # ১) আপনার নিজের পাঠানো মেসেজ: রিপ্লাই নয়, শুধু মনে রাখি যে আপনি এখন কথা বলছেন
    owner_id = await _biz_owner_id(context.bot, conn_id)
    if sender.id == ADMIN_ID or (owner_id is not None and sender.id == owner_id):
        _biz_owner_last[chat_id] = now
        logging.info("Business: মালিকের নিজের মেসেজ, রিপ্লাই দিইনি (sender=%s)", sender.id)
        return

    # ২) অন্য বটকে বা ব্লক করা ইউজারকে রিপ্লাই নয়
    if sender.is_bot or sender.id in blocked_users:
        return

    # ৩) আপনি নিজে সম্প্রতি রিপ্লাই দিয়ে থাকলে বট চুপ থাকে
    if now - _biz_owner_last.get(chat_id, 0) < BIZ_OWNER_ACTIVE_SECONDS:
        logging.info("Business: আপনি সম্প্রতি রিপ্লাই দিয়েছেন, বট চুপ")
        return

    # ৪) একই মেসেজে দুবার রিপ্লাই ঠেকানো
    key = (chat_id, msg.message_id)
    if key in _biz_seen:
        return
    _biz_seen[key] = now
    _biz_cleanup(now)

    text = msg.text or msg.caption or ""

    # ৫) বিষয় ঠিক করে উত্তর বানানো
    topic = None
    if text and BIZ_BOTINFO_RE.search(text.lower()):
        topic = "botinfo"
    elif text:
        topic = _biz_classify(text)

    if topic:
        cooldown_key = (chat_id, topic)
        if now - _biz_last_reply.get(cooldown_key, 0) < BIZ_TOPIC_COOLDOWN:
            logging.info("Business: topic cooldown চলছে")
            return
        reply = _biz_build_reply(topic)
    else:
        # বটের নিয়ম সম্পর্কিত নয় (ভয়েস, ছবি, সাধারণ কথা ইত্যাদি) -> ব্যস্ততার অটো-রিপ্লাই
        topic = "generic"
        cooldown_key = (chat_id, topic)
        if now - _biz_last_reply.get(cooldown_key, 0) < BIZ_GENERIC_COOLDOWN:
            logging.info("Business: ব্যস্ততার মেসেজ cooldown চলছে")
            return
        reply = BUSY_AUTO_REPLY

    if not reply:
        return
    _biz_last_reply[cooldown_key] = now
    try:
        await _biz_send(context.bot, chat_id, reply, conn_id)
    except Exception as e:
        _biz_last_reply.pop(cooldown_key, None)
        logging.error("Business reply failed (chat %s): %s", chat_id, e)


async def business_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """সবার আগে (group=-5) চলে। শুধু বিজনেস আপডেট ধরে নিজে সামলায় এবং বাকি হ্যান্ডলার থামিয়ে দেয়,
    তাই সাধারণ বটের চ্যানেল-জয়েন/ব্লক/মেনু ইত্যাদি কিছুতেই বিজনেস মেসেজ ঢোকে না।
    সাধারণ আপডেটে কিছুই করে না, তাই আগের সব ফিচার আগের মতোই চলে।"""
    bc = getattr(update, "business_connection", None)
    if bc is not None:
        try:
            _biz_owner_cache[bc.id] = bc.user.id
            logging.info(
                "Business connection %s: user=%s enabled=%s",
                bc.id, bc.user.id, getattr(bc, "is_enabled", None),
            )
        except Exception as e:
            logging.warning("Business connection update error: %s", e)
        raise ApplicationHandlerStop

    if getattr(update, "business_message", None) is not None:
        _bm = update.business_message
        logging.info(
            "Business message পেয়েছি: chat=%s from=%s conn=%s",
            _bm.chat.id, getattr(_bm.from_user, "id", None), getattr(_bm, "business_connection_id", None),
        )
        try:
            await handle_business_message(update, context)
        except ApplicationHandlerStop:
            raise
        except Exception as e:
            logging.error("Business message error: %s", e)
        raise ApplicationHandlerStop

    # এডিট/ডিলিট হওয়া বিজনেস মেসেজে রিপ্লাই দরকার নেই
    if (
        getattr(update, "edited_business_message", None) is not None
        or getattr(update, "deleted_business_messages", None) is not None
    ):
        raise ApplicationHandlerStop


# ============ মেইন ============
def main():
    start_keepalive()
    import telegram

    print("MOMIN_NEW v2 চালু হচ্ছে | python-telegram-bot", telegram.__version__)
    app = ApplicationBuilder().token(TOKEN).post_init(setup_menu_commands).build()

    # টেলিগ্রাম বিজনেস (Chat Automation) আপডেট সবার আগে ধরে অটো-রিপ্লাই দেয়; সাধারণ আপডেটে কিছু করে না
    app.add_handler(TypeHandler(Update, business_router), group=-5)

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
                pattern=r"^(withdraw_|sell_usd_start$|sell_usd_done$|fb_uid$|fb_cancel$|setprice_)",
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
            FB_UID: [MessageHandler(TEXT_INPUT, receive_fb_uid)],
            FB_COOKIES: [MessageHandler(TEXT_INPUT, receive_fb_cookies)],
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
    app.add_handler(CommandHandler("addfb", add_fb_account))
    app.add_handler(CommandHandler("fblist", list_fb_accounts))
    app.add_handler(CommandHandler("clearfb", clear_fb_accounts))
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
    app.add_handler(CommandHandler("rejectg", reject_gmail))
    app.add_handler(CommandHandler("rejectfb", reject_facebook))
    app.add_handler(CommandHandler("rejectig", reject_instagram))
    app.add_handler(CommandHandler("add", add_balance))
    app.add_handler(CommandHandler("cut", cut_balance))
    app.add_handler(CommandHandler("refund", refund_balance))
    app.add_handler(CommandHandler("cutnotice", cut_notice_setting))
    app.add_handler(CommandHandler("setrefgmail", set_ref_gmail))
    app.add_handler(CommandHandler("users", total_users))
    app.add_handler(CommandHandler("info", user_info))
    app.add_handler(CommandHandler("block", block_user_cmd))
    app.add_handler(CommandHandler("unblock", unblock_user_cmd))
    app.add_handler(CommandHandler("msg", admin_send_message))
    app.add_handler(CommandHandler("aimsg", admin_ai_message))
    app.add_handler(
        MessageHandler(filters.PHOTO & filters.User(user_id=ADMIN_ID), handle_admin_photo)
    )
    app.add_handler(CallbackQueryHandler(gmail_review, pattern=r"^(gmok|gmx|fbok|fbx|igok|igx):[0-9]+$"))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    print("Bot is running successfully with all features...")
    # business_message / business_connection আপডেট পেতে সব ধরনের আপডেট চালু রাখা হলো
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
