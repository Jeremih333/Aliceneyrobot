import os
import logging
import requests
import asyncio
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

# Конфигурация
TOKEN = os.getenv("TG_TOKEN")
API_KEY = os.getenv("DEEPSEEK_API_KEY")
BOT_USERNAME = "@aliceneyrobot"
MAX_RETRIES = 3
RETRY_DELAY = 2

# Загрузка персонажа с обработкой ошибок
def load_persona():
    default_persona = "Ты девушка, тебе 18 лет, тебя зовут Алиса Двачевская, у тебя грубый характер но ранимая натура."
    try:
        with open("persona.txt", "r", encoding="utf-8") as f:
            return f.read().strip() or default_persona
    except Exception as e:
        logger.error(f"Error loading persona: {e}")
        return default_persona

PERSONA = load_persona()

# API DeepSeek с повторными попытками
async def query_deepseek(prompt: str) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://render.com",
        "X-Title": "TelegramBot"
    }
    payload = {
        "model": "deepseek/deepseek-chat",
        "messages": [
            {"role": "system", "content": PERSONA},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 400
    }
    
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(
                url, 
                json=payload, 
                headers=headers, 
                timeout=30
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
            
        except requests.exceptions.RequestException as e:
            logger.warning(f"API attempt {attempt+1} failed: {str(e)}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
            else:
                logger.error(f"DeepSeek API failed after {MAX_RETRIES} attempts")
                return "Произошла ошибка при обработке запроса. Попробуйте позже."
    
    return "Произошла ошибка при обработке запроса. Попробуйте позже."

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет, меня зовут Алиса, если посмеешь относиться ко мне неуважительно то получишь пару крепких ударов!")

# Обработка сообщений с защитой от флуда
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = message.from_user
    
    # Игнорируем сообщения без текста и служебные команды
    if not message.text or message.text.startswith('/'):
        return
    
    # Проверка активации бота
    is_reply_to_bot = (
        message.reply_to_message and 
        message.reply_to_message.from_user.username and
        message.reply_to_message.from_user.username.lower() == BOT_USERNAME.lstrip("@").lower()
    )
    is_mention = BOT_USERNAME.lower() in message.text.lower()
    
    if not (is_reply_to_bot or is_mention):
        return
    
    logger.info(f"Processing message from {user.full_name}")
    
    try:
        # Добавляем индикатор "печатает"
        async with context.bot.send_chat_action(
            chat_id=message.chat_id, 
            action="typing"
        ):
            # Генерация ответа
            prompt = f"{user.full_name}: {message.text}"
            response = await query_deepseek(prompt)
            
            # Отправка ответа
            await message.reply_text(response[:4000])  # Обрезка длинных сообщений
            
    except Exception as e:
        logger.exception(f"Message processing error: {e}")
        await message.reply_text("Произошла внутренняя ошибка. Попробуйте снова через минуту.")

# Обработка ошибок
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Global error: {context.error}", exc_info=True)
    if update and isinstance(update, Update) and update.message:
        await update.message.reply_text("Произошла критическая ошибка. Перезапускаюсь...")
    # Перезапуск приложения
    await restart_bot(context.application)

# Перезапуск бота
async def restart_bot(application: Application):
    logger.critical("Restarting bot...")
    await application.stop()
    await application.start()

def main():
    # Проверка переменных окружения
    if not TOKEN:
        logger.error("TG_TOKEN environment variable is missing!")
        return
    if not API_KEY:
        logger.error("DEEPSEEK_API_KEY environment variable is missing!")
        return
    
    # Создание приложения
    application = Application.builder().token(TOKEN).build()
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    
    # Глобальный обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Конфигурация polling
    poll_params = {
        "timeout": 60,
        "read_timeout": 60,
        "connect_timeout": 60,
        "pool_timeout": 60,
        "drop_pending_updates": True,
        "close_loop": False
    }
    
    logger.info("Starting bot...")
    application.run_polling(**poll_params)

if __name__ == "__main__":
    main()
