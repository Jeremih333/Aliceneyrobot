import os
import logging
import asyncio
import threading
import time
import re
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from openai import OpenAI
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# Настройка логгирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загрузка конфигурации
TOKEN = os.getenv("TG_TOKEN")
NOVITA_API_KEY = os.getenv("NOVITA_API_KEY")
BOT_USERNAME = "@aliceneyrobot"

# Идентификатор чата без ограничений
UNLIMITED_CHAT_ID = -1001481824277

# Глобальные переменные
user_contexts = {}
daily_message_counters = {}  # Формат: {(user_id, date): count}
last_cleanup_time = time.time()

# Загрузка персонажа
try:
    with open("persona.txt", "r", encoding="utf-8") as f:
        PERSONA = f.read().strip()
    PERSONA += "\n\nТы общаешься в чате с разными пользователями. Обращайся к ним по именам. " \
               "Учитывай историю переписки с каждым пользователем отдельно. " \
               "Сообщения пользователей начинаются с их имени в формате 'Имя: текст'."
except Exception as e:
    logger.error(f"Error loading persona: {e}")
    PERSONA = "Ты девушка, тебе 18 лет, тебя зовут Алиса Двачевская, у тебя грубый характер но ранимая натура. " \
              "Ты общаешься в чате с разными пользователями. Обращайся к ним по именам. " \
              "Учитывай историю переписки с каждым пользователем отдельно. " \
              "Сообщения пользователей начинаются с их имени в формате 'Имя: текст'."

# Функция очистки устаревших счетчиков
def cleanup_old_counters():
    global daily_message_counters, last_cleanup_time
    current_time = time.time()
    
    # Проверяем каждые 30 минут
    if current_time - last_cleanup_time > 1800:
        logger.info("Starting cleanup of old message counters")
        today = datetime.utcnow().date()
        keys_to_delete = []
        
        for key in daily_message_counters.keys():
            _, date_str = key
            record_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if (today - record_date).days > 1:
                keys_to_delete.append(key)
        
        for key in keys_to_delete:
            del daily_message_counters[key]
            logger.debug(f"Removed old counter: {key}")
        
        last_cleanup_time = current_time
        logger.info(f"Cleanup completed. Removed {len(keys_to_delete)} old counters")

# Функция проверки лимита сообщений
def check_message_limit(user_id: int) -> bool:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    key = (user_id, today)
    
    # Очистка старых записей перед проверкой
    cleanup_old_counters()
    
    # Инициализация счетчика
    if key not in daily_message_counters:
        daily_message_counters[key] = 0
    
    # Проверка лимита
    if daily_message_counters[key] >= 35:
        return False
    
    # Увеличение счетчика
    daily_message_counters[key] += 1
    logger.info(f"User {user_id} message count: {daily_message_counters[key]}/35")
    return True

# Функция для очистки ответа
def clean_response(response: str) -> str:
    cleaned = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
    cleaned = cleaned.replace('<think>', '').replace('</think>', '')
    cleaned = cleaned.replace('</s>', '').replace('<s>', '')
    cleaned = re.sub(r'\n\s*\n', '\n\n', cleaned).strip()
    return cleaned

# HTTP-сервер для проверки работоспособности
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Service is alive')
    
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_http_server(port=8080):
    server_address = ('', port)
    httpd = HTTPServer(server_address, HealthHandler)
    logger.info(f"Starting HTTP health check server on port {port}")
    httpd.serve_forever()

# Запрос к DeepSeek через Novita API
def query_chat(messages: list) -> str:
    try:
        client = OpenAI(
            base_url="https://api.novita.ai/v3/openai",
            api_key=NOVITA_API_KEY,
        )
        
        response = client.chat.completions.create(
            model="deepseek/deepseek-r1-0528",
            messages=messages,
            temperature=0.7,
            max_tokens=400,
            stream=False,
            response_format={"type": "text"}
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Novita API error: {e}")
        return "Произошла ошибка при обработке запроса. Попробуйте позже."

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет, меня зовут Алиса, если посмеешь относиться ко мне неуважительно то получишь пару крепких ударов!")

async def clear_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    chat_id = update.message.chat_id
    key = (chat_id, user.id)
    
    if key in user_contexts:
        del user_contexts[key]
        logger.info(f"Context cleared for user {user.full_name} in chat {chat_id}")
        await update.message.reply_text("История диалога очищена. Начнем заново!")
    else:
        await update.message.reply_text("У тебя еще нет истории диалога со мной!")

# Обработка сообщений с учетом лимитов
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = message.from_user
    chat_id = message.chat_id
    key = (chat_id, user.id)
    
    if not message.text:
        return
    
    # Проверка условий активации
    is_reply_to_bot = (
        message.reply_to_message and 
        message.reply_to_message.from_user.username and
        message.reply_to_message.from_user.username.lower() == BOT_USERNAME.lstrip("@").lower()
    )
    is_mention = BOT_USERNAME.lower() in message.text.lower()
    
    if not (is_reply_to_bot or is_mention):
        return
    
    # Проверка лимита сообщений (кроме безлимитного чата)
    if chat_id != UNLIMITED_CHAT_ID:
        if not check_message_limit(user.id):
            logger.warning(f"User {user.full_name} ({user.id}) exceeded daily message limit")
            return
    
    logger.info(f"Обработка сообщения от {user.full_name} в чате {chat_id}: {message.text}")
    
    try:
        history = user_contexts.get(key, [])
        user_message_content = f"{user.full_name}: {message.text}"
        user_message = {"role": "user", "content": user_message_content}
        
        messages = [{"role": "system", "content": PERSONA}]
        messages.extend(history)
        messages.append(user_message)
        
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, query_chat, messages)
        cleaned_response = clean_response(response)
        
        if not cleaned_response.strip():
            cleaned_response = "Я обдумываю твой вопрос... Попробуй спросить по-другому."
        
        history.append(user_message)
        history.append({"role": "assistant", "content": cleaned_response})
        
        if len(history) > 10:
            history = history[-10:]
        
        user_contexts[key] = history
        await message.reply_text(cleaned_response)
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {e}")
        await message.reply_text("Что-то пошло не так. Попробуйте еще раз.")

def main():
    if not TOKEN:
        logger.error("TG_TOKEN environment variable is missing!")
        return
    if not NOVITA_API_KEY:
        logger.error("NOVITA_API_KEY environment variable is missing!")
        return

    # Запуск HTTP-сервера
    port = int(os.getenv('PORT', 8080))
    http_thread = threading.Thread(target=run_http_server, args=(port,), daemon=True)
    http_thread.start()

    logger.info("Ожидание 45 секунд перед запуском бота...")
    time.sleep(45)

    application = Application.builder().token(TOKEN).build()
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clear", clear_context))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    
    logger.info("Запуск бота в режиме polling...")
    
    poll_params = {
        "drop_pending_updates": True,
        "close_loop": False,
        "stop_signals": [],
        "connect_timeout": 60,
        "read_timeout": 60,
        "pool_timeout": 60
    }
    
    application.run_polling(**poll_params)

if __name__ == "__main__":
    main()
