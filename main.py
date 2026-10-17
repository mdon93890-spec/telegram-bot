import os
import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    ContextTypes, filters, ConversationHandler, CallbackQueryHandler
)

# Logging Setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Config
BOT_TOKEN = "8695302843:AAHwLFj5lP7TNy8NIC-G4a2sxl-LGvKkG2Y"
ADMIN_ID = "8361020821"

# Conversation States
GMAIL_PROOF, FB_DATA, WITHDRAW_METHOD, WITHDRAW_ACC, WITHDRAW_AMT = range(5)

# User Database (In-Memory)
users_db = {}

def get_user(user_id):
    if user_id not in users_db:
        users_db[user_id] = {"balance": 0, "completed": 0, "pending": 0}
    return users_db[user_id]

# Main Keyboard Menu (8 Options)
def main_keyboard():
    keyboard = [
        [KeyboardButton("📧 জিমেইল কাজ"), KeyboardButton("📘 ফেসবুক কাজ")],
        [KeyboardButton("📸 ইনস্টাগ্রাম কাজ"), KeyboardButton("💰 আমার ব্যালেন্স")],
        [KeyboardButton("💸 টাকা তুলুন"), KeyboardButton("💬 সাপোর্ট")],
        [KeyboardButton("🌐 প্রক্সি কিনুন"), KeyboardButton("🎥 কিভাবে কাজ করবেন তার ভিডিও")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Start Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id)
    text = f"🥰 স্বাগতম, 💸⚡ {user.first_name} ⚡💸!\nআমাদের ইনকাম বটে আপনাকে স্বাগতম।"
    await update.message.reply_text(text, reply_markup=main_keyboard())

# Main Button Handlers
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    u_data = get_user(user_id)

    if text == "📧 জিমেইল কাজ":
        keyboard = [[InlineKeyboardButton("একটি জিমেইল একাউন্টের দাম ১৮ টাকা 💸", callback_data="gmail_price")]]
        await update.message.reply_text("নিচের বাটনে ক্লিক করুন:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif text == "📘 ফেসবুক কাজ":
        keyboard = [[InlineKeyboardButton("একটি ফেসবুক একাউন্টের দাম ৪ টাকা 💸", callback_data="fb_price")]]
        await update.message.reply_text("নিচের বাটনে ক্লিক করুন:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif text == "📸 ইনস্টাগ্রাম কাজ":
        await update.message.reply_text("এই মুহূর্তে ইনস্টাগ্রাম এর কাজ নেই")

    elif text == "💰 আমার ব্যালেন্স":
        msg = (
            f"📊 **আপনার অ্যাকাউন্ট ড্যাশবোর্ড:**\n\n"
            f"👤 ইউজার ID: `{user_id}`\n"
            f"✅ সম্পন্ন কাজ: {u_data['completed']} টি\n"
            f"⏳ পেন্ডিং কাজ: {u_data['pending']} টি\n"
            f"💵 বর্তমান ব্যালেন্স: {u_data['balance']} টাকা"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text == "💬 সাপোর্ট":
        await update.message.reply_text("যেকোনো সাহায্যের জন্য এডমিনের সাথে যোগাযোগ করুন: @mominboss009")

    elif text == "🌐 প্রক্সি কিনুন":
        keyboard = [[InlineKeyboardButton("এডমিনের সাথে যোগাযোগ করুন 📩", url="https://t.me/mominboss009")]]
        await update.message.reply_text("একটি প্রক্সির দাম ২০ টাকা।\nপ্রক্সি কিনতে এডমিনের সাথে যোগাযোগ করুন:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif text == "🎥 কিভাবে কাজ করবেন তার ভিডিও":
        keyboard = [[InlineKeyboardButton("গ্রুপে জয়েন হোন 🚀", url="https://t.me/mominboss005")]]
        await update.message.reply_text("কাজের ভিডিও দেখতে ও নতুন আপডেট পেতে নিচের গ্রুপে জয়েন হোন:", reply_markup=InlineKeyboardMarkup(keyboard))

# Callbacks for Gmail & FB Flow
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "gmail_price":
        keyboard = [[InlineKeyboardButton("জমা দিন 📥", callback_data="gmail_submit")]]
        instructions = (
            "📌 **জিমেইল খোলার নিয়ম:**\n"
            "১. একটি নতুন ফ্রেশ জিমেইল তৈরি করুন।\n"
            "২. রিকভারি মেইল যুক্ত করুন।\n"
            "৩. সঠিক নিয়মে কাজ শেষ করে নিচে 'জমা দিন' এ ক্লিক করুন।"
        )
        await query.message.reply_text(instructions, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "gmail_submit":
        await query.message.reply_text(
            "আপনি যে সঠিকভাবে কাজটি করেছেন তার প্রমাণ হিসেবে একটি স্ক্রিনশট জমা দিন\n\n🔥⚡MD.MOMIN⚡🔥"
        )
        return GMAIL_PROOF

    elif query.data == "fb_price":
        keyboard = [[InlineKeyboardButton("জমা দিন 📥", callback_data="fb_submit")]]
        instructions = (
            "📌 **ফেসবুক কাজ জমা দেওয়ার নিয়ম:**\n"
            "একটি সচল ফেসবুক অ্যাকাউন্ট তৈরি করে কাজ শেষ করুন এবং নিচে 'জমা দিন' বাটনে ক্লিক করুন।"
        )
        await query.message.reply_text(instructions, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "fb_submit":
        await query.message.reply_text("ফেসবুক একাউন্টের নাম্বার এবং পাসওয়ার্ড দিন (ফরম্যাট: Number | Password):")
        return FB_DATA

# Gmail Proof Handler
async def receive_gmail_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u_data = get_user(user.id)
    
    if update.message.photo:
        u_data['pending'] += 1
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"📥 **নতুন জিমেইল কাজ জমা এসেছে!**\n\n👤 ইউজার: @{user.username} (ID: `{user.id}`)"
        )
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=update.message.photo[-1].file_id)
        
        await update.message.reply_text("কাজটি সফলভাবে সাবমিট হয়েছে! এডমিন ভেরিফাই করে আপনার ব্যালেন্স যোগ করে দেবে।", reply_markup=main_keyboard())
        return ConversationHandler.END
    else:
        await update.message.reply_text("অনুগ্রহ করে একটি স্ক্রিনশট (ছবি) পাঠান।")
        return GMAIL_PROOF

# FB Data Handler
async def receive_fb_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u_data = get_user(user.id)
    text = update.message.text

    u_data['pending'] += 1
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📥 **নতুন ফেসবুক কাজ জমা এসেছে!**\n\n👤 ইউজার: @{user.username} (ID: `{user.id}`)\n📝 ডাটা: `{text}`",
        parse_mode="Markdown"
    )

    await update.message.reply_text("কাজটি সফলভাবে সাবমিট হয়েছে! এডমিন ভেরিফাই করে ব্যালেন্স যোগ করে দেবে।", reply_markup=main_keyboard())
    return ConversationHandler.END

# Withdraw System
async def start_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "💸 টাকা তুলুন":
        keyboard = [
            [InlineKeyboardButton("১. বিকাশ নাম্বার দেন 📱", callback_data="method_bkash")],
            [InlineKeyboardButton("২. নগদ নাম্বার দেন 📱", callback_data="method_nagad")]
        ]
        await update.message.reply_text("টাকা তোলার জন্য মাধ্যম সিলেক্ট করুন:", reply_markup=InlineKeyboardMarkup(keyboard))
        return WITHDRAW_METHOD

async def withdraw_method_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    method = "বিকাশ" if query.data == "method_bkash" else "নগদ"
    context.user_data['withdraw_method'] = method
    
    await query.message.reply_text(f"আপনার {method} নাম্বার লিখুন:")
    return WITHDRAW_ACC

async def receive_withdraw_acc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['withdraw_acc'] = update.message.text
    await update.message.reply_text("আপনি কত টাকা উইথড্র করবেন (পরিমাণ লিখুন):")
    return WITHDRAW_AMT

async def receive_withdraw_amt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u_data = get_user(user.id)
    
    try:
        amount = float(update.message.text)
    except ValueError:
        await update.message.reply_text("অনুগ্রহ করে সঠিক টাকার পরিমাণ সংখ্যায় লিখুন:")
        return WITHDRAW_AMT

    if amount <= 0:
        await update.message.reply_text("সঠিক পরিমাণ লিখুন।")
        return WITHDRAW_AMT

    if u_data['balance'] < amount:
        await update.message.reply_text(f"❌ আপনার পর্যাপ্ত ব্যালেন্স নেই! বর্তমান ব্যালেন্স: {u_data['balance']} টাকা।", reply_markup=main_keyboard())
        return ConversationHandler.END

    u_data['balance'] -= amount
    method = context.user_data.get('withdraw_method')
    acc = context.user_data.get('withdraw_acc')

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"🚨 **নতুন উইথড্র রিকোয়েস্ট!**\n\n"
            f"👤 ইউজার: @{user.username} (ID: `{user.id}`)\n"
            f"💳 মাধ্যম: {method}\n"
            f"📱 অ্যাকাউন্ট: `{acc}`\n"
            f"💵 পরিমাণ: {amount} টাকা"
        ),
        parse_mode="Markdown"
    )

    await update.message.reply_text("✅ আপনার উইথড্র রিকোয়েস্ট সফলভাবে জমা হয়েছে! শীঘ্রই এডমিন টাকা পাঠিয়ে দেবে।", reply_markup=main_keyboard())
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("প্রক্রিয়া বাতিল করা হয়েছে।", reply_markup=main_keyboard())
    return ConversationHandler.END

def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^💸 টাকা তুলুন$"), start_withdraw),
            CallbackQueryHandler(button_callback, pattern="^(gmail_submit|fb_submit)$")
        ],
        states={
            GMAIL_PROOF: [MessageHandler(filters.PHOTO, receive_gmail_proof)],
            FB_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_fb_data)],
            WITHDRAW_METHOD: [CallbackQueryHandler(withdraw_method_selected, pattern="^method_")],
            WITHDRAW_ACC: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_withdraw_acc)],
            WITHDRAW_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_withdraw_amt)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Clean polling
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
        
