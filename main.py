import os
import logging
from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
from datetime import datetime, timezone, timedelta
import asyncio

# --- Konfigurasi Logger ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Konfigurasi Google Sheets ---
# Dapatkan kredensial dari variabel lingkungan
try:
    GSPREAD_SERVICE_ACCOUNT_KEY = os.environ.get('GSPREAD_SERVICE_ACCOUNT_KEY')
    if not GSPREAD_SERVICE_ACCOUNT_KEY:
        raise ValueError("GSPREAD_SERVICE_ACCOUNT_KEY environment variable not set.")
    
    # Kredensial adalah JSON string, perlu di-parse
    creds_json = json.loads(GSPREAD_SERVICE_ACCOUNT_KEY)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json,
                                                             scopes=['https://spreadsheets.google.com/feeds',
                                                                     'https://www.googleapis.com/auth/drive'])
    client = gspread.authorize(creds)
    
    SPREADSHEET_ID = os.environ.get('GOOGLE_SHEET_ID')
    SHEET_TAB_NAME = os.environ.get('GOOGLE_SHEET_TAB_NAME', "Checkin") # Default ke "Checkin"

    if not SPREADSHEET_ID:
        raise ValueError("GOOGLE_SHEET_ID environment variable not set.")

    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    worksheet = spreadsheet.worksheet(SHEET_TAB_NAME)
    logger.info(f"Berhasil terhubung ke Google Sheet (ID: '{SPREADSHEET_ID}', Tab: '{SHEET_TAB_NAME}')")

except Exception as e:
    logger.error(f"ERROR: {e}")
    logger.error("Gagal menginisialisasi Google Sheets. Bot mungkin tidak dapat mencatat data.")
    worksheet = None # Pastikan worksheet adalah None jika gagal inisialisasi

# --- Muat Authorized Sales IDs ---
authorized_sales_ids_str = os.environ.get('AUTHORIZED_SALES', '')
if authorized_sales_ids_str:
    authorized_sales_ids = {int(uid.strip()) for uid in authorized_sales_ids_str.split(',') if uid.strip().isdigit()}
else:
    authorized_sales_ids = set() # Kosongkan jika tidak ada ID
logger.info(f"Memuat daftar authorized sales IDs...")
logger.info(f"Authorized sales IDs loaded: {authorized_sales_ids}")

# --- Inisialisasi Bot Telegram ---
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')

if not TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN belum diatur!")
    # Jika token tidak ada, bot tidak bisa jalan. Langsung keluar.
    exit(1)

if not WEBHOOK_URL:
    logger.error("WEBHOOK_URL belum diatur!")
    # Jika webhook URL tidak ada, bot tidak bisa menerima update. Langsung keluar.
    exit(1)

# Application bot harus diinisialisasi secara global
application = Application.builder().token(TOKEN).build()
logger.info("Menginisialisasi bot Telegram (webhook mode)...")

# Kita perlu memanggil initialize() agar handler bekerja, tetapi tidak di async_run global
# karena ini akan dipanggil otomatis oleh update processor atau secara manual saat set_webhook.
# Untuk memastikan handler siap, kita bisa panggil di sini
asyncio.run(application.initialize())
logger.info("Application Telegram handlers initialized.")

# --- Fungsi Handler Telegram ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id in authorized_sales_ids:
        await update.message.reply_text(f"Halo {update.effective_user.first_name}! 👋 Saya bot pencatat sales harian Anda.")
    else:
        await update.message.reply_text("Maaf, Anda tidak memiliki izin untuk menjalankan bot ini.")
        logger.warning(f"Unauthorized user {user_id} tried to use /start.")

async def checkin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id in authorized_sales_ids:
        # Menunggu balasan dari user
        await update.message.reply_text("Silakan kirimkan nama Anda dan jumlah sales hari ini (contoh: John Doe, 1000000).")
    else:
        await update.message.reply_text("Maaf, Anda tidak memiliki izin untuk melakukan check-in.")
        logger.warning(f"Unauthorized user {user_id} tried to use /checkin.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in authorized_sales_ids:
        await update.message.reply_text("Maaf, Anda tidak memiliki izin untuk menggunakan bot ini.")
        logger.warning(f"Unauthorized user {user_id} sent message: {update.message.text}")
        return

    text = update.message.text
    try:
        # Asumsi format: "Nama, Jumlah Sales"
        parts = text.split(',')
        if len(parts) != 2:
            await update.message.reply_text("Format tidak benar. Mohon gunakan format 'Nama Anda, Jumlah Sales' (contoh: John Doe, 1000000).")
            return

        sales_name = parts[0].strip()
        sales_amount_str = parts[1].strip()

        try:
            sales_amount = int(sales_amount_str)
        except ValueError:
            await update.message.reply_text("Jumlah sales harus berupa angka. Mohon coba lagi.")
            return

        # Dapatkan waktu saat ini dalam WIB (UTC+7)
        wib_tz = timezone(timedelta(hours=7))
        timestamp = datetime.now(wib_tz).strftime("%Y-%m-%d %H:%M:%S WIB")

        # Catat ke Google Sheets
        if worksheet:
            row_data = [timestamp, sales_name, sales_amount]
            worksheet.append_row(row_data)
            await update.message.reply_text(f"Terima kasih, {sales_name}! Sales {sales_amount:,} telah dicatat.")
            logger.info(f"Sales data recorded: {row_data}")
        else:
            await update.message.reply_text("Maaf, gagal terhubung ke Google Sheets. Data tidak dapat dicatat.")
            logger.error("Attempted to record data but Google Sheets was not initialized.")

    except Exception as e:
        await update.message.reply_text(f"Terjadi kesalahan saat memproses pesan Anda: {e}")
        logger.error(f"Error processing message from {user_id}: {e}", exc_info=True)


# --- Setup Flask ---
app = Flask(__name__)

@app.route('/telegram', methods=['POST'])
async def telegram_webhook():
    logger.info("Menerima pembaruan dari Telegram.")
    try:
        # Memproses update dari Telegram
        await application.process_update(Update.de_json(request.get_json(force=True), application.bot))
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Gagal memproses pembaruan Telegram: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

# Tambahkan handlers
application.add_handler(CommandHandler("start", start_command))
application.add_handler(CommandHandler("checkin", checkin_command))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Tidak ada lagi `if __name__ == '__main__':` karena Gunicorn akan mengelola startup.