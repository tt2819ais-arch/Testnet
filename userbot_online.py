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

# Хранилище состояний пользователей
class UserStatesStorage:
    def __init__(self):
        self.states_file = Path("user_states.json")
        self.states = self.load_states()
    
    def load_states(self):
        if self.states_file.exists():
            try:
                with open(self.states_file, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def save_states(self):
        try:
            with open(self.states_file, 'w') as f:
                json.dump(self.states, f, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения состояний: {e}")
    
    def get_state(self, user_id):
        return self.states.get(str(user_id), {})
    
    def set_state(self, user_id, key, value):
        user_id = str(user_id)
        if user_id not in self.states:
            self.states[user_id] = {}
        self.states[user_id][key] = value
        self.save_states()
    
    def get_all(self, user_id):
        return self.states.get(str(user_id), {})
    
    def clear_state(self, user_id):
        user_id = str(user_id)
        if user_id in self.states:
            del self.states[user_id]
            self.save_states()

# Инициализация хранилища
user_states = UserStatesStorage()

class ConfigManager:
    """Менеджер конфигурации с шифрованием"""
    def __init__(self):
        self.config_dir = Path("config")
        self.config_file = self.config_dir / "userbot_config.json"
        
        # Создаем директорию если не существует
        self.config_dir.mkdir(exist_ok=True)
    
    def save_config(self, api_id, api_hash, phone, session_path):
        """Сохраняет конфигурацию"""
        config = {
            'api_id': api_id,
            'api_hash': api_hash,
            'phone': phone,
            'session_path': session_path
        }
        
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            logger.info("Конфигурация сохранена")
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения конфигурации: {e}")
            return False
    
    def load_config(self):
        """Загружает конфигурацию"""
        if not self.config_file.exists():
            return None
        
        try:
            with open(self.config_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки конфигурации: {e}")
            return None
    
    def delete_config(self):
        """Удаляет конфигурацию"""
        if self.config_file.exists():
            self.config_file.unlink()
            return True
        return False

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
                return False, "❌ Не авторизован. Используйте /setup для настройки"
            
            self.me = await self.client.get_me()
            return True, f"✅ Подключен как {self.me.first_name} (@{self.me.username})"
            
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
            return "⚠️ User-bot уже запущен"
        
        try:
            # Проверяем подключение
            if not self.client.is_connected():
                success, msg = await self.connect()
                if not success:
                    return msg
            
            self.running = True
            self.task = asyncio.create_task(self._keep_online_loop())
            
            return f"✅ User-bot запущен для {self.me.first_name}\nОнлайн: {self.online_minutes} мин, Оффлайн: {self.offline_minutes} мин"
            
        except Exception as e:
            return f"❌ Ошибка запуска: {str(e)}"
    
    async def stop_online_keeping(self):
        """Остановка поддержания онлайн статуса"""
        if not self.running:
            return "⚠️ User-bot не запущен"
        
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
        
        cycle_count = 0
        try:
            while self.running:
                cycle_count += 1
                
                # Устанавливаем онлайн
                try:
                    await self.client(UpdateStatusRequest(offline=False))
                    logger.info(f"[Цикл {cycle_count}] Статус: Онлайн")
                except Exception as e:
                    logger.error(f"Ошибка установки онлайн: {e}")
                
                # Ждем онлайн-период
                online_time = self.online_minutes * 60
                deviation = random.uniform(-0.2, 0.2)
                actual_online_time = online_time * (1 + deviation)
                
                for i in range(int(actual_online_time)):
                    if not self.running:
                        break
                    if i % 60 == 0:  # Логируем каждую минуту
                        remaining = (actual_online_time - i) / 60
                        logger.info(f"[Цикл {cycle_count}] Онлайн, осталось: {remaining:.1f} минут")
                    await asyncio.sleep(1)
                
                if not self.running:
                    break
                
                # Устанавливаем оффлайн
                try:
                    await self.client(UpdateStatusRequest(offline=True))
                    logger.info(f"[Цикл {cycle_count}] Статус: Оффлайн")
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

# Инициализация менеджеров
config_manager = ConfigManager()
userbot = None

# Роутер
router = Router()

def get_main_keyboard():
    """Клавиатура основного меню"""
    keyboard = [
        [KeyboardButton(text="🚀 Запустить")],
        [KeyboardButton(text="🛑 Остановить")],
        [KeyboardButton(text="⚙️ Перенастроить")],
        [KeyboardButton(text="📊 Статус")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_setup_keyboard():
    """Клавиатура для настройки"""
    keyboard = [
        [KeyboardButton(text="❌ Отмена")]
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
            await message.answer(msg)
        
        await message.answer(
            "👋 User-bot готов к работе!\n"
            "Выберите действие:",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer(
            "👋 Добро пожаловать!\n"
            "User-bot не настроен.\n"
            "Используйте /setup для настройки.",
            reply_markup=get_main_keyboard()
        )

@router.message(Command("setup"))
async def cmd_setup(message: Message):
    """Начало настройки"""
    user_states.clear_state(message.from_user.id)
    user_states.set_state(message.from_user.id, "step", "waiting_api_id")
    
    await message.answer(
        "⚙️ Настройка user-bot\n\n"
        "1. Перейдите на https://my.telegram.org\n"
        "2. Войдите в свой аккаунт\n"
        "3. Перейдите в 'API Development Tools'\n"
        "4. Создайте приложение\n\n"
        "Введите ваш API ID (только цифры):",
        reply_markup=get_setup_keyboard()
    )

@router.message(F.text == "❌ Отмена")
async def cancel_setup(message: Message):
    """Отмена настройки"""
    user_states.clear_state(message.from_user.id)
    await message.answer(
        "Настройка отменена.",
        reply_markup=get_main_keyboard()
    )

@router.message(F.text == "🚀 Запустить")
async def start_userbot(message: Message):
    """Запуск user-bot"""
    if userbot is None:
        await message.answer("❌ User-bot не настроен. Используйте /setup")
        return
    
    result = await userbot.start_online_keeping()
    await message.answer(result, reply_markup=get_main_keyboard())

@router.message(F.text == "🛑 Остановить")
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
    
    status = "🟢 Запущен" if userbot.running else "🔴 Остановлен"
    if userbot.me:
        await message.answer(
            f"📊 Статус:\n"
            f"• Состояние: {status}\n"
            f"• Аккаунт: {userbot.me.first_name}\n"
            f"• Username: @{userbot.me.username}\n"
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
    config_manager.delete_config()
    
    # Удаляем сессии
    sessions_dir = Path("sessions")
    for file in sessions_dir.glob("*.session"):
        file.unlink()
    
    await message.answer(
        "🔄 Конфигурация удалена.\n"
        "Используйте /setup для новой настройки",
        reply_markup=get_main_keyboard()
    )

@router.message()
async def handle_messages(message: Message):
    """Обработка всех сообщений"""
    user_id = message.from_user.id
    user_state = user_states.get_all(user_id)
    
    if not user_state:
        await message.answer("Выберите действие:", reply_markup=get_main_keyboard())
        return
    
    step = user_state.get("step")
    text = message.text
    
    if step == "waiting_api_id":
        if text.isdigit():
            user_states.set_state(user_id, "api_id", text)
            user_states.set_state(user_id, "step", "waiting_api_hash")
            await message.answer("✅ API ID сохранен\nВведите API Hash:")
        else:
            await message.answer("❌ API ID должен содержать только цифры. Попробуйте еще раз:")
    
    elif step == "waiting_api_hash":
        user_states.set_state(user_id, "api_hash", text)
        user_states.set_state(user_id, "step", "waiting_phone")
        await message.answer("✅ API Hash сохранен\nВведите номер телефона (в формате +79991234567):")
    
    elif step == "waiting_phone":
        user_states.set_state(user_id, "phone", text)
        
        # Получаем данные
        api_id = user_state.get("api_id")
        api_hash = user_state.get("api_hash")
        phone = text
        
        # Создаем временный клиент
        temp_session = f"sessions/temp_{user_id}"
        temp_client = TelegramClient(temp_session, int(api_id), api_hash)
        
        try:
            await temp_client.connect()
            sent_code = await temp_client.send_code_request(phone)
            
            user_states.set_state(user_id, "temp_client", {
                "api_id": api_id,
                "api_hash": api_hash,
                "phone": phone,
                "phone_code_hash": sent_code.phone_code_hash,
                "session": temp_session
            })
            user_states.set_state(user_id, "step", "waiting_code")
            
            await message.answer("✅ Код отправлен на ваш телефон\nВведите код подтверждения из Telegram:")
            
        except Exception as e:
            await message.answer(f"❌ Ошибка отправки кода: {str(e)}\nНачните заново с /setup")
            user_states.clear_state(user_id)
    
    elif step == "waiting_code":
        code = text.strip()
        client_data = user_state.get("temp_client", {})
        
        if not client_data:
            await message.answer("❌ Ошибка данных. Начните заново с /setup")
            user_states.clear_state(user_id)
            return
        
        temp_client = TelegramClient(
            client_data["session"],
            int(client_data["api_id"]),
            client_data["api_hash"]
        )
        
        try:
            await temp_client.connect()
            await temp_client.sign_in(
                phone=client_data["phone"],
                code=code,
                phone_code_hash=client_data["phone_code_hash"]
            )
            
            # Проверяем авторизацию
            if await temp_client.is_user_authorized():
                # Сохраняем конфигурацию
                session_path = f"sessions/{client_data['phone']}"
                config_manager.save_config(
                    client_data["api_id"],
                    client_data["api_hash"],
                    client_data["phone"],
                    session_path
                )
                
                # Копируем сессию
                import shutil
                temp_session_file = Path(f"{client_data['session']}.session")
                final_session_file = Path(f"{session_path}.session")
                
                if temp_session_file.exists():
                    shutil.copy2(temp_session_file, final_session_file)
                    temp_session_file.unlink()
                
                await temp_client.disconnect()
                
                # Инициализируем userbot
                global userbot
                userbot = UserBotManager({
                    'api_id': client_data["api_id"],
                    'api_hash': client_data["api_hash"],
                    'phone': client_data["phone"],
                    'session_path': session_path
                })
                
                success, msg = await userbot.connect()
                
                user_states.clear_state(user_id)
                
                await message.answer(
                    f"🎉 Настройка завершена!\n{msg}\n\n"
                    f"Теперь вы можете запустить user-bot.",
                    reply_markup=get_main_keyboard()
                )
                
            else:
                await message.answer("❌ Ошибка авторизации. Попробуйте снова с /setup")
                user_states.clear_state(user_id)
                
        except SessionPasswordNeededError:
            user_states.set_state(user_id, "step", "waiting_password")
            await message.answer("🔐 Требуется пароль двухфакторной аутентификации.\nВведите пароль 2FA:")
            
        except Exception as e:
            await message.answer(f"❌ Ошибка авторизации: {str(e)}\nНачните заново с /setup")
            user_states.clear_state(user_id)
    
    elif step == "waiting_password":
        password = text.strip()
        client_data = user_state.get("temp_client", {})
        
        if not client_data:
            await message.answer("❌ Ошибка данных. Начните заново с /setup")
            user_states.clear_state(user_id)
            return
        
        temp_client = TelegramClient(
            client_data["session"],
            int(client_data["api_id"]),
            client_data["api_hash"]
        )
        
        try:
            await temp_client.connect()
            await temp_client.sign_in(password=password)
            
            if await temp_client.is_user_authorized():
                # Сохраняем конфигурацию
                session_path = f"sessions/{client_data['phone']}"
                config_manager.save_config(
                    client_data["api_id"],
                    client_data["api_hash"],
                    client_data["phone"],
                    session_path
                )
                
                # Копируем сессию
                import shutil
                temp_session_file = Path(f"{client_data['session']}.session")
                final_session_file = Path(f"{session_path}.session")
                
                if temp_session_file.exists():
                    shutil.copy2(temp_session_file, final_session_file)
                    temp_session_file.unlink()
                
                await temp_client.disconnect()
                
                # Инициализируем userbot
                global userbot
                userbot = UserBotManager({
                    'api_id': client_data["api_id"],
                    'api_hash': client_data["api_hash"],
                    'phone': client_data["phone"],
                    'session_path': session_path
                })
                
                success, msg = await userbot.connect()
                
                user_states.clear_state(user_id)
                
                await message.answer(
                    f"🎉 Настройка завершена!\n{msg}\n\n"
                    f"Теперь вы можете запустить user-bot.",
                    reply_markup=get_main_keyboard()
                )
            else:
                await message.answer("❌ Ошибка авторизации. Попробуйте снова с /setup")
                user_states.clear_state(user_id)
                
        except Exception as e:
            await message.answer(f"❌ Ошибка ввода пароля: {str(e)}\nНачните заново с /setup")
            user_states.clear_state(user_id)

async def main():
    """Основная функция"""
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logger.error("❌ Токен бота не установлен!")
        logger.error("Создайте .env файл с TELEGRAM_BOT_TOKEN=ваш_токен")
        return
    
    # Загрузка существующей конфигурации
    config = config_manager.load_config()
    if config:
        global userbot
        userbot = UserBotManager(config)
        success, msg = await userbot.connect()
        if success:
            logger.info(f"✅ Автоматически подключен: {msg}")
        else:
            logger.warning(f"⚠️ Не удалось подключить: {msg}")
    
    # Инициализация бота
    bot = Bot(token=bot_token)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)
    
    logger.info("✅ Бот запущен и готов к работе")
    
    # Удаляем webhook и запускаем polling
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
