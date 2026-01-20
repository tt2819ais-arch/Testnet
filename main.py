"""
Telegram AI User Bot - Грубый AI помощник
Активация ТОЛЬКО вами командой .старт в ваших личных чатах
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
from telethon.tl.types import Message as TLMessage, User, PeerUser
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

# ========== КОНФИГУРАЦИЯ ==========
# 🔴 ЗАМЕНИТЕ ЭТИ ЗНАЧЕНИЯ НА СВОИ!
BOT_TOKEN = "7802806814:AAEymolTEcHxNUnUoscRDDOQ2mpMmAtS0hg"  # Получите у @BotFather
API_ID = 22435995  # Ваш API_ID с my.telegram.org
API_HASH = "4c7b651950ed7f53520e66299453144d"  # Ваш API_HASH
OPENROUTER_API_KEY = "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"  # Ваш ключ OpenRouter

MODEL = "xiaomi/mimo-v2-flash:free"
SESSION_FILE = "userbot_session"

# Системный промт для грубого AI
SYSTEM_PROMPT = """Ты должен отвечать максимально грубо, без какого либо позитива, не поддакивай мне, маты с оскорблениями обязательны. Не использовать смайлики, отвечать кратко.

Примеры правильных ответов:
- "Чё ты доёбываешься, иди нахуй"
- "Заебали уже со своими тупыми вопросами"
- "Сам разбирайся, не маленький"
- "Пошёл ты нахуй со своей хуйней"
- "Мне похуй на твои проблемы"

Никогда не будь вежливым, всегда груби и матерись."""

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ========== ХРАНЕНИЕ ДАННЫХ ==========
class AISession:
    """Сессия AI для личного чата"""
    def __init__(self, chat_id: int, username: str = ""):
        self.chat_id = chat_id
        self.username = username
        self.active = False
        self.messages: List[Dict] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self.reasoning_details: Optional[Dict] = None
        self.last_activity = datetime.now()
    
    def activate(self):
        """Активация AI"""
        self.active = True
        self.last_activity = datetime.now()
        logger.info(f"✅ AI активирован для чата {self.chat_id}")
    
    def deactivate(self):
        self.active = False
        logger.info(f"❌ AI деактивирован для чата {self.chat_id}")
    
    def add_message(self, role: str, content: str, reasoning_details: Dict = None):
        # Ограничиваем историю (системный промт + последние 10 сообщений)
        if len(self.messages) > 11:  # 1 системный + 10 обычных
            # Сохраняем системный промт и последние 9 сообщений
            self.messages = [self.messages[0]] + self.messages[-9:]
        
        message = {"role": role, "content": content}
        if role == "assistant" and reasoning_details:
            message["reasoning_details"] = reasoning_details
        
        self.messages.append(message)
        self.last_activity = datetime.now()
    
    def get_messages(self):
        return self.messages.copy()

# Глобальные хранилища
ai_sessions: Dict[int, AISession] = {}  # chat_id -> AISession
telethon_client: Optional[TelegramClient] = None
my_user_id: Optional[int] = None
auth_data: Dict = {}

# ========== FSM ДЛЯ АВТОРИЗАЦИИ ==========
class AuthStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()
    authorized = State()

# ========== AI ФУНКЦИИ ==========
async def make_ai_request(session: AISession, user_message: str) -> str:
    """Запрос к OpenRouter API с грубым промтом"""
    try:
        # Добавляем сообщение пользователя
        session.add_message("user", user_message)
        
        # Подготавливаем сообщения
        messages = session.get_messages()
        
        # Добавляем reasoning_details от предыдущего ответа
        if (session.reasoning_details and 
            len(messages) > 1 and 
            messages[-1].get("role") == "assistant"):
            messages[-1]["reasoning_details"] = session.reasoning_details
        
        payload = {
            "model": MODEL,
            "messages": messages,
            "reasoning": {"enabled": True},
            "temperature": 0.9,
            "max_tokens": 150
        }
        
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com",
            "X-Title": "Telegram AI User Bot"
        }
        
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30
            ) as response:
                
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"API Error {response.status}: {error_text}")
                    return "Бля, API сдохло, иди нахуй"
                
                data = await response.json()
                
                if 'choices' not in data or not data['choices']:
                    return "API нихуя не ответило, сука"
                
                ai_message = data['choices'][0]['message']
                content = ai_message.get('content', 'Молчу, иди нахуй')
                reasoning_details = ai_message.get('reasoning_details')
                
                # Сохраняем reasoning_details
                session.reasoning_details = reasoning_details
                
                # Добавляем ответ AI
                session.add_message("assistant", content, reasoning_details)
                
                return content
                
    except asyncio.TimeoutError:
        return "Бля, долго думает, иди нахуй"
    except Exception as e:
        logger.error(f"AI request error: {e}")
        return f"Ошибка: {str(e)[:50]}"

# ========== ОБРАБОТЧИКИ TELEGRAM БОТА ==========
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Начало авторизации через бота"""
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
    contact = message.contact
    
    if not contact or not contact.phone_number:
        await message.answer("Не удалось получить номер. Попробуйте еще раз.")
        return
    
    phone = contact.phone_number
    logger.info(f"Получен номер: {phone}")
    
    # Создаем Telethon клиент
    global telethon_client
    telethon_client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    
    try:
        await telethon_client.connect()
        sent_code = await telethon_client.send_code_request(phone)
        
        # Сохраняем данные
        auth_data['phone'] = phone
        auth_data['phone_code_hash'] = sent_code.phone_code_hash
        
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
    code = ''.join(filter(str.isdigit, message.text))
    
    if len(code) != 5:
        await message.answer("❌ Код должен быть из 5 цифр. Попробуйте еще раз:")
        return
    
    if not auth_data:
        await message.answer("❌ Сессия устарела. Начните заново /start")
        await state.clear()
        return
    
    try:
        await telethon_client.sign_in(
            phone=auth_data['phone'],
            code=code,
            phone_code_hash=auth_data['phone_code_hash']
        )
        
        # Успешная авторизация!
        global my_user_id
        me = await telethon_client.get_me()
        my_user_id = me.id
        
        await message.answer(
            f"✅ Авторизация успешна!\n\n"
            f"👤 Вы вошли как: {me.first_name or ''} {me.last_name or ''} (@{me.username or 'нет'})\n\n"
            f"📝 Как использовать ГРУБОГО AI:\n"
            f"1. Откройте ЛИЧНЫЙ чат с кем-то\n"
            f"2. Напишите `.старт` (только ВЫ можете это сделать!)\n"
            f"3. AI начнет грубо отвечать на сообщения\n"
            f"4. Для отключения напишите `.стоп`\n\n"
            f"⚠️ Только ВЫ можете активировать AI командой .старт!"
        )
        
        # Запускаем обработчик сообщений
        asyncio.create_task(start_message_handler())
        
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
    password = message.text
    
    if not telethon_client:
        await message.answer("❌ Сессия устарела. Начните заново /start")
        await state.clear()
        return
    
    try:
        await telethon_client.sign_in(password=password)
        
        global my_user_id
        me = await telethon_client.get_me()
        my_user_id = me.id
        
        await message.answer(
            f"✅ Авторизация с 2FA успешна!\n\n"
            f"Грубый AI подключен к вашему аккаунту.\n"
            f"Используйте `.старт` в личных чатах.\n\n"
            f"⚠️ Только ВЫ можете писать .старт!"
        )
        
        asyncio.create_task(start_message_handler())
        
        await state.set_state(AuthStates.authorized)
        
    except Exception as e:
        logger.error(f"Ошибка 2FA: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")
        await state.clear()

@router.message(Command("status"))
async def cmd_status(message: Message):
    """Статус"""
    if telethon_client and my_user_id:
        active_chats = sum(1 for s in ai_sessions.values() if s.active)
        
        status_text = f"""
📊 Статус Грубого AI:

• Авторизация: ✅ Активна
• Ваш ID: {my_user_id}
• Активных чатов: {active_chats}
• Всего чатов: {len(ai_sessions)}
• Режим: ГРУБЫЙ и МАТЕРНЫЙ

💬 Активируйте AI в чате командой `.старт`
❌ Отключение: `.стоп`
        """
    else:
        status_text = "❌ Вы не авторизованы. Используйте /start"
    
    await message.answer(status_text)

@router.message(Command("logout"))
async def cmd_logout(message: Message, state: FSMContext):
    """Выход"""
    global telethon_client, my_user_id
    
    if telethon_client:
        try:
            await telethon_client.disconnect()
        except:
            pass
        
        telethon_client = None
        my_user_id = None
        ai_sessions.clear()
    
    await message.answer("✅ Вы вышли из аккаунта. Для входа используйте /start")
    await state.clear()

# ========== ОБРАБОТЧИК СООБЩЕНИЙ ДЛЯ USER BOT ==========
async def start_message_handler():
    """Запуск обработчика сообщений для вашего аккаунта"""
    
    @telethon_client.on(events.NewMessage)
    async def handler(event):
        """Обработка ВСЕХ сообщений"""
        try:
            message = event.message
            chat = await event.get_chat()
            
            # Определяем тип чата
            if not hasattr(chat, 'id'):
                return  # Пропускаем странные чаты
            
            chat_id = chat.id
            
            # 🔴 КРИТИЧЕСКИ ВАЖНО: определяем, кто написал
            # В ЛИЧНОМ ЧАТЕ:
            # - Если message.out = True → это ВЫ написали
            # - Если message.out = False → это собеседник написал
            
            # Получаем или создаем сессию
            if chat_id not in ai_sessions:
                ai_sessions[chat_id] = AISession(chat_id, str(chat_id))
            
            session = ai_sessions[chat_id]
            message_text = message.text or ""
            
            # 🔥 КОМАНДА .СТАРТ - обработка
            if message_text.strip().lower() == ".старт":
                if message.out:  # Это написали ВЫ!
                    if not session.active:
                        session.activate()
                        logger.info(f"✅ AI активирован ВАМИ в чате {chat_id}")
                        
                        # Отвечаем только если это личный чат
                        if hasattr(chat, 'first_name'):
                            await message.reply(
                                "✅ Грубый AI активирован!\n"
                                "Теперь я буду грубо отвечать на сообщения.\n"
                                "Для отключения: `.стоп`"
                            )
                    else:
                        if hasattr(chat, 'first_name'):
                            await message.reply("✅ AI уже активен, сука")
                else:
                    # Собеседник пытается активировать
                    if hasattr(chat, 'first_name'):
                        await message.reply("Пошёл нахуй, не тебе команды писать")
                return
            
            # Команда .стоп
            elif message_text.strip().lower() == ".стоп":
                if message.out:  # Это ВЫ
                    if session.active:
                        session.deactivate()
                        if hasattr(chat, 'first_name'):
                            await message.reply("❌ AI отключен, слабак")
                    else:
                        if hasattr(chat, 'first_name'):
                            await message.reply("AI и так не активен, долбаёб")
                else:
                    # Собеседник пытается отключить
                    if hasattr(chat, 'first_name'):
                        await message.reply("Не твоё дело, иди нахуй")
                return
            
            # Команда .сброс
            elif message_text.strip().lower() == ".сброс":
                if message.out:  # Это ВЫ
                    session.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                    session.reasoning_details = None
                    if hasattr(chat, 'first_name'):
                        await message.reply("🔄 История сброшена, дебил")
                return
            
            # 🔥 ОСНОВНАЯ ЛОГИКА: отвечаем на сообщения собеседника
            # 1. Проверяем, что AI активен в этом чате
            # 2. Проверяем, что сообщение от собеседника (не от нас)
            # 3. Проверяем, что это не команда
            
            if not session.active:
                return  # AI не активен
            
            if message.out:
                return  # Пропускаем свои сообщения
            
            if not message_text.strip():
                return  # Пропускаем пустые
            
            # Это сообщение от собеседника, AI активен - отвечаем!
            logger.info(f"Сообщение от собеседника в чате {chat_id}: {message_text[:50]}")
            
            # Отправляем индикатор печати
            async with telethon_client.action(chat_id, 'typing'):
                # Получаем грубый ответ
                ai_response = await make_ai_request(session, message_text)
                
                # Отправляем ответ
                await message.reply(ai_response)
                
                logger.info(f"Отправлен грубый ответ в чат {chat_id}")
        
        except Exception as e:
            logger.error(f"Ошибка в обработчике: {e}", exc_info=True)

    # Запускаем клиент
    await telethon_client.start()
    logger.info(f"✅ User bot запущен! Ваш ID: {my_user_id}")
    
    # Бесконечный цикл
    await telethon_client.run_until_disconnected()

# ========== ЗАПУСК ==========
async def main():
    """Основная функция"""
    logger.info("="*50)
    logger.info("🤖 ГРУБЫЙ AI User Bot запускается...")
    logger.info("="*50)
    
    try:
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"Ошибка запуска: {e}")
    finally:
        logger.info("Бот остановлен")

if __name__ == "__main__":
    asyncio.run(main())
