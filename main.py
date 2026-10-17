import logging
import sqlite3
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Logging Configuration
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ---------------------------------------------------------
# BOT CONFIGURATION (আপনার তথ্যাদি)
# ---------------------------------------------------------
BOT_TOKEN = '8695302843:AAHwLFj5lP7TNy8NIC-G4a2sxl-LGvKkG2Y'
ADMIN_USERNAME = 'mominboss009'

# ---------------------------------------------------------
# DATABASE SETUP (SQLite)
# ---------------------------------------------------------
def init_db():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            balance REAL DEFAULT 0.0,
            submitted_gmails INTEGER DEFAULT 0,
            submitted_fb INTEGER DEFAULT 0,
            submitted_insta INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ---------------------------------------------------------
# KEYBOARD MENUS (বাংলা ইন্টারফেস)
# ---------------------------------------------------------
def get_main_keyboard():
    keyboard = [
        ['📧 জিমেইল কাজ', '📘 ফেসবুক কাজ'],
        ['📸 ইনস্টাগ্রাম কাজ', '💰 আমার ব্যালেন্স'],
        ['💸 টাকা তুলুন', '💬 সাপোর্ট']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ---------------------------------------------------------
# COMMAND HANDLERS
# ---------------------------------------------------------
# /start Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    # Save user to DB if not exists
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user.id,))
    if not cursor.fetchone():
        cursor.execute('INSERT INTO users (user_id, username, full_name, balance) VALUES (?, ?, ?, ?)',
                       (user.id, user.username, user.full_name, 0.0))
        conn.commit()
    conn.close()

    welcome_text = f"🥰 স্বাগতম, <b>{user.full_name}</b>!\n\n" \
                   f"আমাদের জিমেইল, ফেসবুক ও ইনস্টাগ্রাম ইনকাম বোটে আপনাকে স্বাগতম। ⚡️\n\n" \
                   f"🆔 আপনার টেলিগ্রাম ID: <code>{user.id}</code>\n\n" \
                   f"↗️ নিচে দেওয়া মেনু থেকে আপনার পছন্দমতো কাজের অপশনে ক্লিক করুন 🔻"
    await update.message.reply_html(welcome_text, reply_markup=get_main_keyboard())

# Handle Button Clicks
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT balance, submitted_gmails, submitted_fb, submitted_insta FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()

    balance = row[0] if row else 0.0
    gmails = row[1] if row else 0
    fb = row[2] if row else 0
    insta = row[3] if row else 0

    if text == '📧 জিমেইল কাজ':
        msg = "📧 <b>জিমেইল কাজ জমা দেওয়ার নিয়ম:</b>\n\n" \
              "সঠিক নিয়মে অ্যাকাউন্ট তৈরি করে নিচের ফরম্যাটে ডিটেইলস পাঠান:\n" \
              "<code>Email | Password | Recovery Email</code>\n\n" \
              "📸 সাথে কাজের স্ক্রিনশট অবশ্যই টেলিগ্রাম চ্যাটে পাঠিয়ে দিবেন।"
        await update.message.reply_html(msg)

    elif text == '📘 ফেসবুক কাজ':
        msg = "📘 <b>ফেসবুক কাজ জমা দেওয়ার নিয়ম:</b>\n\n" \
              "ফেসবুক আইডি জমা দিতে এই ফরম্যাট ব্যবহার করুন:\n" \
              "<code>Profile Link / Number | Password | 2FA Key (যদি থাকে)</code>"
        await update.message.reply_html(msg)

    elif text == '📸 ইনস্টাগ্রাম কাজ':
        msg = "📸 <b>ইনস্টাগ্রাম কাজ জমা দেওয়ার নিয়ম:</b>\n\n" \
              "ইনস্টাগ্রাম অ্যাকাউন্ট জমা দেওয়ার জন্য নিচের ফরম্যাটে লিখুন:\n" \
              "<code>Username | Password</code>"
        await update.message.reply_html(msg)

    elif text == '💰 আমার ব্যালেন্স':
        msg = f"📊 <b>আপনার অ্যাকাউন্ট ড্যাশবোর্ড:</b>\n\n" \
              f"👤 ইউজার ID: <code>{user_id}</code>\n" \
              f"📧 জমা দেওয়া জিমেইল: <b>{gmails} টি</b>\n" \
              f"📘 জমা দেওয়া ফেসবুক: <b>{fb} টি</b>\n" \
              f"📸 জমা দেওয়া ইনস্টাগ্রাম: <b>{insta} টি</b>\n" \
              f"─────────────────\n" \
              f"💵 বর্তমান মোট ব্যালেন্স: <b>{balance} টাকা</b>"
        await update.message.reply_html(msg)

    elif text == '💸 টাকা তুলুন':
        if balance < 50:
            await update.message.reply_text(f"❌ আপনার ব্যালেন্স অপর্যাপ্ত! সর্বনিম্ন ৫০ টাকা হলে উইথড্র দিতে পারবেন।\nআপনার বর্তমান ব্যালেন্স: {balance} টাকা।")
        else:
            await update.message.reply_text(f"✅ উইথড্র দেওয়ার জন্য আপনার বিকাশ বা নগদ নাম্বার লিখে এডমিনকে মেসেজ দিন: @{ADMIN_USERNAME}")

    elif text == '💬 সাপোর্ট':
        await update.message.reply_text(f"💬 যেকোনো সহায়তার জন্য এডমিনের সাথে যোগাযোগ করুন: @{ADMIN_USERNAME}")

# ---------------------------------------------------------
# ADMIN COMMAND: Add Balance & Task Count
# Format: /addbalance <USER_ID> <AMOUNT> <TASK_TYPE>
# Example: /addbalance 12345678 50 gmail
# ---------------------------------------------------------
async def add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ইউজার যদি এডমিন মেসেজ হ্যান্ডেল করতে চায়
    try:
        target_user_id = int(context.args[0])
        amount = float(context.args[1])
        task_type = context.args[2].lower() if len(context.args) > 2 else 'gmail'

        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        if task_type == 'gmail':
            cursor.execute('UPDATE users SET balance = balance + ?, submitted_gmails = submitted_gmails + 1 WHERE user_id = ?', (amount, target_user_id))
        elif task_type == 'fb':
            cursor.execute('UPDATE users SET balance = balance + ?, submitted_fb = submitted_fb + 1 WHERE user_id = ?', (amount, target_user_id))
        elif task_type == 'insta':
            cursor.execute('UPDATE users SET balance = balance + ?, submitted_insta = submitted_insta + 1 WHERE user_id = ?', (amount, target_user_id))

        conn.commit()
        conn.close()

        await update.message.reply_text(f"✅ সফলভাবে User ID: {target_user_id}-এর অ্যাকাউন্টে {amount} টাকা যোগ করা হয়েছে! (Task: {task_type})")
        
        # Notify User
        await context.bot.send_message(chat_id=target_user_id, text=f"🎉 আপনার অ্যাকাউন্টে {amount} টাকা যোগ করা হয়েছে! ধন্যবাদ।")
    except Exception as e:
        await update.message.reply_text("⚠️ সঠিক ফরম্যাট: `/addbalance <USER_ID> <AMOUNT> <TYPE>`\nযেমন: `/addbalance 12345678 50 gmail` (Types: gmail, fb, insta)")

# ---------------------------------------------------------
# MAIN BOT RUNNER
# ---------------------------------------------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('addbalance', add_balance))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
  
