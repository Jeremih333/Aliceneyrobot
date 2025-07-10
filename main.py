import os
import logging
import asyncio
import threading
import time
import re
import random
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from openai import OpenAI
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    BotCommand
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
    CallbackQueryHandler
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

# Идентификатор разработчика
DEVELOPER_ID = 1040929628

# Идентификатор чата без ограничений
UNLIMITED_CHAT_ID = -1001481824277

# Глобальные переменные
user_contexts = {}
daily_message_counters = {}  # Формат: {(user_id, date): count}
unlimited_users = {}  # Пользователи с безлимитом: {user_id: date}
last_cleanup_time = time.time()

# Состояния для ConversationHandler разработчика
SELECT_USER, SELECT_ACTION, INPUT_AMOUNT = range(3)

# Список эмодзи для использования
EMOJI_LIST = ["😊", "😂", "😍", "🤔", "😎", "👍", "❤️", "✨", "🎉", "💔"]

# Загрузка персонажа
try:
    with open("persona.txt", "r", encoding="utf-8") as f:
        PERSONA = f.read().strip()
    PERSONA += "\n\nТы общаешься в чате с разными пользователями. Обращайся к ним по именам. " \
               "Учитывай историю переписки с каждым пользователем отдельно. " \
               "Всегда завершай сообщение полностью. " \
               "Форматируй ответы с абзацами и отступами, где это уместно."
except Exception as e:
    logger.error(f"Error loading persona: {e}")
    PERSONA = "Ты девушка, тебе 18 лет, тебя зовут Алиса Двачевская, у тебя грубый характер но ранимая натура. " \
              "Ты общаешься в чате с разными пользователями. Обращайся к ним иногда по именам. " \
              "Учитывай историю переписки с каждым пользователем отдельно. " \
              "Сообщения пользователей начинаются с их имени в формате 'Имя: текст'. " \
              "Все действия описывай в формате *действие*. " \
              "Всегда завершай сообщение полностью. " \
              "Форматируй ответы с абзацами и отступами, где это уместно."

# Функция очистки устаревших счетчиков
def cleanup_old_counters():
    global daily_message_counters, unlimited_users, last_cleanup_time
    current_time = time.time()
    today = datetime.utcnow().date()
    
    # Проверяем каждые 30 минут
    if current_time - last_cleanup_time > 1800:
        logger.info("Starting cleanup of old data")
        keys_to_delete = []
        
        # Удаляем старые счетчики сообщений
        for key in daily_message_counters.keys():
            _, date_str = key
            record_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if (today - record_date).days > 1:
                keys_to_delete.append(key)
        
        for key in keys_to_delete:
            del daily_message_counters[key]
            logger.debug(f"Removed old counter: {key}")
        
        # Удаляем устаревшие безлимитные записи
        users_to_remove = []
        for user_id, unlimited_date in unlimited_users.items():
            if (today - unlimited_date).days > 0:
                users_to_remove.append(user_id)
        
        for user_id in users_to_remove:
            del unlimited_users[user_id]
            logger.debug(f"Removed unlimited status for user: {user_id}")
        
        last_cleanup_time = current_time
        logger.info(f"Cleanup completed. Removed {len(keys_to_delete)} old counters and {len(users_to_remove)} unlimited users")

# Функция проверки лимита сообщений
def check_message_limit(user_id: int) -> bool:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    key = (user_id, today)
    
    # Очистка старых записей перед проверкой
    cleanup_old_counters()
    
    # Проверяем безлимитный статус
    if user_id in unlimited_users:
        return True
    
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

# Функция для форматирования действий (без обратных кавычек)
def format_actions(text: str) -> str:
    # Просто оставляем действия в формате *действие*
    return text

# Функция для добавления эмодзи (реже и только в конце)
def add_emojis(text: str) -> str:
    if not text:
        return text
    
    # Добавляем эмодзи только в 20% случаев
    if random.random() < 0.2:
        # Добавляем только 1 эмодзи
        selected_emoji = random.choice(EMOJI_LIST)
        
        # Убедимся, что в конце нет эмодзи
        if text[-1] not in EMOJI_LIST:
            return text + selected_emoji
    return text

# Функция для завершения незаконченных предложений
def complete_sentences(text: str) -> str:
    if not text:
        return text
    
    # Если текст не заканчивается на пунктуационный знак, добавляем точку
    if not re.search(r'[.!?…]$', text):
        text += '.'
    
    return text

# Функция для форматирования абзацев
def format_paragraphs(text: str) -> str:
    # Разделяем текст на абзацы по двойным переносам строк
    paragraphs = text.split('\n\n')
    
    # Форматируем каждый абзац с отступом
    formatted = []
    for paragraph in paragraphs:
        if paragraph.strip():
            # Убираем лишние пробелы
            cleaned = re.sub(r'\s+', ' ', paragraph).strip()
            # Добавляем красную строку (4 пробела в начале)
            formatted.append(f"    {cleaned}")
    
    # Собираем обратно с двойными переносами строк
    return '\n\n'.join(formatted)

# Функция для очистки ответа
def clean_response(response: str) -> str:
    # Удаляем специальные теги
    cleaned = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
    cleaned = cleaned.replace('<think>', '').replace('</think>', '')
    cleaned = cleaned.replace('</s>', '').replace('<s>', '')
    
    # Форматируем действия (без обратных кавычек)
    cleaned = format_actions(cleaned)
    
    # Удаляем лишние переносы
    cleaned = re.sub(r'\n\s*\n', '\n\n', cleaned).strip()
    
    # Завершаем незаконченные предложения
    cleaned = complete_sentences(cleaned)
    
    # Форматируем абзацы
    cleaned = format_paragraphs(cleaned)
    
    # Добавляем эмодзи только в конце всего сообщения (реже)
    cleaned = add_emojis(cleaned)
    
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
            max_tokens=600,  # Увеличили лимит токенов для форматирования
            stream=False,
            response_format={"type": "text"}
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Novita API error: {e}")
        return "Произошла ошибка при обработке запроса. Попробуйте позже."

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Обновленное приветственное сообщение
    await update.message.reply_text(
        "Привет, меня зовут Алиса, если посмеешь относиться ко мне неуважительно то получишь пару крепких ударов!\n\n"
        "/info - информация обо мне и как правильно ко мне обращаться.\n"
        "/stat - узнать свой статус и оставшиеся сообщения"
    )

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /info с инлайн-кнопкой"""
    keyboard = [
        [InlineKeyboardButton("Информация", url="https://telegra.ph/Ob-Alise-Dvachevskoj-07-09")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "❗️Здесь вы можете ознакомиться с правилами использования нашего бота.\n"
        "Рекомендуем прочитать перед использованием.",
        reply_markup=reply_markup
    )

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

async def stat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /stat - информация о статусе пользователя"""
    user = update.message.from_user
    today = datetime.utcnow().strftime("%Y-%m-%d")
    key = (user.id, today)
    
    # Проверяем наличие истории диалога
    has_context = False
    for ctx_key in user_contexts.keys():
        if ctx_key[1] == user.id:  # Ищем по user_id
            has_context = True
            break
    
    # Проверяем безлимитный статус
    if user.id in unlimited_users:
        remaining_text = "♾️"
    else:
        # Получаем количество использованных сообщений
        used_messages = daily_message_counters.get(key, 0)
        remaining = max(0, 35 - used_messages)
        remaining_text = f"{remaining}/35"
    
    # Формируем сообщение
    message = (
        f"📊 <b>Ваш статус:</b>\n\n"
        f"• Осталось сообщений сегодня: <b>{remaining_text}</b>\n"
        f"• История диалога: {'сохранена' if has_context else 'отсутствует'}\n\n"
        f"💡 Для сброса истории используйте /clear"
    )
    
    await update.message.reply_text(message, parse_mode="HTML")

# Получение статистики пользователя по ID
def get_user_stat(user_id: int) -> str:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    key = (user_id, today)
    
    # Проверяем безлимитный статус
    if user_id in unlimited_users:
        remaining_text = "♾️"
    else:
        # Получаем количество использованных сообщений
        used_messages = daily_message_counters.get(key, 0)
        remaining = max(0, 35 - used_messages)
        remaining_text = f"{remaining}/35"
    
    # Проверяем наличие истории диалога
    has_context = any(ctx_key[1] == user_id for ctx_key in user_contexts.keys())
    
    # Формируем статистику
    return (
        f"👤 <b>Статистика пользователя ID {user_id}:</b>\n\n"
        f"• Осталось сообщений сегодня: <b>{remaining_text}</b>\n"
        f"• История диалога: {'сохранена' if has_context else 'отсутствует'}\n"
    )

# Обработчик команды /dev (только для разработчика)
async def dev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скрытая команда для разработчика"""
    user = update.message.from_user
    
    # Проверяем, является ли пользователь разработчиком
    if user.id != DEVELOPER_ID:
        logger.warning(f"User {user.id} tried to access dev command")
        await update.message.reply_text("У вас нет прав для использования этой команды.")
        return ConversationHandler.END
    
    # Запрашиваем ID пользователя
    await update.message.reply_text(
        "🔧 <b>Режим разработчика</b>\n\n"
        "Введите ID пользователя, с которым хотите работать:",
        parse_mode="HTML"
    )
    
    return SELECT_USER

# Обработка введенного ID пользователя
async def select_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    
    # Проверяем, является ли ввод числом
    if not user_input.isdigit():
        await update.message.reply_text("❌ ID пользователя должен быть числом. Попробуйте еще раз:")
        return SELECT_USER
    
    user_id = int(user_input)
    context.user_data['target_user_id'] = user_id
    
    # Показываем статистику пользователя
    user_stat = get_user_stat(user_id)
    
    # Создаем клавиатуру с действиями
    keyboard = [
        [InlineKeyboardButton("➕ Добавить сообщения", callback_data="add_messages")],
        [InlineKeyboardButton("➖ Убрать сообщения", callback_data="remove_messages")],
        [InlineKeyboardButton("♾️ Включить безлимит", callback_data="set_unlimited")],
        [InlineKeyboardButton("🚫 Выключить безлимит", callback_data="remove_unlimited")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"{user_stat}\nВыберите действие:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    
    return SELECT_ACTION

# Обработка выбора действия
async def select_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    action = query.data
    context.user_data['action'] = action
    
    # Для специальных действий обрабатываем сразу
    if action in ["set_unlimited", "remove_unlimited"]:
        target_user_id = context.user_data['target_user_id']
        today = datetime.utcnow().date()
        
        if action == "set_unlimited":
            unlimited_users[target_user_id] = today
            action_result = "безлимит включен ♾️"
        else:
            if target_user_id in unlimited_users:
                del unlimited_users[target_user_id]
                action_result = "безлимит выключен 🚫"
            else:
                action_result = "безлимит уже выключен 🚫"
        
        # Формируем отчет
        report = (
            f"✅ Успешно!\n\n"
            f"• Пользователь ID: {target_user_id}\n"
            f"• Действие: {action_result}\n\n"
            f"{get_user_stat(target_user_id)}"
        )
        
        await query.edit_message_text(report, parse_mode="HTML")
        return ConversationHandler.END
    
    # Для обычных действий запрашиваем количество
    action_text = "добавить" if action == "add_messages" else "убрать"
    
    await query.edit_message_text(
        f"✏️ Введите количество сообщений для {action_text} или отправьте ♾️ для безлимита:"
    )
    
    return INPUT_AMOUNT

# Обработка ввода количества сообщений
async def input_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    target_user_id = context.user_data['target_user_id']
    action = context.user_data['action']
    today = datetime.utcnow().strftime("%Y-%m-%d")
    key = (target_user_id, today)
    
    # Обработка специальных эмодзи
    if user_input == "♾️":
        # Включаем безлимит
        unlimited_users[target_user_id] = datetime.utcnow().date()
        action_result = "безлимит включен ♾️"
    elif user_input == "🚫":
        # Выключаем безлимит
        if target_user_id in unlimited_users:
            del unlimited_users[target_user_id]
            action_result = "безлимит выключен 🚫"
        else:
            action_result = "безлимит уже выключен 🚫"
    else:
        # Обработка числового ввода
        if not user_input.isdigit():
            await update.message.reply_text("❌ Количество сообщений должно быть числом или эмодзи ♾️/🚫. Попробуйте еще раз:")
            return INPUT_AMOUNT
        
        amount = int(user_input)
        
        # Инициализируем счетчик, если его еще нет
        if key not in daily_message_counters:
            daily_message_counters[key] = 0
        
        # Выполняем действие
        if action == "add_messages":
            daily_message_counters[key] = max(0, daily_message_counters[key] - amount)
            action_result = f"добавлено {amount} сообщений"
        else:
            daily_message_counters[key] += amount
            action_result = f"убрано {amount} сообщений"
    
    # Формируем отчет
    report = (
        f"✅ Успешно!\n\n"
        f"• Пользователь ID: {target_user_id}\n"
        f"• Действие: {action_result}\n\n"
        f"{get_user_stat(target_user_id)}"
    )
    
    await update.message.reply_text(report, parse_mode="HTML")
    
    # Завершаем диалог
    return ConversationHandler.END

# Отмена диалога разработчика
async def cancel_dev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Операция отменена.")
    return ConversationHandler.END

# Обработка сообщений с учетом лимитов
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = message.from_user
    chat_id = message.chat_id
    key = (chat_id, user.id)
    
    if not message.text:
        return
    
    # Проверка условий активации для групповых чатов
    is_reply_to_bot = (
        message.reply_to_message and 
        message.reply_to_message.from_user.username and
        message.reply_to_message.from_user.username.lower() == BOT_USERNAME.lstrip("@").lower()
    )
    is_mention = BOT_USERNAME.lower() in message.text.lower()
    
    # Для групповых чатов - пропускаем некорректные сообщения
    if message.chat.type != "private":
        if not (is_reply_to_bot or is_mention):
            return
    
    # Проверка лимита сообщений (кроме безлимитного чата)
    if chat_id != UNLIMITED_CHAT_ID:
        if not check_message_limit(user.id):
            logger.warning(f"User {user.full_name} ({user.id}) exceeded daily message limit")
            await message.reply_text(
                "❗️Вы достигли ежедневного лимита на общение с Алисой в 35 сообщений в день, "
                "возвращайтесь завтра или продолжите безлимитно ей пользоваться в чате - "
                "https://t.me/freedom346"
            )
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

async def post_init(application: Application) -> None:
    """Устанавливаем меню команд при запуске бота"""
    commands = [
        BotCommand("start", "Начало работы с ботом"),
        BotCommand("info", "Информация о боте и правила использования"),
        BotCommand("clear", "Очистить историю диалога"),
        BotCommand("stat", "Статус и оставшиеся сообщения")
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Меню команд бота установлено")

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

    # Создаем приложение с обработчиком post_init
    application = Application.builder().token(TOKEN).post_init(post_init).build()
    
    # Регистрация обработчиков команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("info", info))
    application.add_handler(CommandHandler("clear", clear_context))
    application.add_handler(CommandHandler("stat", stat))
    
    # Скрытая команда для разработчика
    dev_handler = ConversationHandler(
        entry_points=[CommandHandler("dev", dev)],
        states={
            SELECT_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_user)],
            SELECT_ACTION: [CallbackQueryHandler(select_action)],
            INPUT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_amount)]
        },
        fallbacks=[CommandHandler("cancel", cancel_dev)],
        allow_reentry=True
    )
    application.add_handler(dev_handler)
    
    # Основной обработчик сообщений
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
