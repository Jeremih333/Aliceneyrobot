import os
import logging
import asyncio
import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from aiohttp import web

# Настройка логгирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загрузка конфигурации
TOKEN = os.getenv("TG_TOKEN")
API_KEY = os.getenv("DEEPSEEK_API_KEY")
BOT_USERNAME = "@aliceneyrobot"  # Убедитесь, что юзернейм правильный

# Загрузка персонажа
try:
    with open("persona.txt", "r", encoding="utf-8") as f:
        PERSONA = f.read().strip()
except Exception as e:
    logger.error(f"Error loading persona: {e}")
    PERSONA = "Ты полезный ассистент в Telegram группе. Отвечай кратко и по делу."

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
        "max_tokens": 200
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"DeepSeek API error: {e}")
        return "Произошла ошибка при обработке запроса. Попробуйте позже."

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот для этой группы. Упомяните мой юзернейм или ответьте на моё сообщение, чтобы пообщаться.")

# Обработка сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = message.from_user
    chat = message.chat
    
    # Пропускаем сообщения без текста
    if not message.text:
        return
    
    # Проверка условий активации
    is_reply_to_bot = (
        message.reply_to_message and 
        message.reply_to_message.from_user.username == BOT_USERNAME.lstrip("@")
    )
    is_mention = BOT_USERNAME.lower() in message.text.lower()
    
    if not (is_reply_to_bot or is_mention):
        return
    
    logger.info(f"Обработка сообщения от {user.full_name}: {message.text}")
    
    try:
        # Генерация ответа
        prompt = f"{user.full_name}: {message.text}"
        response = query_deepseek(prompt)
        
        # Отправка ответа
        await message.reply_text(response)
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {e}")
        await message.reply_text("Что-то пошло не так. Попробуйте еще раз.")

# Веб-сервер для Render
async def health_check(request):
    return web.Response(text="Бот активен. Сервер работает.")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 5000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Веб-сервер запущен на порту {port}")

async def main():
    # Проверка переменных окружения
    if not TOKEN:
        logger.error("TG_TOKEN environment variable is missing!")
        exit(1)
    if not API_KEY:
        logger.error("DEEPSEEK_API_KEY environment variable is missing!")
        exit(1)
    
    # Запуск веб-сервера
    asyncio.create_task(start_web_server())
    
    # Инициализация и запуск бота
    application = Application.builder().token(TOKEN).build()
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    
    logger.info("Бот запущен в режиме polling...")
    await application.run_polling()

if __name__ == "__main__":
    # Запуск основного приложения
    asyncio.run(main())
