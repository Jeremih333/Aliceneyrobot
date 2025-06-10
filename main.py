import os
import logging
import asyncio
from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
import requests

# Настройка логгирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загрузка конфигурации
TOKEN = os.getenv("TG_TOKEN")
API_KEY = os.getenv("DEEPSEEK_API_KEY")
BOT_USERNAME = "@DeepSeekHelperBot"  # УКАЖИТЕ РЕАЛЬНЫЙ ЮЗЕРНЕЙМ БОТА С @

# Загрузка персонажа
with open("persona.txt", "r") as f:
    PERSONA = f.read().strip()

# API DeepSeek
def query_deepseek(prompt: str) -> str:
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": PERSONA},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 150
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"DeepSeek API error: {e}")
        return "Произошла ошибка. Попробуйте позже."

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот для этой группы. Упомяните мой юзернейм или ответьте на моё сообщение, чтобы пообщаться.")

# Обработка сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = message.from_user
    chat = message.chat
    
    # Проверка условий активации
    is_reply_to_bot = (
        message.reply_to_message and 
        message.reply_to_message.from_user.username == BOT_USERNAME.lstrip("@")
    )
    is_mention = BOT_USERNAME.lower() in message.text.lower()
    
    if not (is_reply_to_bot or is_mention):
        return
    
    # Генерация ответа
    prompt = f"{user.full_name} ({user.id}) в чате {chat.title} ({chat.id}) пишет:\n{message.text}"
    response = query_deepseek(prompt)
    
    # Отправка ответа
    await message.reply_text(response)

# Инициализация Flask для Render
app = Flask(__name__)

@app.route("/")
def home():
    return "Бот активен. Сервер работает."

async def run_bot():
    application = Application.builder().token(TOKEN).build()
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    
    logger.info("Бот запущен в режиме polling...")
    await application.run_polling()

if __name__ == "__main__":
    # Проверка переменных окружения
    if not TOKEN or not API_KEY:
        raise ValueError("Не заданы переменные окружения!")
    
    # Запускаем бота и Flask в одном цикле событий
    loop = asyncio.get_event_loop()
    bot_task = loop.create_task(run_bot())
    
    # Запуск Flask в основном потоке
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
