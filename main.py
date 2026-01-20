"""
Telegram AI User Bot - авторизация через Telegram бота
"""

import asyncio
import json
import logging
import os
import sys
from typing import Dict, List, Optional, Set
import aiohttp
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from telethon import TelegramClient, events
from telethon.tl.types import Message as TLMessage, User
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneNumberUnoccupiedError,
    FloodWaitError
)

# ========== НАСТРОЙКА ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('userbot.log')
    ]
)
logger = logging.getLogger(__name__)

# ========== ПРЯМЫЕ ЗНАЧЕНИЯ ==========
BOT_TOKEN = "7802806814:AAEymolTEcHxNUnUoscRDDOQ2mpMmAtS0hg"  # Токен вашего бота
API_ID = 22435995  # Ваш API_ID
API_HASH = "4c7b651950ed7f53520e66299453144d"  # Ваш API_HASH
OPENROUTER_API_KEY = "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"  # Ваш ключ

MODEL = "xiaomi/mimo-v2-flash:free"
SESSION_FILE = "userbot_session"

# ========== ИНИЦИАЛИЗАЦИЯ AIOGRAM ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ========== ХРАНЕНИЕ ДАННЫХ ==========
class AISession:
    """Сессия AI для личного чата"""
    def __init__(self, user_id: int, username: str = ""):
        self.user_id = user_id
        self.username = username
        self.active = False
        self.messages: List[Dict] = []
        self.reasoning_details: Optional[Dict] = None
        self.last_activity = datetime.now()
    
    def activate(self):
        self.active = True
        self.messages = []
        self.reasoning_details = None
        self.last_activity = datetime.now()
        logger.info(f"AI активирован для пользователя {self.user_id}")
    
    def deactivate(self):
        self.active = False
        logger.info(f"AI деактивирован для пользователя {self.user_id}")
    
    def add_message(self, role: str, content: str, reasoning_details: Dict = None):
        if len(self.messages) > 15:
            self.messages = self.messages[-14:]
        
        message = {"role": role, "content": content}
        if role == "assistant" and reasoning_details:
            message["reasoning_details"] = reasoning_details
        
        self.messages.append(message)
        self.last_activity = datetime.now()
    
    def get_messages(self):
        return self.messages.copy()

# Глобальные хранилища
ai_sessions: Dict[int, AISession] = {}
telethon_clients: Dict[int, TelegramClient] = {}  # user_id -> TelethonClient
auth_data: Dict[int, Dict] = {}  # user_id -> данные для авторизации

# ========== FSM ДЛЯ АВТОРИЗАЦИИ ==========
class AuthStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()
    authorized = State()

# ========== AI ФУНКЦИИ ==========
async def make_ai_request(session: AISession, user_message: str) -> str:
    """Запрос к OpenRouter API"""
    try:
        session.add_message("user", user_message)
        messages = session.get_messages()
        
        if (session.reasoning_details and 
            messages and 
            messages[-1].get("role") == "assistant"):
            messages[-1]["reasoning_details"] = session.reasoning_details
        
        payload = {
            "model": MODEL,
            "messages": messages,
            "reasoning": {"enabled": True}
        }
        
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30
            ) as response:
                
                if response.status != 200:
                    return "⚠️ Ошибка при обращении к AI сервису."
                
                data = await response.json()
                
                if 'choices' not in data or not data['choices']:
                    return "⚠️ Неверный ответ от AI."
                
                ai_message = data['choices'][0]['message']
                content = ai_message.get('content', '')
                reasoning_details = ai_message.get('reasoning_details')
                
                session.reasoning_details = reasoning_details
                session.add_message("assistant", content, reasoning_details)
                
                return content
                
    except Exception as e:
        logger.error(f"AI request error: {e}")
        return f"⚠️ Ошибка: {str(e)[:100]}"

# ========== ОБРАБОТЧИКИ TELEGRAM БОТА ==========
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Начало авторизации через бота"""
    user_id = message.from_user.id
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить номер телефона", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
    await message.answer(
        "👋 Для работы AI User Bot нужно авторизоваться в Telegram\n\n"
        "1. Нажмите кнопку ниже, чтобы поделиться номером\n"
        "2. Я пришлю код из Telegram\n"
        "3. Введите код для завершения\n\n"
        "⚠️ Используйте СВОЙ номер телефона!",
        reply_markup=keyboard
    )
    
    await state.set_state(AuthStates.waiting_for_phone)

@router.message(AuthStates.waiting_for_phone, F.contact)
async def process_phone(message: Message, state: FSMContext):
    """Обработка номера телефона"""
    user_id = message.from_user.id
    contact = message.contact
    
    if not contact or not contact.phone_number:
        await message.answer("Не удалось получить номер. Попробуйте еще раз.")
        return
    
    phone = contact.phone_number
    logger.info(f"Пользователь {user_id} отправил номер: {phone}")
    
    # Создаем Telethon клиент
    session_file = f"session_{user_id}"
    client = TelegramClient(session_file, API_ID, API_HASH)
    
    try:
        await client.connect()
        sent_code = await client.send_code_request(phone)
        
        # Сохраняем данные
        auth_data[user_id] = {
            'phone': phone,
            'client': client,
            'phone_code_hash': sent_code.phone_code_hash
        }
        
        await state.update_data(
            phone=phone,
            phone_code_hash=sent_code.phone_code_hash
        )
        
        await message.answer(
            f"✅ Код отправлен на {phone}\n\n"
            f"📨 Введите код из Telegram (5 цифр):",
            reply_markup=ReplyKeyboardRemove()
        )
        
        await state.set_state(AuthStates.waiting_for_code)
        
    except PhoneNumberUnoccupiedError:
        await message.answer("❌ Этот номер не зарегистрирован в Telegram.")
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка отправки кода: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")
        await state.clear()

@router.message(AuthStates.waiting_for_code)
async def process_code(message: Message, state: FSMContext):
    """Обработка кода подтверждения"""
    user_id = message.from_user.id
    code = ''.join(filter(str.isdigit, message.text))
    
    if len(code) != 5:
        await message.answer("❌ Код должен быть из 5 цифр. Попробуйте еще раз:")
        return
    
    if user_id not in auth_data:
        await message.answer("❌ Сессия устарела. Начните заново /start")
        await state.clear()
        return
    
    data = auth_data[user_id]
    client = data['client']
    phone = data['phone']
    
    try:
        await client.sign_in(
            phone=phone,
            code=code,
            phone_code_hash=data['phone_code_hash']
        )
        
        # Успешная авторизация!
        await message.answer(
            "✅ Авторизация успешна!\n\n"
            "Теперь AI User Bot подключен к вашему аккаунту.\n\n"
            "📝 Как использовать:\n"
            "1. AI будет работать в ваших ЛИЧНЫХ ЧАТАХ\n"
            "2. В нужном чате напишите `.старт`\n"
            "3. AI начнет отвечать на сообщения\n"
            "4. Для отключения напишите `.стоп`\n\n"
            "⚠️ Работает ТОЛЬКО в личных чатах!"
        )
        
        # Сохраняем клиент
        telethon_clients[user_id] = client
        
        # Запускаем обработчик сообщений для этого пользователя
        asyncio.create_task(start_user_message_handler(client, user_id))
        
        await state.set_state(AuthStates.authorized)
        
    except SessionPasswordNeededError:
        await message.answer("🔐 Введите пароль от вашего аккаунта Telegram:")
        await state.set_state(AuthStates.waiting_for_password)
    except PhoneCodeInvalidError:
        await message.answer("❌ Неверный код. Попробуйте еще раз:")
    except Exception as e:
        logger.error(f"Ошибка авторизации: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")
        await state.clear()

@router.message(AuthStates.waiting_for_password)
async def process_password(message: Message, state: FSMContext):
    """Обработка пароля 2FA"""
    user_id = message.from_user.id
    password = message.text
    
    if user_id not in auth_data:
        await message.answer("❌ Сессия устарела. Начните заново /start")
        await state.clear()
        return
    
    client = auth_data[user_id]['client']
    
    try:
        await client.sign_in(password=password)
        
        await message.answer(
            "✅ Авторизация с 2FA успешна!\n\n"
            "AI User Bot теперь подключен к вашему аккаунту.\n"
            "Используйте `.старт` в личных чатах для активации AI."
        )
        
        telethon_clients[user_id] = client
        asyncio.create_task(start_user_message_handler(client, user_id))
        
        await state.set_state(AuthStates.authorized)
        
    except Exception as e:
        logger.error(f"Ошибка 2FA: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")
        await state.clear()

@router.message(Command("status"))
async def cmd_status(message: Message):
    """Статус"""
    user_id = message.from_user.id
    
    if user_id in telethon_clients:
        active_chats = sum(1 for s in ai_sessions.values() if s.active)
        total_chats = len(ai_sessions)
        
        status_text = f"""
📊 Статус AI User Bot:

• Авторизация: ✅ Активна
• Активных чатов: {active_chats}
• Всего чатов: {total_chats}
• Модель: {MODEL}
        """
    else:
        status_text = "❌ Вы не авторизованы. Используйте /start"
    
    await message.answer(status_text)

@router.message(Command("logout"))
async def cmd_logout(message: Message, state: FSMContext):
    """Выход"""
    user_id = message.from_user.id
    
    if user_id in telethon_clients:
        try:
            await telethon_clients[user_id].disconnect()
        except:
            pass
        
        telethon_clients.pop(user_id, None)
        auth_data.pop(user_id, None)
        
        # Удаляем AI сессии этого пользователя
        keys_to_remove = [k for k in ai_sessions.keys() if k == user_id]
        for k in keys_to_remove:
            ai_sessions.pop(k, None)
    
    await message.answer("✅ Вы вышли из аккаунта. Для входа используйте /start")
    await state.clear()

# ========== ОБРАБОТЧИК СООБЩЕНИЙ ДЛЯ USER BOT ==========
async def start_user_message_handler(client: TelegramClient, user_id: int):
    """Запуск обработчика сообщений для пользователя"""
    
    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def handler(event):
        """Обработка личных сообщений"""
        try:
            if event.message.out:
                return
            
            sender = await event.get_sender()
            chat_user_id = sender.id
            username = getattr(sender, 'username', '') or getattr(sender, 'first_name', 'Неизвестно')
            message_text = event.message.text
            
            if not message_text:
                return
            
            # Создаем AI сессию если нужно
            if chat_user_id not in ai_sessions:
                ai_sessions[chat_user_id] = AISession(chat_user_id, username)
            
            session = ai_sessions[chat_user_id]
            
            # Команды
            if message_text.lower() == ".старт":
                if not session.active:
                    session.activate()
                    await event.reply(
                        "✅ AI помощник активирован в этом чате!\n\n"
                        "Теперь я буду автоматически отвечать на ваши сообщения.\n"
                        "Для отключения напишите `.стоп`"
                    )
                else:
                    await event.reply("✅ AI уже активен в этом чате!")
                return
            
            elif message_text.lower() == ".стоп":
                if session.active:
                    session.deactivate()
                    await event.reply("❌ AI помощник отключен в этом чате.")
                else:
                    await event.reply("AI помощник и так не активен.")
                return
            
            elif message_text.lower() == ".сброс":
                session.messages = []
                session.reasoning_details = None
                await event.reply("🔄 История диалога сброшена!")
                return
            
            # Если AI не активен - игнорируем
            if not session.active:
                return
            
            # Получаем ответ от AI
            async with client.action(event.chat_id, 'typing'):
                ai_response = await make_ai_request(session, message_text)
                await event.reply(ai_response)
                
        except Exception as e:
            logger.error(f"Ошибка в обработчике: {e}")
    
    # Запускаем клиент
    await client.start()
    logger.info(f"User bot запущен для пользователя {user_id}")

# ========== ЗАПУСК ==========
async def main():
    """Основная функция"""
    logger.info("="*50)
    logger.info("🤖 Telegram AI User Bot запускается...")
    logger.info(f"Бот токен: {BOT_TOKEN[:10]}...")
    logger.info(f"API ID: {API_ID}")
    logger.info("="*50)
    
    try:
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"Ошибка запуска: {e}")
    finally:
        logger.info("Бот остановлен")

if __name__ == "__main__":
    asyncio.run(main())
