# Здесь хранится вся инфа о командах и фоновых задачах
import inspect
import json
import os
import sys
import time
import asyncio
from datetime import datetime, timedelta
from telethon import errors

# --- СИСТЕМА ЛОГИРОВАНИЯ И РЕЖИМ ОТЛАДКИ (--debug) ---
DEBUG_MODE = "--debug" in sys.argv

def is_debug_mode():
    """Возвращает True, если юзербот запущен с флагом --debug."""
    return DEBUG_MODE

def set_debug_mode(enabled: bool):
    """Вручную переключает режим отладки."""
    global DEBUG_MODE
    DEBUG_MODE = bool(enabled)

# --- ХРАНИЛИЩЕ И ОТПРАВКА ЛОГОВ В ЧАТ ubtg-logs ---
log_chat_state = {
    "chat_id": None
}
_pending_log_errors = []
_is_sending_log = False

def set_log_chat_id(chat_id):
    """Устанавливает ID чата ubtg-logs и сбрасывает накопившиеся ошибки."""
    global _pending_log_errors
    log_chat_state["chat_id"] = chat_id
    if chat_id and _pending_log_errors:
        bot = get_bot()
        if bot:
            loop = None
            try:
                loop = asyncio.get_event_loop()
            except Exception:
                pass
            if loop and loop.is_running():
                for item in list(_pending_log_errors):
                    loop.create_task(_async_send_log_error(bot, chat_id, item["module"], item["msg"], item["time"]))
                _pending_log_errors.clear()

def get_log_chat_id():
    """Возвращает ID чата ubtg-logs."""
    return log_chat_state.get("chat_id")

def send_error_to_log_chat(module_name, msg):
    """
    Планирует отправку лога ошибки в чат ubtg-logs через Telegram бота.
    Работает всегда, независимо от флага --debug.
    """
    chat_id = get_log_chat_id()
    bot = get_bot()
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    if not chat_id or not bot:
        # Если чат логов еще не инициализирован, сохраняем в буфер
        if len(_pending_log_errors) < 50:
            _pending_log_errors.append({
                "module": module_name,
                "msg": str(msg),
                "time": now_str
            })
        return

    try:
        loop = asyncio.get_event_loop()
        if loop and loop.is_running():
            loop.create_task(_async_send_log_error(bot, chat_id, module_name, msg, now_str))
    except Exception:
        pass

async def _async_send_log_error(bot, chat_id, module_name, msg, time_str=None):
    """Асинхронная отправка лога ошибки через Telegram бота в чат ubtg-logs."""
    global _is_sending_log
    if _is_sending_log:
        return
    try:
        _is_sending_log = True
        time_str = time_str or datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        safe_msg = str(msg).strip()
        if len(safe_msg) > 3500:
            safe_msg = safe_msg[:3500] + "\n... [Лог обрезан по лимиту Telegram]"

        text = (
            f"🚨 **[ОШИБКА]** `[{module_name}]`\n"
            f"⏱ **Время:** `{time_str}`\n\n"
            f"**Детали ошибки:**\n"
            f"```{safe_msg}```"
        )
        await bot.send_message(chat_id, text)
    except Exception:
        pass
    finally:
        _is_sending_log = False

async def ensure_log_chat(userbot_client, bot_client, bot_username):
    """
    Проверяет существование чата ubtg-logs.
    Если чат не найден, создает приватный канал 'ubtg-logs',
    добавляет в него бота как администратора с правами публикации
    и сохраняет log_chat_id в core_conf.json.
    """
    from telethon.tl.functions.channels import CreateChannelRequest, EditAdminRequest
    from telethon.tl.types import ChatAdminRights
    from telethon import utils

    config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core_conf.json")
    config = {}
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            pass

    saved_chat_id = config.get("log_chat_id")
    target_channel = None

    # 1. Проверяем сохраненный log_chat_id
    if saved_chat_id:
        try:
            target_channel = await userbot_client.get_entity(saved_chat_id)
        except Exception:
            target_channel = None

    # 2. Если не найден по сохраненному ID, ищем среди диалогов
    if not target_channel:
        try:
            async for dialog in userbot_client.iter_dialogs(limit=100):
                if dialog.is_channel and (dialog.name == "ubtg-logs" or dialog.title == "ubtg-logs"):
                    target_channel = dialog.entity
                    break
        except Exception as e:
            logger.debug(f"Поиск ubtg-logs в диалогах: {e}")

    # 3. Если канал найден — проверяем/выдаем права боту
    if target_channel:
        channel_id = utils.get_peer_id(target_channel)
        if bot_username:
            try:
                bot_user = await userbot_client.get_input_entity(bot_username)
                admin_rights = ChatAdminRights(
                    change_info=True,
                    post_messages=True,
                    edit_messages=True,
                    delete_messages=True,
                    invite_users=True
                )
                await userbot_client(EditAdminRequest(
                    channel=target_channel,
                    user_id=bot_user,
                    admin_rights=admin_rights,
                    rank="Logger Bot"
                ))
            except Exception as e:
                logger.debug(f"Назначение прав боту в ubtg-logs: {e}")

        set_log_chat_id(channel_id)
        config["log_chat_id"] = channel_id
        try:
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception:
            pass
        return channel_id

    # 4. Если канала нет — создаем новый приватный канал ubtg-logs
    logger.info("Создание приватного канала 'ubtg-logs' для логирования ошибок...")
    result = await userbot_client(CreateChannelRequest(
        title="ubtg-logs",
        about="Логи ошибок юзербота UBTG",
        megagroup=False
    ))
    target_channel = result.chats[0]
    channel_id = utils.get_peer_id(target_channel)

    # Добавляем бота как администратора
    if bot_username:
        try:
            bot_user = await userbot_client.get_input_entity(bot_username)
            admin_rights = ChatAdminRights(
                change_info=True,
                post_messages=True,
                edit_messages=True,
                delete_messages=True,
                invite_users=True
            )
            await userbot_client(EditAdminRequest(
                channel=target_channel,
                user_id=bot_user,
                admin_rights=admin_rights,
                rank="Logger Bot"
            ))
        except Exception as e:
            logger.debug(f"Назначение прав боту в новом ubtg-logs: {e}")

    set_log_chat_id(channel_id)
    config["log_chat_id"] = channel_id
    try:
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception:
        pass

    # Отправляем приветственное сообщение в лог-чат от имени бота
    try:
        await bot_client.send_message(
            channel_id,
            "📁 **Чат логов UBTG (`ubtg-logs`) успешно создан!**\n\n"
            "🤖 Сюда от имени бота будут автоматически присылаться все логи ошибок юзербота."
        )
    except Exception as ex_init_msg:
        logger.debug(f"Ошибка отправки приветствия в ubtg-logs: {ex_init_msg}")

    return channel_id

class ModuleLogger:
    """Логгер для модулей и ядра с поддержкой уровней, цветов, флага --debug и отправки ошибок в чат."""
    def __init__(self, name="Core"):
        self.name = name

    def _log(self, level_name, color, icon, msg):
        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"\033[90m{now_str}\033[0m {color}[{icon} {level_name}]\033[0m \033[1;34m[{self.name}]\033[0m {msg}")

    def debug(self, msg):
        if DEBUG_MODE:
            self._log("DEBUG", "\033[36m", "🔍", msg)

    def info(self, msg):
        if DEBUG_MODE:
            self._log("INFO", "\033[32m", "ℹ️", msg)

    def warning(self, msg):
        if DEBUG_MODE:
            self._log("WARN", "\033[33m", "⚠️", msg)

    def error(self, msg):
        # Ошибки ВСЕГДА выводятся в консоль (и в debug режиме, и без него)
        self._log("ERROR", "\033[31m", "❌", msg)
        # Ошибки ВСЕГДА отправляются в чат ubtg-logs через ТГ бота
        send_error_to_log_chat(self.name, msg)

    def success(self, msg):
        if DEBUG_MODE:
            self._log("OK", "\033[32m", "✅", msg)

def get_logger(module_name="Core"):
    """Возвращает экземпляр ModuleLogger для указанного модуля."""
    return ModuleLogger(module_name)

logger = get_logger("Core")

def log_debug(module_name, msg):
    get_logger(module_name).debug(msg)

def log_info(module_name, msg):
    get_logger(module_name).info(msg)

def log_warning(module_name, msg):
    get_logger(module_name).warning(msg)

def log_error(module_name, msg):
    get_logger(module_name).error(msg)

# --- ГЛАВНЫЙ КЛИЕНТ ЮЗЕРБОТА ---
main_client_instance = None

def set_main_client(client):
    """Сохраняет главный экземпляр TelegramClient юзербота в реестре."""
    global main_client_instance
    main_client_instance = client

def get_main_client():
    """Возвращает главный экземпляр TelegramClient юзербота."""
    return main_client_instance

# --- СИСТЕМА ОГРАНИЧЕНИЙ И ЗАЩИТЫ ОТ СПАМБАНА (Rate Limiter / FloodWait) ---
rate_limiter_state = {
    "until": 0.0,
    "last_cmd_time": 0.0,
    "min_interval": 0.3  # Минимальная задержка между командами (в секундах)
}

def is_rate_limited():
    """Проверяет, действует ли сейчас ограничение на отправку запросов"""
    return time.time() < rate_limiter_state["until"]

def get_rate_limit_remaining():
    """Возвращает оставшееся время ограничения в секундах"""
    rem = rate_limiter_state["until"] - time.time()
    return max(0, int(rem))

async def apply_flood_wait(seconds, source="Telegram API"):
    """
    Фиксирует задержку FloodWait, высчитывает время окончания и отправляет уведомление через бота.
    """
    rate_limiter_state["until"] = time.time() + seconds
    
    until_dt = datetime.now() + timedelta(seconds=seconds)
    until_str = until_dt.strftime("%H:%M:%S")

    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)

    parts = []
    if hours > 0:
        parts.append(f"{hours} ч.")
    if mins > 0:
        parts.append(f"{mins} мин.")
    parts.append(f"{secs} сек.")
    duration_str = " ".join(parts)

    notify_text = (
        f"⚠️ **Внимание: Сработало ограничение Telegram API (FloodWait)!**\n\n"
        f"⏱ **Длительность:** `{duration_str}` (`{seconds} сек.`)\n"
        f"⏳ **Ограничение спадёт в:** `{until_str}`\n"
        f"📌 **Источник:** {source}\n\n"
        f"🤖 Юзербот временно заблокировал отправку новых запросов, чтобы предотвратить спамбан и сброс сессии."
    )
    print(f"[RateLimiter] ⚠️ Зафиксирован FloodWait на {seconds} сек. (до {until_str})")
    await send_bot_notification(notify_text)

async def check_cmd_rate_limit():
    """
    Проактивная проверка перед отправкой команды:
    - Задерживает выполнение на min_interval, если команды идут слишком часто
    - Если действует FloodWait, генерирует исключение
    """
    if is_rate_limited():
        rem = get_rate_limit_remaining()
        raise PermissionError(f"Действует ограничение Telegram (FloodWait). Запросы заблокированы ещё на {rem} сек.")

    now = time.time()
    elapsed = now - rate_limiter_state["last_cmd_time"]
    if elapsed < rate_limiter_state["min_interval"]:
        await asyncio.sleep(rate_limiter_state["min_interval"] - elapsed)
    rate_limiter_state["last_cmd_time"] = time.time()

# --- СИСТЕМА КОНФИГУРАЦИЙ ---
CONFIG_FILE = "Global_config.json"
global_config = {}

def load_config():
    """Загружает конфигурацию из JSON файла"""
    global global_config
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            try:
                global_config = json.load(f)
            except json.JSONDecodeError:
                global_config = {}
    else:
        global_config = {}
        save_config()

def save_config():
    """Сохраняет текущую конфигурацию в JSON файл"""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(global_config, f, indent=4, ensure_ascii=False)

def get_config(module_name, key, default=None):
    """
    Получает значение из конфига.
    Пример: get_config("module_ping", "reply_text", "Понг!")
    """
    return global_config.get(module_name, {}).get(key, default)

def set_config(module_name, key, value):
    """
    Записывает значение в конфиг и сохраняет файл.
    Пример: set_config("module_ping", "reply_text", "Дарова!")
    """
    if module_name not in global_config:
        global_config[module_name] = {}
    
    global_config[module_name][key] = value
    save_config()

def init_config(module_name, default_dict):
    """
    Инициализирует дефолтные настройки модуля. 
    Записывает их в конфиг, только если их там еще нет.
    Вызывать в начале файла модуля.
    """
    if module_name not in global_config:
        global_config[module_name] = {}
        
    changed = False
    for key, default_value in default_dict.items():
        if key not in global_config[module_name]:
            global_config[module_name][key] = default_value
            changed = True
            
    if changed:
        save_config()

# Загружаем конфиг при старте
load_config()


# --- РЕЕСТР МОДУЛЕЙ И КОМАНД ---
modules_repo = {
    "modules": {},       # Инфа о модулях и их командах
    "commands": {},      # Плоский список для быстрого запуска
    "background_tasks": []
}

def set_module_meta(name, desc="Описания не найдено", system=False):
    """
    Задает имя и описание для модуля (вызывать в начале файла).
    system=True — модуль системный. Такие модули нельзя удалить через .uninstall,
    и в .help они помечаются специальной пометкой "Системное".
    """
    frame = inspect.currentframe().f_back
    module_id = inspect.getmodule(frame).__name__
    
    if module_id not in modules_repo["modules"]:
        modules_repo["modules"][module_id] = {
            "name": name,
            "desc": desc,
            "commands": {},
            "system": system
        }
    else:
        modules_repo["modules"][module_id]["name"] = name
        modules_repo["modules"][module_id]["desc"] = desc
        modules_repo["modules"][module_id]["system"] = system

def register_cmd(command_name, desc="Описания не найдено"):
    def decorator(func):
        module_id = inspect.getmodule(func).__name__
        
        # Если модуль не задал о себе инфу через set_module_meta, даем ему дефолтное имя
        if module_id not in modules_repo["modules"]:
            fallback_name = module_id.split('.')[-1].capitalize()
            modules_repo["modules"][module_id] = {
                "name": fallback_name,
                "desc": "Описания не найдено",
                "commands": {},
                "system": False
            }
        
        # Сохраняем описание команды в модуль
        modules_repo["modules"][module_id]["commands"][command_name] = desc
        # Сохраняем саму функцию в плоский словарь для ядра
        modules_repo["commands"][command_name] = func
        
        return func
    return decorator

def register_bg():
    def decorator(func):
        modules_repo["background_tasks"].append(func)
        return func
    return decorator

def is_system_module(module_id):
    """Проверяет, является ли модуль системным (защищенным от удаления)."""
    mod_info = modules_repo["modules"].get(module_id)
    if not mod_info:
        return False
    return mod_info.get("system", False)


# --- СИСТЕМА ПОЛНОЙ ПЕРЕЗАГРУЗКИ ---
# Используется для того, чтобы после os.execv() ядро могло найти чат и
# сообщение, куда нужно отправить подтверждение об успешном рестарте.
RESTART_FILE = "restart_info.json"

def save_restart_info(chat_id, message_id, custom_text=None, inline_message_id=None):
    """
    Сохраняет данные о том, где было вызвано .restart или .update, чтобы после
    перезапуска процесса ядро могло отредактировать это же сообщение (включая inline-сообщения).
    """
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "time": time.time()
    }
    if custom_text:
        data["custom_text"] = custom_text
    if inline_message_id:
        data["inline_message_id"] = inline_message_id
    try:
        with open(RESTART_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[Registry] Не удалось сохранить restart_info: {e}")

def pop_restart_info():
    """
    Читает данные о рестарте (если они есть) и сразу удаляет файл,
    чтобы при следующем обычном запуске бот не пытался снова кому-то отвечать.
    Возвращает dict {chat_id, message_id, time} либо None.
    """
    if not os.path.exists(RESTART_FILE):
        return None
    try:
        with open(RESTART_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = None
    finally:
        try:
            os.remove(RESTART_FILE)
        except OSError:
            pass
    return data

async def restart_userbot(client=None, chat_id=None, message_id=None, custom_text=None, event=None):
    """
    Выполняет полную перезагрузку юзербота (полный перезапуск Python-процесса).
    Сохраняет данные для восстановления контекста/редактирования исходного сообщения после перезапуска.
    """
    restart_logger = get_logger("Restart")
    restart_logger.info("🔄 Инициализация перезагрузки юзербота...")

    inline_info = None
    if event:
        input_msg_id = getattr(event, "_input_inline_message_id", None)
        if input_msg_id:
            inline_info = {
                "type": type(input_msg_id).__name__,
                "dc_id": getattr(input_msg_id, "dc_id", 0),
                "id": getattr(input_msg_id, "id", 0),
                "access_hash": getattr(input_msg_id, "access_hash", 0)
            }
        if chat_id is None:
            chat_id = getattr(event, "chat_id", None)
        if message_id is None:
            message_id = getattr(event, "message_id", None) or getattr(event, "id", None)

    if chat_id is not None or inline_info is not None:
        restart_logger.debug(f"Сохранение контекста перезагрузки: chat_id={chat_id}, message_id={message_id}, inline={inline_info is not None}")
        save_restart_info(chat_id, message_id, custom_text=custom_text, inline_message_id=inline_info)

    target_client = client or get_main_client()
    if target_client:
        restart_logger.debug("Отключение клиента Telegram юзербота...")
        try:
            if hasattr(target_client, 'is_connected') and target_client.is_connected():
                await target_client.disconnect()
            elif hasattr(target_client, 'disconnect'):
                await target_client.disconnect()
        except Exception as e:
            restart_logger.debug(f"Исключение при отключении клиента: {e}")

    bot = get_bot()
    if bot:
        restart_logger.debug("Отключение Telegram-бота ядра...")
        try:
            if hasattr(bot, 'is_connected') and bot.is_connected():
                await bot.disconnect()
            elif hasattr(bot, 'disconnect'):
                await bot.disconnect()
        except Exception as e:
            restart_logger.debug(f"Исключение при отключении бота: {e}")

    python = sys.executable or "python3"
    script = os.path.abspath(sys.argv[0])
    args = [python, script] + sys.argv[1:]

    restart_logger.info(f"🚀 Перезапуск процесса: {' '.join(args)}")

    try:
        os.execv(python, args)
    except Exception as e:
        restart_logger.error(f"os.execv завершился с ошибкой: {e}. Запуск через subprocess fallback...")
        try:
            import subprocess
            subprocess.Popen(args)
            sys.exit(0)
        except Exception as sub_err:
            restart_logger.error(f"Критическая ошибка fallback перезапуска: {sub_err}")


# --- ХРАНИЛИЩЕ БОТА, ВЛАДЕЛЬЦА И ИНЛАЙН ИНФРАСТРУКТУРЫ ---
bot_instance = {
    "client": None,
    "username": None,
    "owner_id": None,
    "start_time": time.time()
}

callback_handlers = {}
inline_payload_cache = {}

def set_bot(client, username=None):
    """Сохраняет инстанс TelegramClient бота и его юзернейм"""
    bot_instance["client"] = client
    if username:
        bot_instance["username"] = username.replace("@", "")

def get_bot():
    """Возвращает инстанс TelegramClient для бота"""
    return bot_instance["client"]

def set_bot_client(client):
    """Сохраняет инстанс TelegramClient для бота (совместимость)"""
    bot_instance["client"] = client

def get_bot_client():
    """Возвращает инстанс TelegramClient для бота"""
    return bot_instance["client"]

def get_bot_username():
    """Возвращает юзернейм бота (без @)"""
    return bot_instance["username"]

def set_owner_id(owner_id):
    """Сохраняет Telegram ID владельца юзербота"""
    bot_instance["owner_id"] = owner_id

def get_owner_id():
    """Возвращает Telegram ID владельца юзербота"""
    return bot_instance["owner_id"]

def get_userbot_start_time():
    """Возвращает время запуска юзербота"""
    return bot_instance["start_time"]

async def send_bot_notification(text):
    """
    Отправляет уведомление владельцу юзербота через ТГ бота (если бот активен)
    с автоматическим резолвом сущности пользователя при необходимости.
    """
    client = get_bot()
    owner_id = get_owner_id()
    if client and owner_id:
        try:
            await client.send_message(owner_id, text)
            return True
        except Exception:
            try:
                entity = await client.get_entity(owner_id)
                await client.send_message(entity, text)
                return True
            except Exception as ex:
                print(f"[Registry] ⚠️ Не удалось отправить уведомление через бота: {ex}")
    return False

def register_callback(prefix):
    """
    Декоратор для регистрации функций-обработчиков инлайн-кнопок в модулях.
    Пример:
        @register_callback("my_prefix")
        async def handle_click(event, data):
            await event.answer("Нажато!")
    """
    def decorator(func):
        callback_handlers[prefix] = func
        return func
    return decorator

async def send_inline(client, chat_id, text, buttons=None, reply_to=None):
    """
    Отправляет сообщение с инлайн-кнопками в любой чат через встроенного бота.
    Модули юзербота могут использовать эту функцию для вывода интерактивных элементов.
    """
    bot_username = get_bot_username()
    if not bot_username:
        raise ValueError("Встроенный Telegram Бот не запущен или отсутствует username")

    payload_id = f"inl_{int(time.time() * 1000)}"
    inline_payload_cache[payload_id] = {
        "text": text,
        "buttons": buttons
    }

    try:
        results = await client.inline_query(bot_username, payload_id)
        if results:
            return await results[0].click(chat_id, reply_to=reply_to)
    except errors.FloodWaitError as e:
        await apply_flood_wait(e.seconds, source="send_inline")
        raise e
    except Exception as e:
        print(f"[Registry] Ошибка отправки inline-сообщения: {e}")
        raise e