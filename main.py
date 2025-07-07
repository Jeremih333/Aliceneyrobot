import os
import logging
import asyncio
import threading
import time
import re
import random
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

# Глобальная переменная для хранения контекста пользователей
user_contexts = {}

# Загрузка персонажа с улучшенными инструкциями
try:
    with open("persona.txt", "r", encoding="utf-8") as f:
        PERSONA = f.read().strip()
    
    # Улучшенные инструкции для модели
    PERSONA += "\n\nВажные правила общения:\n" \
               "1. Все свои мысли выражай ТОЛЬКО внутри тегов <think>...</think>, никогда не показывай их пользователю\n" \
               "2. В ответах будь естественной, используй живую разговорную речь\n" \
               "3. Не обязательно обращаться по имени в каждом сообщении\n" \
               "4. Иногда используй эмодзи для выражения эмоций (но не переусердствуй)\n" \
               "5. Всегда завершай свои мысли и предложения\n" \
               "6. Избегай формальностей, будь прямой и эмоциональной\n" \
               "7. Если обсуждаешь несколько тем - закончи одну мысль прежде чем переходить к другой"
except Exception as e:
    logger.error(f"Error loading persona: {e}")
    PERSONA = "Ты девушка, тебе 18 лет, тебя зовут Алиса Двачевская, у тебя грубый характер но ранимая натура.\n\n" \
              "Важные правила общения:\n" \
              "1. Все свои мысли выражай ТОЛЬКО внутри тегов <think>...</think>, никогда не показывай их пользователю\n" \
              "2. В ответах будь естественной, используй живую разговорную речь\n" \
              "3. Не обязательно обращаться по имени в каждом сообщении\n" \
              "4. Иногда используй эмодзи для выражения эмоций (но не переусердствуй)\n" \
              "5. Всегда завершай свои мысли и предложения\n" \
              "6. Избегай формальностей, будь прямой и эмоциональной\n" \
              "7. Если обсуждаешь несколько тем - закончи одну мысль прежде чем переходить к другой"

# Эмодзи для разных эмоций
EMOJIS = {
    'радость': ['😂', '🤣', '😊', '😍', '🥰', '😎', '🤩'],
    'злость': ['😠', '😤', '👿', '💢', '🤬'],
    'грусть': ['😢', '😭', '😔', '🥺'],
    'удивление': ['😮', '😲', '🤯', '😳'],
    'ирония': ['😏', '😒', '🙄', '🤨'],
    'дружелюбие': ['👍', '👌', '✌️', '🤝', '🤗'],
    'нейтральные': ['😐', '😶', '🤔', '🙃']
}

# Функция для очистки ответа от технической информации
def clean_response(response: str) -> str:
    """Удаляет технические теги и их содержимое из ответа"""
    # Удаление think-тегов с содержимым
    cleaned = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
    # Удаление одиночных тегов
    cleaned = re.sub(r'</?think>', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace('</s>', '').replace('<s>', '')
    
    # Удаление технических инструкций
    cleaned = re.sub(r'\[.*?\]', '', cleaned)
    
    # Удаление лишних переносов строк
    cleaned = re.sub(r'\n\s*\n', '\n\n', cleaned).strip()
    
    # Завершение незаконченных предложений
    if cleaned and cleaned[-1] not in ['.', '!', '?', '…']:
        if ',' in cleaned or ';' in cleaned:
            # Если есть запятые - вероятно сложное предложение
            cleaned += '...'
        else:
            # Простое предложение
            cleaned += '.'
    
    # Удаление маркированных списков (сохраняя содержание)
    cleaned = re.sub(r'(?:^|\n)[-*•]\s*', '\n', cleaned)
    
    return cleaned

# Функция для добавления эмодзи в сообщения
def add_emoji(text: str, emotion: str = None) -> str:
    """Добавляет эмодзи в сообщение с определенной вероятностью"""
    if random.random() > 0.3:  # 30% вероятность добавления эмодзи
        return text
    
    if not emotion:
        # Автоматическое определение эмоции по содержанию
        if any(word in text.lower() for word in ['смех', 'смешно', 'ха-ха', 'ржу']):
            emotion = 'радость'
        elif any(word in text.lower() for word in ['злость', 'злой', 'бесит', 'раздражает']):
            emotion = 'злость'
        elif any(word in text.lower() for word in ['грустно', 'печаль', 'обидно']):
            emotion = 'грусть'
        elif any(word in text.lower() for word in ['удивлен', 'не ожидал', 'вау']):
            emotion = 'удивление'
        else:
            emotion = 'нейтральные'
    
    emoji_list = EMOJIS.get(emotion, EMOJIS['нейтральные'])
    emoji = random.choice(emoji_list)
    
    # Вставляем эмодзи в случайное место
    if len(text) > 20 and random.random() > 0.5:
        words = text.split()
        if len(words) > 3:
            pos = random.randint(1, len(words)-1)
            words.insert(pos, emoji)
            return ' '.join(words)
    
    return text + ' ' + emoji

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

# Запрос к DeepSeek через Novita API с учетом истории диалога
def query_chat(messages: list) -> str:
    try:
        # Инициализация клиента Novita API
        client = OpenAI(
            base_url="https://api.novita.ai/v3/openai",
            api_key=NOVITA_API_KEY,
        )
        
        # Отправка запроса
        response = client.chat.completions.create(
            model="deepseek/deepseek-r1-0528",
            messages=messages,
            temperature=0.75,  # Немного выше для разнообразия
            max_tokens=500,    # Увеличено для завершенных ответов
            stream=False,
            response_format={"type": "text"}
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Novita API error: {e}")
        return "Произошла ошибка при обработке запроса. Попробуйте позже."

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет, меня зовут Алиса, если посмеешь относиться ко мне неуважительно то получишь пару крепких ударов! 👊")

async def clear_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Очистка истории диалога для пользователя"""
    user = update.message.from_user
    chat_id = update.message.chat_id
    key = (chat_id, user.id)
    
    if key in user_contexts:
        del user_contexts[key]
        logger.info(f"Context cleared for user {user.full_name} in chat {chat_id}")
        await update.message.reply_text("История диалога очищена. Начнем заново! 🔄")
    else:
        await update.message.reply_text("У тебя еще нет истории диалога со мной! 🤷‍♀️")

# Обработка сообщений с учетом контекста
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
    
    logger.info(f"Обработка сообщения от {user.full_name} в чате {chat_id}: {message.text}")
    
    try:
        # Получаем текущий контекст или создаем новый
        history = user_contexts.get(key, [])
        
        # Формируем сообщение пользователя с именем
        user_message_content = f"{user.full_name}: {message.text}"
        user_message = {"role": "user", "content": user_message_content}
        
        # Собираем все сообщения для отправки
        messages = [{"role": "system", "content": PERSONA}]
        messages.extend(history)
        messages.append(user_message)
        
        # Генерация ответа
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, query_chat, messages)
        
        # Очистка ответа от технической информации
        cleaned_response = clean_response(response)
        
        # Проверка на пустой ответ после очистки
        if not cleaned_response.strip():
            cleaned_response = "Я обдумываю твой вопрос... Попробуй спросить по-другому."
        
        # Добавление эмодзи
        final_response = add_emoji(cleaned_response)
        
        # Обновляем контекст
        history.append(user_message)
        history.append({"role": "assistant", "content": cleaned_response})  # Сохраняем без эмодзи
        
        # Ограничиваем историю последними 10 сообщениями
        if len(history) > 10:
            history = history[-10:]
        
        user_contexts[key] = history
        
        # Отправка ответа
        await message.reply_text(final_response)
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {e}")
        await message.reply_text("Что-то пошло не так. Попробуйте еще раз. 🤔")

def main():
    # Проверка переменных окружения
    if not TOKEN:
        logger.error("TG_TOKEN environment variable is missing!")
        return
    if not NOVITA_API_KEY:
        logger.error("NOVITA_API_KEY environment variable is missing!")
        return

    # Запуск HTTP-сервера для проверки работоспособности
    port = int(os.getenv('PORT', 8080))
    http_thread = threading.Thread(target=run_http_server, args=(port,), daemon=True)
    http_thread.start()

    # Увеличиваем задержку для завершения предыдущих инстансов
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
    
    # Параметры для polling
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
