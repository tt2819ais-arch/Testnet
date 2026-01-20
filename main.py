"""
Telegram AI User Bot с входом через номер телефона в самом боте

Особенности:
1. Обычный Telegram бот (через @BotFather)
2. Бот запрашивает номер телефона через кнопку или текстом
3. Авторизация как пользователь (User API) через введенные данные
4. AI работает только в том чате, где написана команда ".старт"
5. Отключение командой ".стоп"
6. Использует OpenRouter API с моделью Xiaomi: MiMo-V2-Flash
7. Поддержка reasoning chain

Архитектура:
1. Основной бот принимает команды и данные для авторизации
2. Создается сессия Telethon с полученными данными
3. Юзербот подключается к Telegram как пользователь
4. AI обрабатывает сообщения в указанных чатах

Требования:
- Хостинг с поддержкой asyncio (bothost.ru подходит)
- Возможность хранить файлы сессий
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Dict, Optional, List, Any
import aiohttp
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from telethon import TelegramClient
from telethon.tl.types import Message as TLMessage
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = "7802806814:AAEymolTEcHxNUnUoscRDDOQ2mpMmAtS0hg"
OPENROUTER_API_KEY = "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"
MODEL = "xiaomi/mimo-v2-flash:free"

# Папка для хранения сессий
SESSIONS_DIR = "telethon_sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)

router = Router()

# FSM состояния для авторизации
class AuthStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()
    authorized = State()

# Состояния AI для чатов
class AISession:
    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.active = False
        self.messages: List[Dict] = []
        self.reasoning_details: Optional[Dict] = None
        self.client: Optional[TelegramClient] = None
    
    def activate(self, client: TelegramClient):
        self.active = True
        self.client = client
        self.messages = []
        self.reasoning_details = None
    
    def deactivate(self):
        self.active = False
        self.messages.clear()
        self.reasoning_details = None
        self.client = None
    
    def add_message(self, role: str, content: str, reasoning_details: Dict = None):
        message = {"role": role, "content": content}
        if role == "assistant" and reasoning_details:
            message["reasoning_details"] = reasoning_details
        self.messages.append(message)
    
    def get_messages(self):
        return self.messages.copy()

# Глобальные хранилища
user_sessions: Dict[int, TelegramClient] = {}  # user_id -> Telethon client
ai_sessions: Dict[int, Dict[int, AISession]] = {}  # user_id -> {chat_id -> AISession}
auth_states: Dict[int, Dict[str, Any]] = {}  # user_id -> auth data

def get_ai_session(user_id: int, chat_id: int) -> Optional[AISession]:
    """Получить AI сессию для чата пользователя"""
    if user_id in ai_sessions and chat_id in ai_sessions[user_id]:
        return ai_sessions[user_id][chat_id]
    return None

async def make_ai_request(session: AISession, user_message: str) -> str:
    """Отправка запроса к OpenRouter API"""
    # Добавляем сообщение пользователя в историю
    session.add_message("user", user_message)
    
    # Подготавливаем сообщения для API
    messages = session.get_messages()
    
    # Добавляем reasoning_details от предыдущего ответа, если есть
    if session.reasoning_details and messages and messages[-1].get("role") == "assistant":
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
    
    try:
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30
            ) as response:
                data = await response.json()
                
                if response.status != 200:
                    logger.error(f"API Error: {data}")
                    return "Ошибка при обращении к AI сервису."
                
                ai_message = data['choices'][0]['message']
                content = ai_message.get('content', '')
                reasoning_details = ai_message.get('reasoning_details')
                
                # Сохраняем reasoning_details для следующего запроса
                session.reasoning_details = reasoning_details
                
                # Добавляем ответ ассистента в историю
                session.add_message("assistant", content, reasoning_details)
                
                return content
                
    except Exception as e:
        logger.error(f"Request error: {e}")
        return "Произошла ошибка при обработке запроса."

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Команда /start - начало работы с ботом"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Поделиться номером", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
    await message.answer(
        "👋 Привет! Я AI User Bot\n\n"
        "Для начала работы мне нужно авторизоваться в Telegram как пользователь.\n"
        "1. Нажмите кнопку ниже, чтобы поделиться номером телефона\n"
        "2. Я пришлю вам код подтверждения\n"
        "3. Введите код для авторизации\n\n"
        "После авторизации я смогу работать как AI в ваших чатах!",
        reply_markup=keyboard
    )
    await state.set_state(AuthStates.waiting_for_phone)

@router.message(AuthStates.waiting_for_phone, F.contact)
async def process_phone(message: Message, state: FSMContext):
    """Обработка номера телефона"""
    phone_number = message.contact.phone_number
    user_id = message.from_user.id
    
    # Сохраняем номер телефона
    await state.update_data(phone=phone_number, user_id=user_id)
    
    # Создаем Telethon клиент
    session_file = os.path.join(SESSIONS_DIR, f"session_{user_id}")
    client = TelegramClient(session_file, api_id="YOUR_API_ID", api_hash="YOUR_API_HASH")
    
    # Сохраняем клиент в глобальном хранилище
    auth_states[user_id] = {"client": client, "phone": phone_number}
    
    try:
        # Отправляем код
        await client.connect()
        sent_code = await client.send_code_request(phone_number)
        
        await state.update_data(
            phone_code_hash=sent_code.phone_code_hash,
            client=client
        )
        
        await message.answer(
            f"📱 Код подтверждения отправлен на номер {phone_number}\n"
            f"Пожалуйста, введите полученный код (формат: 1 2 3 4 5):",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.set_state(AuthStates.waiting_for_code)
        
    except Exception as e:
        logger.error(f"Error sending code: {e}")
        await message.answer(f"Ошибка при отправке кода: {str(e)}")
        await state.clear()

@router.message(AuthStates.waiting_for_code)
async def process_code(message: Message, state: FSMContext):
    """Обработка кода подтверждения"""
    code = message.text.strip().replace(" ", "")
    user_id = message.from_user.id
    user_data = await state.get_data()
    
    if user_id not in auth_states:
        await message.answer("Сессия не найдена. Начните заново с /start")
        await state.clear()
        return
    
    client = auth_states[user_id]["client"]
    phone = auth_states[user_id]["phone"]
    
    try:
        # Пытаемся войти с кодом
        await client.sign_in(
            phone=phone,
            code=code,
            phone_code_hash=user_data.get("phone_code_hash")
        )
        
        # Успешная авторизация
        await message.answer(
            "✅ Успешная авторизация!\n\n"
            "Теперь вы можете использовать команды:\n"
            "• .старт - активировать AI в текущем чате\n"
            "• .стоп - отключить AI\n\n"
            "Примечание: Для работы AI в чате, я должен быть добавлен в него как участник.",
            reply_markup=ReplyKeyboardRemove()
        )
        
        # Сохраняем клиент в активных сессиях
        user_sessions[user_id] = client
        ai_sessions[user_id] = {}
        
        # Запускаем прослушивание сообщений
        asyncio.create_task(start_message_listener(client, user_id))
        
        await state.set_state(AuthStates.authorized)
        
    except SessionPasswordNeededError:
        await message.answer(
            "🔐 Требуется двухфакторная аутентификация.\n"
            "Пожалуйста, введите пароль от вашего аккаунта:"
        )
        await state.set_state(AuthStates.waiting_for_password)
        
    except PhoneCodeInvalidError:
        await message.answer("❌ Неверный код. Пожалуйста, попробуйте еще раз:")
        
    except Exception as e:
        logger.error(f"Error during sign in: {e}")
        await message.answer(f"Ошибка авторизации: {str(e)}\nПопробуйте снова с /start")

@router.message(AuthStates.waiting_for_password)
async def process_password(message: Message, state: FSMContext):
    """Обработка пароля 2FA"""
    password = message.text
    user_id = message.from_user.id
    
    if user_id not in auth_states:
        await message.answer("Сессия не найдена. Начните заново с /start")
        await state.clear()
        return
    
    client = auth_states[user_id]["client"]
    
    try:
        # Завершаем вход с паролем
        await client.sign_in(password=password)
        
        await message.answer(
            "✅ Успешная авторизация с 2FA!\n\n"
            "Теперь вы можете использовать команды:\n"
            "• .старт - активировать AI в текущем чате\n"
            "• .стоп - отключить AI",
            reply_markup=ReplyKeyboardRemove()
        )
        
        # Сохраняем клиент
        user_sessions[user_id] = client
        ai_sessions[user_id] = {}
        
        # Запускаем прослушивание
        asyncio.create_task(start_message_listener(client, user_id))
        
        await state.set_state(AuthStates.authorized)
        
    except Exception as e:
        logger.error(f"Error with 2FA: {e}")
        await message.answer(f"Ошибка авторизации: {str(e)}\nПопробуйте снова с /start")
        await state.clear()

async def start_message_listener(client: TelegramClient, user_id: int):
    """Запуск прослушивания сообщений для Telethon клиента"""
    
    @client.on(events.NewMessage(incoming=True))
    async def handler(event: events.NewMessage.Event):
        """Обработка входящих сообщений"""
        try:
            # Проверяем, что это не от нас самих
            if event.message.out:
                return
            
            # Получаем информацию о чате
            chat_id = event.chat_id
            message_text = event.message.text
            
            if not message_text:
                return
            
            # Проверяем команды
            if message_text.startswith(".старт"):
                # Инициализируем AI сессию для этого чата
                if user_id not in ai_sessions:
                    ai_sessions[user_id] = {}
                
                ai_sessions[user_id][chat_id] = AISession(chat_id)
                ai_sessions[user_id][chat_id].activate(client)
                
                await event.reply(
                    "✅ AI активирован в этом чате!\n"
                    "Теперь я буду отвечать на сообщения.\n"
                    "Для отключения используйте .стоп\n\n"
                    "Модель: Xiaomi MiMo-V2-Flash"
                )
                return
            
            elif message_text.startswith(".стоп"):
                if user_id in ai_sessions and chat_id in ai_sessions[user_id]:
                    ai_sessions[user_id][chat_id].deactivate()
                    del ai_sessions[user_id][chat_id]
                    await event.reply("❌ AI отключен в этом чате.")
                return
            
            # Проверяем, активен ли AI в этом чате
            ai_session = get_ai_session(user_id, chat_id)
            if not ai_session or not ai_session.active:
                return
            
            # Отвечаем через AI
            response = await make_ai_request(ai_session, message_text)
            await event.reply(response)
            
        except Exception as e:
            logger.error(f"Error in message handler: {e}")

@router.message(Command("logout"))
async def cmd_logout(message: Message, state: FSMContext):
    """Команда для выхода из аккаунта"""
    user_id = message.from_user.id
    
    if user_id in user_sessions:
        try:
            await user_sessions[user_id].disconnect()
        except:
            pass
        
        # Очищаем все сессии пользователя
        user_sessions.pop(user_id, None)
        ai_sessions.pop(user_id, None)
        auth_states.pop(user_id, None)
    
    await message.answer("✅ Вы вышли из аккаунта. Для новой авторизации используйте /start")
    await state.clear()

@router.message(Command("status"))
async def cmd_status(message: Message, state: FSMContext):
    """Проверка статуса"""
    user_id = message.from_user.id
    
    if user_id not in user_sessions:
        await message.answer("❌ Не авторизован. Используйте /start")
        return
    
    active_chats = []
    if user_id in ai_sessions:
        active_chats = [str(chat_id) for chat_id, session in ai_sessions[user_id].items() 
                       if session.active]
    
    await message.answer(
        f"📊 Статус:\n"
        f"• Авторизация: ✅\n"
        f"• Активные AI чаты: {len(active_chats)}\n"
        f"• ID активных чатов: {', '.join(active_chats) if active_chats else 'нет'}"
    )

@router.message(Command("help"))
async def cmd_help(message: Message):
    """Справка по командам"""
    help_text = """
🤖 **AI User Bot - Помощь**

**Основные команды:**
/start - Начать авторизацию (требуется номер телефона)
/status - Проверить статус
/logout - Выйти из аккаунта
/help - Эта справка

**Команды в чатах (после авторизации):**
.старт - Активировать AI в текущем чате
.стоп - Отключить AI в текущем чате

**Примечания:**
1. После авторизации я буду работать как пользователь в ваших чатах
2. AI активируется только в тех чатах, где написана команда .старт
3. Для работы в группе добавьте меня в нее как участника
4. Используемая модель: Xiaomi MiMo-V2-Flash
    """
    await message.answer(help_text, parse_mode="Markdown")

async def main():
    """Основная функция"""
    # Инициализация бота
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    # Запуск
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Важно: замените API_ID и API_HASH на свои
    # Получите их на https://my.telegram.org
    asyncio.run(main())
