import os
import logging
import random
import asyncio
import filetype
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.tl.functions.account import GetAuthorizationsRequest
from telethon.tl.functions.users import GetFullUserRequest
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

API_BOT_TOKEN = "8318696400:AAHYmCrjC4L7bKDnlJ_JueBhbUS6ErJcs2E"
FUN_CHANNEL = "sexualr34"  # Канал для .fun команды (без @)

user_clients = {}
user_states = {}
user_telethon_ids = {}

os.makedirs("sessions", exist_ok=True)
os.makedirs("temp", exist_ok=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("📝 Регистрация", callback_data='register')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("👋 Добро пожаловать! Выберите действие:", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    await query.answer()
    if query.data == 'register':
        await context.bot.send_message(chat_id, "🔐 Введите ваш API_ID:")
        context.user_data['step'] = 'api_id'

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    step = context.user_data.get('step')

    if step == 'api_id':
        try:
            context.user_data['api_id'] = int(text)
            await context.bot.send_message(chat_id, "🔐 Теперь введите ваш API_HASH:")
            context.user_data['step'] = 'api_hash'
        except ValueError:
            await context.bot.send_message(chat_id, "❌ API_ID должен быть числом. Попробуйте еще раз:")

    elif step == 'api_hash':
        context.user_data['api_hash'] = text
        await context.bot.send_message(chat_id, "📱 Введите ваш номер телефона (в формате +79123456789):")
        context.user_data['step'] = 'phone'

    elif step == 'phone':
        context.user_data['phone'] = text
        await context.bot.send_message(chat_id, "⏳ Пытаюсь авторизоваться...")

        api_id = context.user_data['api_id']
        api_hash = context.user_data['api_hash']
        phone = context.user_data['phone']

        if tg_user_id in user_clients:
            await user_clients[tg_user_id].disconnect()
            del user_clients[tg_user_id]

        client = TelegramClient(f"sessions/{tg_user_id}", api_id, api_hash)
        
        try:
            await client.connect()
            logger.info(f"Успешное подключение для пользователя {tg_user_id}")
        except Exception as e:
            await context.bot.send_message(chat_id, f"❌ Ошибка подключения: {e}")
            logger.error(f"Ошибка подключения: {e}")
            return

        if not await client.is_user_authorized():
            try:
                sent = await client.send_code_request(phone)
                logger.info(f"Код отправлен для {phone}. Тип: {sent.type}")
                await context.bot.send_message(chat_id, "📨 Введите код из Telegram (в формате '1 2 3 4 5'):")
                context.user_data['client'] = client
                context.user_data['step'] = 'code'
            except Exception as e:
                await client.disconnect()
                await context.bot.send_message(chat_id, f"❌ Ошибка при запросе кода: {e}")
                logger.error(f"Ошибка запроса кода: {e}")
        else:
            me = await client.get_me()
            real_user_id = me.id
            user_clients[tg_user_id] = client
            user_states[tg_user_id] = 'active'
            user_telethon_ids[tg_user_id] = real_user_id
            await context.bot.send_message(chat_id, "✅ Уже авторизован! Бот готов к работе.")
            await setup_listener(client, tg_user_id, real_user_id, context.bot, chat_id)

    elif step == 'code':
        code = text.replace(' ', '')
        client = context.user_data['client']
        phone = context.user_data['phone']
        
        try:
            await client.sign_in(phone=phone, code=code)
            me = await client.get_me()
            real_user_id = me.id
            user_clients[tg_user_id] = client
            user_states[tg_user_id] = 'active'
            user_telethon_ids[tg_user_id] = real_user_id
            await context.bot.send_message(chat_id, "✅ Авторизация успешна! Бот готов к работе.")
            await setup_listener(client, tg_user_id, real_user_id, context.bot, chat_id)
        except SessionPasswordNeededError:
            await context.bot.send_message(chat_id, "🔐 Введите пароль от 2FA:")
            context.user_data['step'] = 'password'
        except Exception as e:
            await client.disconnect()
            await context.bot.send_message(chat_id, f"❌ Ошибка входа: {e}")
            logger.error(f"Ошибка входа: {e}")

    elif step == 'password':
        password = text
        client = context.user_data['client']
        
        try:
            await client.sign_in(password=password)
            me = await client.get_me()
            real_user_id = me.id
            user_clients[tg_user_id] = client
            user_states[tg_user_id] = 'active'
            user_telethon_ids[tg_user_id] = real_user_id
            await context.bot.send_message(chat_id, "✅ Авторизация с 2FA успешна! Бот готов к работе.")
            await setup_listener(client, tg_user_id, real_user_id, context.bot, chat_id)
        except Exception as e:
            await client.disconnect()
            await context.bot.send_message(chat_id, f"❌ Ошибка: {e}")
            logger.error(f"Ошибка 2FA: {e}")

async def get_random_media_from_channel(client: TelegramClient, channel_username: str):
    try:
        # Получаем entity канала
        channel = await client.get_entity(channel_username)
        
        # Получаем последние 100 сообщений из канала
        messages = await client.get_messages(channel, limit=100)
        
        # Фильтруем только сообщения с медиа (фото или видео)
        media_messages = [msg for msg in messages if msg.media and (msg.photo or msg.video)]
        
        if not media_messages:
            return None
        
        # Выбираем случайное медиа-сообщение
        random_media = random.choice(media_messages)
        
        # Скачиваем медиа во временный файл
        file_path = await client.download_media(random_media.media, file="temp/")
        
        return file_path
    except Exception as e:
        logger.error(f"Ошибка при получении медиа из канала: {e}")
        return None

async def get_user_id(client: TelegramClient, username: str):
    try:
        if not username.startswith('@'):
            username = '@' + username
        
        user = await client.get_entity(username)
        return user.id
    except Exception as e:
        logger.error(f"Ошибка при получении ID пользователя: {e}")
        return None

async def setup_listener(client: TelegramClient, tg_user_id: int, telethon_user_id: int, bot, chat_id):
    logger.info(f"Устанавливаю обработчик для Telegram ID: {tg_user_id}, Telethon ID: {telethon_user_id}")

    @client.on(events.NewMessage(from_users=telethon_user_id))
    async def handler(event):
        logger.info(f"Получено сообщение: {event.text} от {event.sender_id}")

        state = user_states.get(tg_user_id, 'active')
        if state == 'stopped' and not event.text.startswith('.start'):
            logger.info("Бот остановлен, игнорирую сообщение")
            return

        text = event.text.strip()

        try:
            if text.startswith('.'):
                await event.delete()
                logger.info(f"Удалено сообщение с командой: {text}")
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")

        if text == '.help':
            help_text = (
                "📖 <b>Помощь по боту:</b>\n\n"
                "📱 <b>.number</b> — ваш номер телефона\n"
                "🆔 <b>.id @username</b> — получить Telegram ID\n"
                "📡 <b>.ses</b> — активные сессии\n"
                "📩 <b>.send сообщение @username</b> — отправить сообщение\n"
                "😴 <b>.sleep</b> — добрые слова\n"
                "🎉 <b>.fun</b> — случайный медиафайл\n"
                "⛔️ <b>.stop</b> — остановить бота\n"
                "✅ <b>.start</b> — снова включить\n"
                "🗑️ <b>.clearses</b> — удалить сессию\n"
                "🎞️ <b>.anim текст</b> — анимировать текст\n"
                "🔁 <b>.spam текст количество</b> — спам сообщениями\n"
            )
            await event.respond(help_text, parse_mode='html')
            logger.info("Отправлено сообщение с помощью")

        elif text == '.stop':
            user_states[tg_user_id] = 'stopped'
            await event.respond("⛔️ Бот остановлен. Используйте .start чтобы снова включить.")
            logger.info(f"Бот остановлен для пользователя {tg_user_id}")

        elif text == '.start':
            user_states[tg_user_id] = 'active'
            await event.respond("✅ Бот снова активен!")
            logger.info(f"Бот активирован для пользователя {tg_user_id}")

        elif text == '.number':
            me = await client.get_me()
            phone = me.phone if me.phone else "Не указан"
            await event.respond(f"📱 Ваш номер: {phone}")
            logger.info(f"Запрос номера для {tg_user_id}")

        elif text == '.ses':
            try:
                auths = await client(GetAuthorizationsRequest())
                sessions = "\n".join([f"{i+1}. {auth.device_model} ({auth.ip})" for i, auth in enumerate(auths.authorizations)])
                await event.respond(f"📡 Активные сессии:\n{sessions}")
                logger.info(f"Запрос сессий для {tg_user_id}")
            except Exception as e:
                await event.respond(f"❌ Ошибка: {e}")
                logger.error(f"Ошибка получения сессий: {e}")

        elif text == '.fun':
            try:
                await event.respond("⏳ Ищу интересный контент...")
                
                # Получаем случайное медиа из канала
                media_path = await get_random_media_from_channel(client, FUN_CHANNEL)
                
                if media_path:
                    # Определяем тип медиа
                    if media_path.endswith(('.jpg', '.jpeg', '.png')):
                        await client.send_file(event.chat_id, media_path)
                    elif media_path.endswith(('.mp4', '.mov', '.gif')):
                        await client.send_file(event.chat_id, media_path, supports_streaming=True)
                    
                    # Удаляем временный файл
                    os.remove(media_path)
                    logger.info(f"Отправлен медиафайл для {tg_user_id}")
                else:
                    await event.respond("❌ Не удалось найти медиафайлы в канале.")
            except Exception as e:
                await event.respond(f"❌ Ошибка: {e}")
                logger.error(f"Ошибка при обработке .fun: {e}")

        elif text.startswith('.id '):
            try:
                username = text.split(' ')[1]
                user_id = await get_user_id(client, username)
                if user_id:
                    await event.respond(f"🆔 ID пользователя {username}: {user_id}")
                else:
                    await event.respond(f"❌ Не удалось найти пользователя {username}")
            except Exception as e:
                await event.respond(f"❌ Ошибка: {e}")
                logger.error(f"Ошибка при обработке .id: {e}")

        elif text.startswith('.send '):
            try:
                parts = text.split(' ', 2)
                if len(parts) < 3:
                    await event.respond("❌ Неверный формат. Используйте: .send сообщение @username")
                    return
                
                message = parts[1]
                username = parts[2]
                user_id = await get_user_id(client, username)
                
                if user_id:
                    await client.send_message(user_id, message)
                    await event.respond(f"✅ Сообщение отправлено пользователю {username}")
                else:
                    await event.respond(f"❌ Не удалось найти пользователя {username}")
            except Exception as e:
                await event.respond(f"❌ Ошибка: {e}")
                logger.error(f"Ошибка при обработке .send: {e}")

        elif text == '.sleep':
            sleep_messages = [
                "💤 Сладких снов!",
                "🌙 Доброй ночи!",
                "🛏️ Отдыхай хорошо!",
                "😴 Спи крепко!",
                "✨ Пусть сны будут приятными!"
            ]
            await event.respond(random.choice(sleep_messages))

        elif text.startswith('.spam '):
            try:
                parts = text.split(' ')
                if len(parts) < 3:
                    await event.respond("❌ Неверный формат. Используйте: .spam текст количество")
                    return
                
                message = ' '.join(parts[1:-1])
                count = int(parts[-1])
                
                if count > 20:
                    await event.respond("❌ Максимальное количество сообщений - 20")
                    return
                
                for i in range(count):
                    await event.respond(message)
                    await asyncio.sleep(0.5)
                
                await event.respond(f"✅ Отправлено {count} сообщений")
            except Exception as e:
                await event.respond(f"❌ Ошибка: {e}")
                logger.error(f"Ошибка при обработке .spam: {e}")

        elif text.startswith('.anim '):
            try:
                text_to_animate = text[6:]
                if not text_to_animate:
                    await event.respond("❌ Укажите текст для анимации")
                    return
                
                animated_text = ""
                for i, char in enumerate(text_to_animate):
                    animated_text += char
                    await event.respond(animated_text)
                    await asyncio.sleep(0.3)
            except Exception as e:
                await event.respond(f"❌ Ошибка: {e}")
                logger.error(f"Ошибка при обработке .anim: {e}")

    try:
        if not client.is_connected():
            await client.connect()
        
        if not await client.is_user_authorized():
            logger.error(f"Клиент не авторизован для {tg_user_id}")
            await bot.send_message(chat_id, "❌ Ошибка: сессия не авторизована. Попробуйте заново.")
            return
        
        await client.start()
        logger.info(f"Клиент успешно запущен для {tg_user_id}")
        await bot.send_message(chat_id, "🔌 Слушатель сообщений активирован!")
    except Exception as e:
        logger.error(f"Ошибка запуска клиента: {e}")
        await bot.send_message(chat_id, f"❌ Ошибка запуска слушателя: {e}")

def main():
    app = ApplicationBuilder().token(API_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    logger.info("Бот запущен...")
    app.run_polling()

if __name__ == '__main__':
    main()
