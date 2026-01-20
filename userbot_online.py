import asyncio
import logging
import random
import json
import os
from datetime import datetime
from pathlib import Path
from cryptography.fernet import Fernet
from telethon import TelegramClient
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from telethon.tl.functions.account import UpdateStatusRequest
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ConfigManager:
    """Менеджер конфигурации с шифрованием"""
    def __init__(self):
        self.config_dir = Path("config")
        self.config_file = self.config_dir / "userbot_config.enc"
        self.key_file = self.config_dir / "key.key"
        
        # Создаем директорию если не существует
        self.config_dir.mkdir(exist_ok=True)
        
        # Генерируем или загружаем ключ шифрования
        self._load_or_generate_key()
    
    def _load_or_generate_key(self):
        """Загружает или генерирует ключ шифрования"""
        if self.key_file.exists():
            with open(self.key_file, 'rb') as f:
                self.key = f.read()
        else:
            self.key = Fernet.generate_key()
            with open(self.key_file, 'wb') as f:
                f.write(self.key)
        
        self.cipher = Fernet(self.key)
    
    def save_config(self, api_id, api_hash, phone, session_path):
        """Сохраняет конфигурацию с шифрованием"""
        config = {
            'api_id': api_id,
            'api_hash': api_hash,
            'phone': phone,
            'session_path': session_path
        }
        
        encrypted_data = self.cipher.encrypt(json.dumps(config).encode())
        
        with open(self.config_file, 'wb') as f:
            f.write(encrypted_data)
        
        logger.info("Конфигурация сохранена и зашифрована")
    
    def load_config(self):
        """Загружает и расшифровывает конфигурацию"""
        if not self.config_file.exists():
            return None
        
        try:
            with open(self.config_file, 'rb') as f:
                encrypted_data = f.read()
            
            decrypted_data = self.cipher.decrypt(encrypted_data)
            return json.loads(decrypted_data.decode())
        except Exception as e:
            logger.error(f"Ошибка загрузки конфигурации: {e}")
            return None

class UserBotManager:
    """Управление user-bot"""
    def __init__(self, config):
        self.api_id = int(config['api_id'])
        self.api_hash = config['api_hash']
        self.phone = config['phone']
        self.session_path = config['session_path']
        
        self.client = TelegramClient(
            self.session_path,
            self.api_id,
            self.api_hash
        )
        
        self.online_minutes = 5
        self.offline_minutes = 1
        self.running = False
        self.task = None
    
    async def start(self):
        """Запуск user-bot"""
        if self.running:
            return "User-bot уже запущен"
        
        try:
            await self.client.connect()
            
            if not await self.client.is_user_authorized():
                return "Не авторизован. Используйте /start для настройки"
            
            self.running = True
            self.task = asyncio.create_task(self._keep_online_loop())
            
            me = await self.client.get_me()
            return f"✅ User-bot запущен для {me.first_name} (@{me.username})"
            
        except Exception as e:
            return f"❌ Ошибка запуска: {str(e)}"
    
    async def stop(self):
        """Остановка user-bot"""
        if not self.running:
            return "User-bot не запущен"
        
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        
        await self.client.disconnect()
        return "✅ User-bot остановлен"
    
    async def _keep_online_loop(self):
        """Цикл поддержания онлайн статуса"""
        logger.info("Запуск цикла поддержания онлайн статуса...")
        
        try:
            while self.running:
                # Устанавливаем онлайн
                await self.client(UpdateStatusRequest(offline=False))
                logger.info("Статус: Онлайн")
                
                # Ждем онлайн-период
                online_time = self.online_minutes * 60
                deviation = random.uniform(-0.2, 0.2)
                actual_online_time = online_time * (1 + deviation)
                
                # Счетчик с периодическим логированием
                for i in range(int(actual_online_time)):
                    if not self.running:
                        break
                    if i % 30 == 0:  # Логируем каждые 30 секунд
                        remaining = (actual_online_time - i) / 60
                        logger.info(f"Осталось онлайн: {remaining:.1f} минут")
                    await asyncio.sleep(1)
                
                if not self.running:
                    break
                
                # Устанавливаем оффлайн
                await self.client(UpdateStatusRequest(offline=True))
                logger.info("Статус: Оффлайн")
                
                # Ждем оффлайн-период
                offline_time = self.offline_minutes * 60
                for i in range(offline_time):
                    if not self.running:
                        break
                    await asyncio.sleep(1)
                    
        except Exception as e:
            logger.error(f"Ошибка в цикле: {e}")
            self.running = False

class TelegramBotHandler:
    """Обработчик Telegram бота для настройки"""
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.config_manager = ConfigManager()
        self.userbot = None
        self.pending_auth = {}  # {user_id: {'api_id': '', 'api_hash': '', 'phone': ''}}
        
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user_id = update.effective_user.id
        
        # Проверяем существующую конфигурацию
        config = self.config_manager.load_config()
        
        if config:
            keyboard = [
                [KeyboardButton("🚀 Запустить user-bot")],
                [KeyboardButton("🛑 Остановить user-bot")],
                [KeyboardButton("⚙️ Перенастроить")]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            await update.message.reply_text(
                "✅ Конфигурация найдена!\n"
                "Выберите действие:",
                reply_markup=reply_markup
            )
            
            # Создаем экземпляр userbot
            self.userbot = UserBotManager(config)
        else:
            await self._start_setup(update)
    
    async def _start_setup(self, update: Update):
        """Начало процесса настройки"""
        await update.message.reply_text(
            "👋 Добро пожаловать в настройку user-bot!\n\n"
            "Для начала вам нужно получить API ID и API Hash:\n"
            "1. Перейдите на https://my.telegram.org\n"
            "2. Войдите в свой аккаунт\n"
            "3. Перейдите в 'API Development Tools'\n"
            "4. Создайте приложение и получите данные\n\n"
            "Введите ваш API ID (только цифры):"
        )
        self.pending_auth[update.effective_user.id] = {}
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик текстовых сообщений"""
        user_id = update.effective_user.id
        text = update.message.text
        
        # Обработка кнопок
        if text == "🚀 Запустить user-bot" and self.userbot:
            result = await self.userbot.start()
            await update.message.reply_text(result)
            return
        elif text == "🛑 Остановить user-bot" and self.userbot:
            result = await self.userbot.stop()
            await update.message.reply_text(result)
            return
        elif text == "⚙️ Перенастроить":
            await self._start_setup(update)
            return
        
        # Процесс настройки
        if user_id in self.pending_auth:
            config = self.pending_auth[user_id]
            
            if 'api_id' not in config:
                try:
                    config['api_id'] = text.strip()
                    await update.message.reply_text("✅ API ID сохранен\nВведите API Hash:")
                except ValueError:
                    await update.message.reply_text("❌ API ID должен содержать только цифры. Попробуйте еще раз:")
                    
            elif 'api_hash' not in config:
                config['api_hash'] = text.strip()
                await update.message.reply_text("✅ API Hash сохранен\nВведите номер телефона (в международном формате, например +79991234567):")
                
            elif 'phone' not in config:
                config['phone'] = text.strip()
                
                # Запрашиваем код подтверждения
                try:
                    # Создаем временный клиент для авторизации
                    temp_client = TelegramClient(
                        f"sessions/temp_{user_id}",
                        int(config['api_id']),
                        config['api_hash']
                    )
                    
                    await temp_client.connect()
                    sent_code = await temp_client.send_code_request(config['phone'])
                    
                    self.pending_auth[user_id]['client'] = temp_client
                    self.pending_auth[user_id]['phone_code_hash'] = sent_code.phone_code_hash
                    
                    await update.message.reply_text(
                        "✅ Код отправлен на ваш телефон\n"
                        "Введите код подтверждения:"
                    )
                    
                except Exception as e:
                    await update.message.reply_text(f"❌ Ошибка отправки кода: {str(e)}")
                    del self.pending_auth[user_id]
                    
            elif 'phone_code_hash' in config and 'code' not in config:
                # Ввод кода подтверждения
                try:
                    temp_client = config['client']
                    
                    await temp_client.sign_in(
                        phone=config['phone'],
                        code=text.strip(),
                        phone_code_hash=config['phone_code_hash']
                    )
                    
                    # Проверяем если нужен пароль 2FA
                    if await temp_client.is_user_authorized():
                        # Сохраняем конфигурацию
                        session_path = f"sessions/{config['phone']}"
                        self.config_manager.save_config(
                            config['api_id'],
                            config['api_hash'],
                            config['phone'],
                            session_path
                        )
                        
                        # Закрываем временный клиент
                        await temp_client.disconnect()
                        
                        # Создаем основной userbot
                        self.userbot = UserBotManager({
                            'api_id': config['api_id'],
                            'api_hash': config['api_hash'],
                            'phone': config['phone'],
                            'session_path': session_path
                        })
                        
                        del self.pending_auth[user_id]
                        
                        keyboard = [
                            [KeyboardButton("🚀 Запустить user-bot")],
                            [KeyboardButton("⚙️ Перенастроить")]
                        ]
                        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                        
                        await update.message.reply_text(
                            "🎉 Авторизация успешна!\n"
                            "Теперь вы можете запустить user-bot.",
                            reply_markup=reply_markup
                        )
                    else:
                        await update.message.reply_text("❌ Ошибка авторизации. Попробуйте снова с /start")
                        
                except SessionPasswordNeededError:
                    await update.message.reply_text(
                        "🔐 Требуется пароль двухфакторной аутентификации.\n"
                        "Введите пароль 2FA:"
                    )
                    self.pending_auth[user_id]['need_password'] = True
                    
                except Exception as e:
                    await update.message.reply_text(f"❌ Ошибка авторизации: {str(e)}")
                    del self.pending_auth[user_id]
                    
            elif config.get('need_password'):
                # Ввод пароля 2FA
                try:
                    temp_client = config['client']
                    await temp_client.sign_in(password=text.strip())
                    
                    # Сохраняем конфигурацию
                    session_path = f"sessions/{config['phone']}"
                    self.config_manager.save_config(
                        config['api_id'],
                        config['api_hash'],
                        config['phone'],
                        session_path
                    )
                    
                    await temp_client.disconnect()
                    self.userbot = UserBotManager({
                        'api_id': config['api_id'],
                        'api_hash': config['api_hash'],
                        'phone': config['phone'],
                        'session_path': session_path
                    })
                    
                    del self.pending_auth[user_id]
                    
                    keyboard = [
                        [KeyboardButton("🚀 Запустить user-bot")],
                        [KeyboardButton("⚙️ Перенастроить")]
                    ]
                    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                    
                    await update.message.reply_text(
                        "🎉 Авторизация успешна!\n"
                        "Теперь вы можете запустить user-bot.",
                        reply_markup=reply_markup
                    )
                    
                except Exception as e:
                    await update.message.reply_text(f"❌ Ошибка ввода пароля: {str(e)}")
                    del self.pending_auth[user_id]

async def main():
    """Основная функция"""
    # Проверяем токен бота
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logger.error("Токен бота не установлен!")
        return
    
    # Создаем директории
    Path("sessions").mkdir(exist_ok=True)
    Path("config").mkdir(exist_ok=True)
    
    # Создаем обработчик
    bot_handler = TelegramBotHandler(bot_token)
    
    # Создаем приложение бота
    application = Application.builder().token(bot_token).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", bot_handler.start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_handler.handle_message))
    
    # Запускаем бота
    logger.info("Бот запущен...")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    asyncio.run(main())
