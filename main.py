import os
import logging
import threading
import time
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from huggingface_hub import InferenceClient
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
HF_TOKEN = os.getenv("HF_TOKEN")  # Изменено название переменной
BOT_USERNAME = "@aliceneyrobot"

# Загрузка персонажа
try:
    with open("persona.txt", "r", encoding="utf-8") as f:
        PERSONA = f.read().strip()
except Exception as e:
    logger.error(f"Error loading persona: {e}")
    PERSONA = "Ты девушка, тебе 18 лет, тебя зовут Алиса Двачевская, у тебя грубый характер но ранимая натура."

# Функция для очистки ответа от технической информации
def clean_response(response: str) -> str:
    """Удаляет технические теги и их содержимое из ответа"""
    cleaned = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
    cleaned = cleaned.replace('<think>', '').replace('</think>', '')
    cleaned = cleaned.replace('</s>', '').replace('<s>', '')
    cleaned = re.sub(r'\n\s*\n', '\n\n', cleaned).strip()
    return cleaned

# Класс для HTTP-сервера (для проверки работоспособности)
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

# Запрос через Hugging Face Inference API
def query_deepseek(prompt: str) -> str:
    try:
        client = InferenceClient(
            provider="together",
            api_key=HF_TOKEN
        )
        completion = client.chat_completion(  # Измененный вызов API
            model="deepseek-ai/DeepSeek-R1-0528",  # Обновленная модель
            messages=[
                {"role": "system", "content": PERSONA},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=400
        )
        return completion.choices[0].message.content
    except Exception as e:
        logger.error(f"Hugging Face API error: {e}")
        return "Произошла ошибка при обработке запроса. Попробуйте позже."

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет, меня зовут Алиса, если посмеешь относиться ко мне неуважительно то получишь пару крепких ударов!")

# Обработка сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = message.from_user
    
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
    
    logger.info(f"Обработка сообщения от {user.full_name}: {message.text}")
    
    try:
        # Генерация ответа
        prompt = f"{user.full_name}: {message.text}"
        response = query_deepseek(prompt)
        
        # Очистка ответа от технической информации
        cleaned_response = clean_response(response)
        
        if not cleaned_response.strip():
            cleaned_response = "Я обдумываю твой вопрос... Попробуй спросить по-другому."
        
        # Отправка ответа
        await message.reply_text(cleaned_response)
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {e}")
        await message.reply_text("Что-то пошло не так. Попробуйте еще раз.")

def main():
    # Проверка переменных окружения
    if not TOKEN:
        logger.error("TG_TOKEN environment variable is missing!")
        return
    if not HF_TOKEN:
        logger.error("HF_TOKEN environment variable is missing!")
        return

    # Запуск HTTP-сервера для проверки работоспособности
    port = int(os.getenv('PORT', 8080))
    http_thread = threading.Thread(target=run_http_server, args=(port,), daemon=True)
    http_thread.start()

    application = Application.builder().token(TOKEN).build()
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    
    logger.info("Запуск бота в режиме polling...")
    application.run_polling(
        drop_pending_updates=True,
        close_loop=False,
        connect_timeout=60,
        read_timeout=60,
        pool_timeout=60
    )

if __name__ == "__main__":
    main()
