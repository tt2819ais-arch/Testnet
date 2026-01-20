"""
Telegram AI User Bot - с прямыми значениями для bothost.ru
"""

import asyncio
import json
import logging
import os
import sys
from typing import Dict, List, Optional, Set
import aiohttp
from datetime import datetime, timedelta

from telethon import TelegramClient, events
from telethon.tl.types import Message, User, Chat
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

# ========== ПРЯМЫЕ ЗНАЧЕНИЯ (ВСТАВЬТЕ СВОИ) ==========
API_ID = 22435995  # ВАШ API_ID (число)
API_HASH = "4c7b651950ed7f53520e66299453144d"  # ВАШ API_HASH
OPENROUTER_API_KEY = "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"  # ВАШ КЛЮЧ

MODEL = "xiaomi/mimo-v2-flash:free"
SESSION_FILE = "userbot_session"

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
        logger.info(f"Создана AI сессия для пользователя {user_id} ({username})")
    
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
        # Ограничиваем историю (последние 15 сообщений)
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
ai_sessions: Dict[int, AISession] = {}  # user_id -> AISession
client: Optional[TelegramClient] = None
me: Optional[User] = None

# ========== AI ФУНКЦИИ ==========
async def make_ai_request(session: AISession, user_message: str) -> str:
    """Запрос к OpenRouter API"""
    try:
        # Добавляем сообщение пользователя
        session.add_message("user", user_message)
        
        # Подготавливаем сообщения
        messages = session.get_messages()
        
        # Добавляем reasoning_details от предыдущего ответа
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
                    return "⚠️ Ошибка при обращении к AI сервису. Попробуйте позже."
                
                data = await response.json()
                
                if 'choices' not in data or not data['choices']:
                    return "⚠️ Неверный ответ от AI сервиса."
                
                ai_message = data['choices'][0]['message']
                content = ai_message.get('content', '')
                reasoning_details = ai_message.get('reasoning_details')
                
                # Сохраняем reasoning_details
                session.reasoning_details = reasoning_details
                
                # Добавляем ответ AI
                session.add_message("assistant", content, reasoning_details)
                
                return content
                
    except asyncio.TimeoutError:
        return "⏱️ Превышено время ожидания ответа от AI."
    except Exception as e:
        logger.error(f"AI request error: {e}", exc_info=True)
        return f"⚠️ Ошибка: {str(e)[:100]}"

# ========== ОБРАБОТЧИКИ СООБЩЕНИЙ ==========
async def setup_handlers():
    """Настройка обработчиков событий"""
    
    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def handle_private_message(event: events.NewMessage.Event):
        """Обработка входящих личных сообщений"""
        try:
            # Пропускаем свои сообщения
            if event.message.out:
                return
            
            message = event.message
            sender = await event.get_sender()
            chat = await event.get_chat()
            
            # Получаем информацию о пользователе
            user_id = sender.id
            username = getattr(sender, 'username', '') or getattr(sender, 'first_name', 'Неизвестно')
            
            logger.info(f"Сообщение от {username} (ID: {user_id}): {message.text[:50]}...")
            
            # Инициализируем сессию если её нет
            if user_id not in ai_sessions:
                ai_sessions[user_id] = AISession(user_id, username)
            
            session = ai_sessions[user_id]
            
            # Команда .старт
            if message.text and message.text.strip().lower() == ".старт":
                if not session.active:
                    session.activate()
                    await message.reply(
                        "✅ AI помощник активирован в этом чате!\n\n"
                        "Теперь я буду автоматически отвечать на ваши сообщения.\n"
                        "Для отключения напишите `.стоп`\n"
                        "Для сброса истории напишите `.сброс`\n\n"
                        "🤖 Готов к общению!"
                    )
                    logger.info(f"AI активирован для {username}")
                else:
                    await message.reply("✅ AI помощник уже активен в этом чате!")
                return
            
            # Команда .стоп
            if message.text and message.text.strip().lower() == ".стоп":
                if session.active:
                    session.deactivate()
                    await message.reply(
                        "❌ AI помощник отключен в этом чате.\n"
                        "Чтобы снова активировать, напишите `.старт`"
                    )
                else:
                    await message.reply("AI помощник и так не активен.")
                return
            
            # Команда .сброс
            if message.text and message.text.strip().lower() == ".сброс":
                session.messages = []
                session.reasoning_details = None
                await message.reply("🔄 История диалога сброшена!")
                return
            
            # Команда .помощь
            if message.text and message.text.strip().lower() == ".помощь":
                help_text = """
📖 **AI Помощник - Команды:**

`.старт` - Активировать AI в этом чате
`.стоп` - Отключить AI в этом чате
`.сброс` - Сбросить историю диалога
`.помощь` - Показать это сообщение
`.статус` - Показать статус AI

**Как работает:**
• После активации AI отвечает на ВСЕ сообщения
• Сохраняет контекст разговора
• Можно общаться на любые темы
• Работает только в личных чатах
                """
                await message.reply(help_text)
                return
            
            # Команда .статус
            if message.text and message.text.strip().lower() == ".статус":
                status = "✅ АКТИВЕН" if session.active else "❌ НЕАКТИВЕН"
                messages_count = len(session.messages)
                last_active = session.last_activity.strftime("%H:%M:%S")
                
                status_text = f"""
📊 **Статус AI помощника:**

• Состояние: {status}
• Сообщений в истории: {messages_count}
• Последняя активность: {last_active}
• ID пользователя: {user_id}
• Имя: {username}
                """
                await message.reply(status_text)
                return
            
            # Если AI не активен - игнорируем сообщение
            if not session.active:
                return
            
            # Проверяем, что сообщение не пустое
            if not message.text or not message.text.strip():
                return
            
            # Отправляем индикатор "печатает"
            async with client.action(chat.id, 'typing'):
                # Получаем ответ от AI
                ai_response = await make_ai_request(session, message.text)
                
                # Отправляем ответ
                await message.reply(ai_response)
                
                logger.info(f"Отправлен ответ пользователю {username}")
        
        except FloodWaitError as e:
            logger.warning(f"Flood wait: {e.seconds} секунд")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}", exc_info=True)
            try:
                await event.reply("⚠️ Произошла ошибка при обработке сообщения.")
            except:
                pass
    
    logger.info("Обработчики сообщений настроены")

# ========== АВТОРИЗАЦИЯ ==========
async def authenticate():
    """Интерактивная авторизация"""
    global client, me
    
    print("\n" + "="*50)
    print("🤖 Telegram AI User Bot - Авторизация")
    print("="*50)
    
    # Создаем клиент
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    
    try:
        # Пытаемся восстановить сессию
        await client.connect()
        
        if not await client.is_user_authorized():
            print("\n📱 Требуется авторизация")
            
            # Запрашиваем номер телефона
            phone = input("Введите номер телефона (с кодом страны, например +79991234567): ").strip()
            
            try:
                # Отправляем код
                sent_code = await client.send_code_request(phone)
                print(f"\n✅ Код отправлен на {phone}")
                
                # Запрашиваем код
                code = input("Введите полученный код из Telegram: ").strip()
                
                # Пытаемся войти
                try:
                    await client.sign_in(phone=phone, code=code, phone_code_hash=sent_code.phone_code_hash)
                except SessionPasswordNeededError:
                    print("\n🔐 Требуется двухфакторная аутентификация")
                    password = input("Введите пароль от вашего аккаунта Telegram: ").strip()
                    await client.sign_in(password=password)
                
                print("✅ Авторизация успешна!")
                
            except PhoneNumberUnoccupiedError:
                print("❌ Этот номер не зарегистрирован в Telegram.")
                return False
            except PhoneCodeInvalidError:
                print("❌ Неверный код.")
                return False
            except Exception as e:
                print(f"❌ Ошибка авторизации: {e}")
                return False
        else:
            print("✅ Используется сохраненная сессия")
        
        # Получаем информацию о себе
        me = await client.get_me()
        print(f"\n👤 Авторизован как: {me.first_name} (@{me.username})")
        print(f"🆔 ID: {me.id}")
        print("="*50 + "\n")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка подключения: {e}")
        return False

# ========== СТАТИСТИКА И УТИЛИТЫ ==========
async def show_statistics():
    """Показать статистику"""
    active_sessions = sum(1 for s in ai_sessions.values() if s.active)
    total_sessions = len(ai_sessions)
    
    print("\n" + "="*50)
    print("📊 Статистика AI User Bot")
    print("="*50)
    print(f"• Всего чатов: {total_sessions}")
    print(f"• Активных сессий: {active_sessions}")
    print(f"• Модель: {MODEL}")
    
    if active_sessions > 0:
        print("\nАктивные чаты:")
        for user_id, session in ai_sessions.items():
            if session.active:
                print(f"  - {session.username} (ID: {user_id})")
    
    print("="*50)

async def cleanup_old_sessions():
    """Очистка старых неактивных сессий"""
    cutoff_time = datetime.now() - timedelta(hours=24)
    to_remove = []
    
    for user_id, session in ai_sessions.items():
        if not session.active and session.last_activity < cutoff_time:
            to_remove.append(user_id)
    
    for user_id in to_remove:
        del ai_sessions[user_id]
        logger.info(f"Удалена старая сессия для пользователя {user_id}")

# ========== ГЛАВНАЯ ФУНКЦИЯ ==========
async def main():
    """Основная функция"""
    logger.info("="*50)
    logger.info("🤖 Telegram AI User Bot запускается...")
    logger.info(f"Модель: {MODEL}")
    logger.info("="*50)
    
    # Авторизация
    if not await authenticate():
        logger.error("Авторизация не удалась")
        return
    
    # Настройка обработчиков
    await setup_handlers()
    
    # Показываем инструкции
    print("\n" + "="*50)
    print("🎯 AI User Bot готов к работе!")
    print("="*50)
    print("\n📝 Как использовать:")
    print("1. AI будет работать в ЛИЧНЫХ ЧАТАХ вашего аккаунта")
    print("2. В нужном личном чате напишите `.старт`")
    print("3. AI начнет отвечать на все сообщения в этом чате")
    print("4. Для отключения в чате напишите `.стоп`")
    print("\n⚠️  AI НЕ работает в группах и каналах!")
    print("⚠️  Только личные чаты (Direct Messages)")
    print("\n📊 Для статистики введите 'stats' в консоли")
    print("🔄 Очистка старых сессий: 'cleanup'")
    print("❌ Выход: 'exit'")
    print("="*50 + "\n")
    
    # Запускаем клиент в фоне
    run_client = asyncio.create_task(client.run_until_disconnected())
    
    try:
        # Консольный интерфейс управления
        while True:
            try:
                cmd = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, input, "> "),
                    timeout=0.1
                )
                
                cmd = cmd.strip().lower()
                
                if cmd == 'stats':
                    await show_statistics()
                elif cmd == 'cleanup':
                    await cleanup_old_sessions()
                    print("✅ Старые сессии очищены")
                elif cmd == 'exit':
                    print("👋 Выход...")
                    break
                elif cmd == 'help':
                    print("\nДоступные команды консоли:")
                    print("  stats    - Показать статистику")
                    print("  cleanup  - Очистить старые сессии")
                    print("  exit     - Выйти из программы")
                    print("  help     - Эта справка\n")
                
            except (asyncio.TimeoutError, asyncio.CancelledError):
                # Таймаут ожидания ввода - нормально, продолжаем работу
                pass
            except EOFError:
                # Конец ввода (при запуске в Docker и т.д.)
                break
            except Exception as e:
                print(f"Ошибка ввода: {e}")
    
    except KeyboardInterrupt:
        print("\n\n👋 Остановка по Ctrl+C...")
    finally:
        # Останавливаем клиент
        if not run_client.done():
            run_client.cancel()
            try:
                await run_client
            except asyncio.CancelledError:
                pass
        
        # Отключаемся
        if client:
            await client.disconnect()
        
        logger.info("User Bot остановлен")

if __name__ == "__main__":
    # Для работы в Docker/хосте без консоли
    if len(sys.argv) > 1 and sys.argv[1] == "--daemon":
        # Режим демона - без интерактивной авторизации
        print("Запуск в режиме демона...")
        client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
        
        async def daemon_main():
            await client.start()
            await setup_handlers()
            print("✅ User Bot запущен в фоновом режиме")
            await client.run_until_disconnected()
        
        asyncio.run(daemon_main())
    else:
        # Обычный режим с интерактивной авторизацией
        asyncio.run(main())
