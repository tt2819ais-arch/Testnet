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
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import asyncio
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Создаем директории
Path("sessions").mkdir(exist_ok=True)
Path("config").mkdir(exist_ok=True)

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
        self.me = None
    
    async def connect(self):
        """Подключение клиента"""
        try:
            await self.client.connect()
            
            if not await self.client.is_user_authorized():
                return False, "Не авторизован. Используйте /start для настройки"
            
            self.me = await self.client.get_me()
            return True, f"✅ Подключен как {self.me.first_name}"
            
        except Exception as e:
            return False, f"❌ Ошибка подключения: {str(e)}"
    
    async def disconnect(self):
        """Отключение клиента"""
        try:
            await self.client.disconnect()
            return True, "✅ Отключен"
        except Exception as e:
            return False, f"❌ Ошибка отключения: {str(e)}"
    
    async def start_online_keeping(self):
        """Запуск поддержания онлайн статуса"""
        if self.running:
            return "User-bot уже запущен"
        
        try:
            # Проверяем подключение
            if not self.client.is_connected():
                success, msg = await self.connect()
                if not success:
                    return msg
            
            self.running = True
            self.task = asyncio.create_task(self._keep_online_loop())
            
            return f"✅ User-bot запущен для {self.me.first_name}"
            
        except Exception as e:
            return f"❌ Ошибка запуска: {str(e)}"
    
    async def stop_online_keeping(self):
        """Остановка поддержания онлайн статуса"""
        if not self.running:
            return "User-bot не запущен"
        
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        
        return "✅ User-bot остановлен"
    
    async def _keep_online_loop(self):
        """Цикл поддержания онлайн статуса"""
        logger.info("Запуск цикла поддержания онлайн статуса...")
        
        try:
            while self.running:
                # Устанавливаем онлайн
                try:
                    await self.client(UpdateStatusRequest(offline=False))
                    logger.info("Статус: Онлайн")
                except Exception as e:
                    logger.error(f"Ошибка установки онлайн: {e}")
                
                # Ждем онлайн-период
                online_time = self.online_minutes * 60
                deviation = random.uniform(-0.2, 0.2)
                actual_online_time = online_time * (1 + deviation)
                
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
                try:
                    await self.client(UpdateStatusRequest(offline=True))
                    logger.info("Статус: Оффлайн")
                except Exception as e:
                    logger.error(f"Ошибка установки оффлайн: {e}")
                
                # Ждем оффлайн-период
                offline_time = self.offline_minutes * 60
                for i in range(offline_time):
                    if not self.running:
                        break
                    await asyncio.sleep(1)
                    
        except asyncio.CancelledError:
            logger.info("Цикл поддержания онлайн остановлен")
        except Exception as e:
            logger.error(f"Ошибка в цикле: {e}")
            self.running = False

# FSM состояния для настройки
class AuthStates(StatesGroup):
    waiting_api_id = State()
    waiting_api_hash = State()
    waiting_phone = State()
    waiting_code = State()
    waiting_password = State()

# Глобальные объекты
config_manager = ConfigManager()
userbot = None
router = Router()

def get_main_keyboard():
    """Клавиатура основного меню"""
    keyboard = [
        [KeyboardButton(text="🚀 Запустить user-bot")],
        [KeyboardButton(text="🛑 Остановить user-bot")],
        [KeyboardButton(text="⚙️ Перенастроить")],
        [KeyboardButton(text="📊 Статус")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

@router.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    config = config_manager.load_config()
    
    if config:
        global userbot
        if userbot is None:
            userbot = UserBotManager(config)
            success, msg = await userbot.connect()
            if not success:
                await message.answer(f"⚠️ {msg}", reply_markup=get_main_keyboard())
                return
        
        await message.answer(
            "👋 Добро пожаловать!\n"
            "User-bot настроен и готов к работе.\n"
            "Выберите действие:",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer(
            "👋 Добро пожаловать в настройку user-bot!\n\n"
            "Для начала вам нужно получить API ID и API Hash:\n"
            "1. Перейдите на https://my.telegram.org\n"
            "2. Войдите в свой аккаунт\n"
            "3. Перейдите в 'API Development Tools'\n"
            "4. Создайте приложение и получите данные\n\n"
            "Введите ваш API ID (только цифры):",
            reply_markup=ReplyKeyboardRemove()
        )
        await message.answer("Или нажмите /cancel для отмены")

@router.message(F.text == "🚀 Запустить user-bot")
async def start_userbot(message: Message):
    """Запуск user-bot"""
    if userbot is None:
        await message.answer("❌ User-bot не настроен. Используйте /start для настройки")
        return
    
    result = await userbot.start_online_keeping()
    await message.answer(result, reply_markup=get_main_keyboard())

@router.message(F.text == "🛑 Остановить user-bot")
async def stop_userbot(message: Message):
    """Остановка user-bot"""
    if userbot is None:
        await message.answer("❌ User-bot не настроен")
        return
    
    result = await userbot.stop_online_keeping()
    await message.answer(result, reply_markup=get_main_keyboard())

@router.message(F.text == "📊 Статус")
async def get_status(message: Message):
    """Получение статуса"""
    if userbot is None:
        await message.answer("❌ User-bot не настроен")
        return
    
    status = "✅ Запущен" if userbot.running else "⏸️ Остановлен"
    if userbot.me:
        await message.answer(
            f"📊 Статус user-bot:\n"
            f"• Состояние: {status}\n"
            f"• Аккаунт: {userbot.me.first_name}\n"
            f"• Онлайн: {userbot.online_minutes} мин\n"
            f"• Оффлайн: {userbot.offline_minutes} мин"
        )
    else:
        await message.answer(f"Статус: {status}")

@router.message(F.text == "⚙️ Перенастроить")
async def reconfigure(message: Message):
    """Перенастройка user-bot"""
    global userbot
    
    if userbot and userbot.running:
        await userbot.stop_online_keeping()
    
    userbot = None
    # Удаляем конфиг файл
    config_file = Path("config/userbot_config.enc")
    if config_file.exists():
        config_file.unlink()
    
    await message.answer(
        "🔄 Настройка сброшена.\n"
        "Для новой настройки используйте /start",
        reply_markup=ReplyKeyboardRemove()
    )

@router.message(F.text == "/cancel")
async def cancel_handler(message: Message, state: FSMContext):
    """Отмена текущего действия"""
    current_state = await state.get_state()
    if current_state is None:
        return
    
    await state.clear()
    await message.answer(
        "❌ Действие отменено",
        reply_markup=get_main_keyboard()
    )

async def main():
    """Основная функция"""
    # Получаем токен из переменных окружения
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logger.error("❌ Токен бота не установлен в переменных окружения!")
        logger.error("Добавьте TELEGRAM_BOT_TOKEN в .env файл")
        return
    
    # Инициализация бота
    bot = Bot(token=bot_token)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)
    
    # Загрузка существующей конфигурации
    config = config_manager.load_config()
    if config:
        global userbot
        userbot = UserBotManager(config)
        success, msg = await userbot.connect()
        if success:
            logger.info(f"✅ Автоматически подключен user-bot: {msg}")
        else:
            logger.warning(f"⚠️ Не удалось подключить user-bot: {msg}")
    
    logger.info("✅ Бот запущен")
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
