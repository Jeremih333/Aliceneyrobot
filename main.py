import os
import logging
import requests
import asyncio
import threading
import time
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update
from telegram.constants import ChatAction
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
API_KEY = os.getenv("DEEPSEEK_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Для обработки изображений
BOT_USERNAME = "@aliceneyrobot"

# Загрузка персонажа
try:
    with open("persona.txt", "r", encoding="utf-8") as f:
        PERSONA = f.read().strip()
except Exception as e:
    logger.error(f"Error loading persona: {e}")
    PERSONA = "Ты девушка, тебе 18 лет, тебя зовут Алиса Двачевская, у тебя грубый характер но ранимая натура."

# Класс для HTTP-сервера
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

# API DeepSeek через OpenRouter
def query_deepseek(prompt: str) -> str:
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
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except requests.exceptions.HTTPError as e:
        logger.error(f"DeepSeek API HTTP error: {e.response.text}")
    except Exception as e:
        logger.error(f"DeepSeek API error: {e}")
    return "Произошла ошибка при обработке запроса. Попробуйте позже."

# Распознавание голосовых сообщений
def speech_to_text(audio_file: str) -> str:
    try:
        url = "https://api.openai.com/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
        
        with open(audio_file, "rb") as f:
            files = {"file": (audio_file, f)}
            data = {"model": "whisper-1"}
            response = requests.post(url, headers=headers, files=files, data=data, timeout=30)
            response.raise_for_status()
            return response.json().get("text", "")
    except Exception as e:
        logger.error(f"Ошибка распознавания речи: {e}")
        return ""

# Обработка изображений
def describe_image(image_url: str) -> str:
    try:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "gpt-4-vision-preview",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Подробно опиши изображение на русском языке"},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url}
                        }
                    ]
                }
            ],
            "max_tokens": 300
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Ошибка обработки изображения: {e}")
        return "Не удалось обработать изображение"

# Проверка активации бота
def is_bot_activated(message) -> bool:
    # Проверка ответа на сообщение бота
    is_reply_to_bot = (
        message.reply_to_message and 
        message.reply_to_message.from_user.username and
        message.reply_to_message.from_user.username.lower() == BOT_USERNAME.lstrip("@").lower()
    )
    
    # Проверка упоминания в тексте/подписи
    text = message.text or message.caption or ""
    is_mention = BOT_USERNAME.lower() in text.lower()
    
    return is_reply_to_bot or is_mention

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет, меня зовут Алиса, если посмеешь относиться ко мне неуважительно то получишь пару крепких ударов!")

# Общая функция обработки запроса
async def process_request(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
    message = update.message
    stop_event = asyncio.Event()
    typing_task = asyncio.create_task(show_typing_indicator(message.chat_id, stop_event))
    
    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, query_deepseek, prompt)
        
        stop_event.set()
        await typing_task
        
        await message.reply_text(response)
    except Exception as e:
        logger.error(f"Ошибка обработки запроса: {e}")
        stop_event.set()
        if not typing_task.done():
            await typing_task
        await message.reply_text("Что-то пошло не так. Попробуйте еще раз.")

# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    
    if not is_bot_activated(message):
        return
    
    logger.info(f"Обработка текста от {message.from_user.full_name}: {message.text}")
    await process_request(update, context, f"{message.from_user.full_name}: {message.text}")

# Обработка голосовых сообщений
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    
    if not is_bot_activated(message):
        return
    
    logger.info(f"Обработка голосового сообщения от {message.from_user.full_name}")
    
    # Скачиваем голосовое сообщение
    voice_file = await message.voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_audio:
        await voice_file.download_to_drive(temp_audio.name)
        text = speech_to_text(temp_audio.name)
        os.unlink(temp_audio.name)
    
    if not text:
        await message.reply_text("Не удалось распознать речь")
        return
    
    logger.info(f"Распознанный текст: {text}")
    await process_request(update, context, f"{message.from_user.full_name} (голосовое): {text}")

# Обработка изображений, гифок и видео
async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    
    if not is_bot_activated(message):
        return
    
    logger.info(f"Обработка медиа от {message.from_user.full_name}")
    
    # Получаем самое качественное изображение
    if message.photo:
        photo = message.photo[-1]
        media_file = await photo.get_file()
        image_url = media_file.file_path
    elif message.video:
        media_file = await message.video.get_file()
        image_url = media_file.file_path
    elif message.animation:  # GIF
        media_file = await message.animation.get_file()
        image_url = media_file.file_path
    else:
        await message.reply_text("Тип медиа не поддерживается")
        return
    
    # Описываем изображение
    description = describe_image(image_url)
    if not description:
        await message.reply_text("Не удалось обработать медиа")
        return
    
    # Формируем запрос с описанием
    caption = message.caption or ""
    prompt = (
        f"{message.from_user.full_name} отправил медиафайл с описанием: '{caption}'\n\n"
        f"Описание содержимого: {description}\n\n"
        "Ответь на сообщение пользователя"
    )
    
    await process_request(update, context, prompt)

async def show_typing_indicator(chat_id: int, stop_event: asyncio.Event):
    application = Application.get_running_application()
    while not stop_event.is_set():
        try:
            await application.bot.send_chat_action(
                chat_id=chat_id,
                action=ChatAction.TYPING
            )
        except Exception as e:
            logger.error(f"Ошибка отправки индикатора: {e}")
            break
        await asyncio.wait_for(stop_event.wait(), timeout=5)

def main():
    if not TOKEN:
        logger.error("TG_TOKEN environment variable is missing!")
        return
    if not API_KEY:
        logger.error("DEEPSEEK_API_KEY environment variable is missing!")
        return
    if not OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY environment variable is missing for media processing!")

    # Запуск HTTP-сервера
    port = int(os.getenv('PORT', 8080))
    http_thread = threading.Thread(target=run_http_server, args=(port,), daemon=True)
    http_thread.start()

    logger.info("Ожидание 45 секунд перед запуском бота...")
    time.sleep(45)

    application = Application.builder().token(TOKEN).build()
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.ANIMATION, handle_media))
    
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
