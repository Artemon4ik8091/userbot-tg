import os
import sys
import time
import json
import asyncio
import importlib
import random
import re
import psutil
import traceback
from datetime import datetime
from telethon import TelegramClient, events, errors, Button
from telethon.sessions import MemorySession

if "--help" in sys.argv or "-h" in sys.argv:
    print("""Использование: python3 userbot.py [ПАРАМЕТРЫ]

Доступные параметры запуска:
  -h, --help                  Показать это сообщение справки и выйти
  --debug                     Включить подробный вывод логов всех модулей в консоль
  --no-web                    Использовать консольную настройку вместо веб-интерфейса
  --port <число>              Порт для веб-интерфейса настройки (по умолчанию: 8080)
  --web-port <число>          Алиас для --port
  --no-api                    Скрыть шаг ввода API Telegram (если уже настроено)
  --no-proxy                  Скрыть шаг настройки прокси в веб-интерфейсе
  --set-app-id <число>        Установить API ID (получить на my.telegram.org)
  --set-hash-id <строка>      Установить API Hash
  --set-proxy-ip <строка>     Установить IP-адрес прокси (например, 127.0.0.1)
  --set-proxy-port <число>    Установить порт прокси (например, 1080)
  --set-proxy-protocol <тип>  Установить протокол прокси (доступны: http, socks4, socks5)
  --host                      Режим хостинга (отключает загрузку локальных installer.py и terminal.py)
""")
    sys.exit(0)

# Подключаем наше общее хранилище из реестра
from registry import (
    modules_repo,
    pop_restart_info,
    set_owner_id,
    get_owner_id,
    set_bot,
    get_bot,
    get_bot_username,
    get_userbot_start_time,
    send_bot_notification,
    callback_handlers,
    inline_payload_cache,
    save_restart_info,
    restart_userbot,
    is_rate_limited,
    get_rate_limit_remaining,
    apply_flood_wait,
    check_cmd_rate_limit,
    get_logger,
    is_debug_mode,
    set_main_client,
    ensure_log_chat,
    get_log_chat_id,
    set_log_chat_id,
    get_prefix
)

core_logger = get_logger("Core")
if is_debug_mode():
    core_logger.info("🐛 Режим отладки (--debug) включен! Подробный лог модулей активирован.")

def get_proxy_config(config):
    """Извлекает настройки прокси из конфигурации ядра"""
    if not isinstance(config, dict): return None
    proxy = config.get("proxy")
    if not proxy or not isinstance(proxy, dict): return None
    addr = proxy.get("addr")
    port = proxy.get("port")
    if not addr or port is None: return None
    try: port = int(port)
    except (ValueError, TypeError): return None
    
    proxy_dict = {
        'proxy_type': str(proxy.get("proxy_type", "http")).lower(),
        'addr': str(addr),
        'port': port
    }
    if proxy.get("username"): proxy_dict['username'] = str(proxy.get("username"))
    if proxy.get("password"): proxy_dict['password'] = str(proxy.get("password"))
    if "rdns" in proxy: proxy_dict['rdns'] = bool(proxy.get("rdns"))
    return proxy_dict

# ==========================================
# ЗАГРУЗКА PRE-AUTH (INIT) МОДУЛЕЙ
# ==========================================
def get_init_modules():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    init_dir = os.path.join(base_dir, 'init_modules')
    
    if not os.path.exists(init_dir):
        os.makedirs(init_dir)
        print(f"[Core] 📁 Создана папка для модулей инициализации: init_modules/")
        
    modules = []
    if init_dir not in sys.path:
        sys.path.insert(0, init_dir)
        
    importlib.invalidate_caches()
    # Сортируем файлы, чтобы 00_auth_wizard.py загрузился первым
    files = sorted(f for f in os.listdir(init_dir) if f.endswith('.py') and not f.startswith('__'))
    for file in files:
        mod_name = file[:-3]
        try:
            mod = importlib.import_module(mod_name)
            modules.append(mod)
        except Exception as e:
            print(f"[Core] ❌ Ошибка при загрузке init-модуля '{mod_name}': {e}")
            
    return modules

init_mods = get_init_modules()

# 1. Этап синхронной сборки конфигурации
raw_config = None
for mod in init_mods:
    if hasattr(mod, 'setup_config'):
        conf = mod.setup_config()
        if conf:
            raw_config = conf
            break

if not raw_config:
    CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core_conf.json")
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            raw_config = json.load(f)
    else:
        print("[Core] ❌ Критическая ошибка: Нет модуля авторизации и отсутствует core_conf.json!")
        sys.exit(1)

API_ID = int(raw_config["app_id"])
API_HASH = raw_config["hash_id"]
proxy_config = get_proxy_config(raw_config)

if proxy_config:
    print(f"[Core] 🌐 Используется прокси ({proxy_config['proxy_type']}://{proxy_config['addr']}:{proxy_config['port']})")
else:
    print("[Core] 🌐 Прокси не используется (не прописан в конфиге)")

# Инициализируем клиента
client = TelegramClient(
    'my_account',
    API_ID,
    API_HASH,
    device_model="MacBook Pro",
    system_version="macOS 14.5",
    app_version="10.11.1",
    proxy=proxy_config
)

async def auto_setup_bot(userbot_client, me):
    """Настройка встроенного бота ядра."""
    
    # Перечитываем файл конфигурации, так как модули pre_auth (настройка после QR)
    # могли обновить его и добавить desired_bot_username.
    CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core_conf.json")
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            config = raw_config
    else:
        config = raw_config

    bot_token = config.get("bot_token")
    bot_username = config.get("bot_username")
    is_first_run = config.get("is_first_run", True)
    desired_username = config.get("desired_bot_username")

    if bot_token and bot_username:
        return bot_token, bot_username, is_first_run

    print("\n[Core] 🤖 Настройка встроенного Telegram Бота...")
    print("[Core] Подключаемся к @BotFather...")

    try:
        bf_entity = await userbot_client.get_input_entity("BotFather")
        async with userbot_client.conversation(bf_entity, timeout=30) as conv:
            found_token = None
            found_username = None

            if desired_username:
                target_un = desired_username if desired_username.lower().endswith("bot") else f"{desired_username}_bot"
                print(f"[Core] 🎯 Пытаемся создать бота с юзернеймом: @{target_un}...")
                await conv.send_message("/newbot")
                resp = await conv.get_response()

                if any(p in resp.text.lower() for p in ["name for your bot", "new bot"]):
                    await conv.send_message(f"{me.first_name}'s Assistant")
                    await conv.get_response()
                    await conv.send_message(target_un)
                    resp = await conv.get_response()
                    match = re.search(r"(\d+:[A-Za-z0-9_-]+)", resp.text)
                    if match:
                        found_token, found_username = match.group(1), target_un
                        print(f"[Core] 🎉 Успешно создан бот @{found_username}!")
                    else:
                        print(f"[Core] ⚠️ Юзернейм @{target_un} недоступен. Сбрасываем диалог...")
                        await conv.send_message("/cancel")
                        try: await conv.get_response()
                        except: pass

            if not found_token:
                print("[Core] 🔍 Проверяем наличие существующих ботов...")
                await conv.send_message("/token")
                resp = await conv.get_response()

                if not any(kw in resp.text.lower() for kw in ["don't have any", "no bots", "у вас нет"]):
                    candidate_bots = []
                    if resp.buttons:
                        for row in resp.buttons:
                            for btn in row:
                                m = re.search(r"@?([A-Za-z0-9_]+_bot)", btn.text or "", re.IGNORECASE)
                                if m: candidate_bots.append((m.group(1), btn))
                    if not candidate_bots:
                        for m in re.finditer(r"@([A-Za-z0-9_]+_bot)", resp.text, re.IGNORECASE):
                            candidate_bots.append((m.group(1), None))

                    if candidate_bots:
                        chosen = next((b for b in candidate_bots if any(x in b[0].lower() for x in ["_ub_", "userbot", "assistant"])), None)
                        if chosen:
                            target_username, btn_obj = chosen
                            print(f"[Core] 💡 Найден подходящий бот: @{target_username}")
                            if btn_obj is not None:
                                try: await btn_obj.click()
                                except: await conv.send_message(f"@{target_username}")
                            else:
                                await conv.send_message(f"@{target_username}")

                            token_resp = await conv.get_response()
                            match = re.search(r"(\d+:[A-Za-z0-9_-]+)", token_resp.text)
                            if match:
                                found_token, found_username = match.group(1), target_username
                                print(f"[Core] ✅ Получен токен для существующего бота @{found_username}")
                        else:
                            print("[Core] ℹ️ Подходящих ботов не найдено.")

            if not found_token:
                print("[Core] ➕ Создаем нового бота со случайным юзернеймом...")
                await conv.send_message("/newbot")
                resp = await conv.get_response()
                if any(p in resp.text.lower() for p in ["name for your bot", "new bot"]):
                    await conv.send_message(f"{me.first_name}'s Assistant")
                    await conv.get_response()
                    
                    # Telegram запрещает юзернеймы, начинающиеся с цифры, поэтому добавляем обязательный префикс `id`
                    target_username = f"id{me.id}_ub_bot"
                    await conv.send_message(target_username)
                    resp = await conv.get_response()
                    
                    match = re.search(r"(\d+:[A-Za-z0-9_-]+)", resp.text)
                    if match:
                        found_token, found_username = match.group(1), target_username
                        print(f"[Core] 🎉 Новый бот @{found_username} успешно создан!")

            if found_token and found_username:
                try:
                    await conv.send_message("/setinline")
                    resp = await conv.get_response()
                    if any(w in resp.text.lower() for w in ["choose a bot"]):
                        await conv.send_message(f"@{found_username}")
                        resp = await conv.get_response()
                    if any(w in resp.text.lower() for w in ["placeholder"]):
                        await conv.send_message("Search")
                        await conv.get_response()
                except Exception as ex_inline:
                    print(f"[Core] ⚠️ Внимание при проверке inline: {ex_inline}")

                config["bot_token"], config["bot_username"], config["is_first_run"] = found_token, found_username, True
                with open(CONFIG_FILE, "w", encoding="utf-8") as f: json.dump(config, f, indent=4)
                return found_token, found_username, True

    except Exception as e:
        print(f"[Core] ⚠️ Не удалось автоматически настроить бота через @BotFather: {e}")

    print("\n" + "="*50)
    print("=== НАСТРОЙКА ВСТРОЕННОГО ТЕЛЕГРАМ БОТА ===")
    bot_token = input("Введите Bot Token (например: 123456789:ABC...): ").strip()
    
    temp_bot = TelegramClient(MemorySession(), API_ID, API_HASH, proxy=proxy_config)
    await temp_bot.start(bot_token=bot_token)
    temp_me = await temp_bot.get_me()
    bot_username = temp_me.username or f"id_{temp_me.id}"
    await temp_bot.disconnect()

    config["bot_token"], config["bot_username"], config["is_first_run"] = bot_token, bot_username, True
    with open(CONFIG_FILE, "w", encoding="utf-8") as f: json.dump(config, f, indent=4)
    return bot_token, bot_username, True

def load_modules():
    """Динамически подгружает все .py файлы из папок system_modules и modules"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    folders_to_load = {'system_modules': True, 'modules': False}

    importlib.invalidate_caches()
    for folder_name, is_system in folders_to_load.items():
        folder_path = os.path.join(base_dir, folder_name)
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
            continue
        if folder_path not in sys.path:
            sys.path.insert(0, folder_path)

        for file in os.listdir(folder_path):
            if file.endswith('.py') and not file.startswith('__'):
                module_name = file[:-3]
                
                # Игнорируем installer.py и terminal.py, если запущен с флагом --host
                if module_name in ("installer", "terminal") and "--host" in sys.argv:
                    core_logger.info(f"⏭ Пропуск модуля '{module_name}' (активен флаг --host)")
                    continue
                    
                try:
                    importlib.import_module(module_name)
                    if is_system:
                        if module_name in modules_repo["modules"]:
                            modules_repo["modules"][module_name]["system"] = True
                        else:
                            modules_repo["modules"][module_name] = {
                                "name": module_name.capitalize(), "desc": "Системный", "commands": {}, "system": True
                            }
                    icon = "⚙️" if is_system else "📦"
                    core_logger.info(f"{icon} Модуль '{module_name}' загружен!")
                except Exception as e:
                    core_logger.error(f"Ошибка при загрузке модуля '{module_name}':\n{traceback.format_exc()}")

async def notify_after_restart():
    restart_info = pop_restart_info()
    if not restart_info: return
    try:
        elapsed = time.time() - restart_info.get("time", time.time())
        custom_text = restart_info.get("custom_text")
        if custom_text:
            text = f"{custom_text}\n⏱ Заняло: `{elapsed:.2f} сек.`"
        else:
            text = f"✅ **Успешно перезагружен!**\n⏱ Заняло: `{elapsed:.2f} сек.`"
        
        edited = False

        # 1. Если сообщение было инлайн (из callback-кнопки встроенного бота)
        inline_data = restart_info.get("inline_message_id")
        if inline_data:
            bot = get_bot()
            if bot:
                try:
                    from telethon import types
                    cls_name = inline_data.get("type", "InputBotInlineMessageID")
                    cls = getattr(types, cls_name, types.InputBotInlineMessageID)
                    input_id = cls(
                        dc_id=inline_data.get("dc_id", 0),
                        id=inline_data.get("id", 0),
                        access_hash=inline_data.get("access_hash", 0)
                    )
                    await bot.edit_message(entity=input_id, text=text)
                    edited = True
                except Exception as ex_inline:
                    core_logger.debug(f"notify_after_restart inline edit exception: {ex_inline}")

        # 2. Обычное сообщение в чате
        if not edited and restart_info.get("chat_id") and restart_info.get("message_id"):
            try:
                await client.edit_message(restart_info["chat_id"], restart_info["message_id"], text)
                edited = True
            except Exception:
                pass

            if not edited:
                bot = get_bot()
                if bot:
                    try:
                        await bot.edit_message(restart_info["chat_id"], restart_info["message_id"], text)
                        edited = True
                    except Exception:
                        pass

        # 3. Fallback в ЛС владельцу через бота
        if not edited:
            await send_bot_notification(text)
    except Exception as e:
        core_logger.debug(f"notify_after_restart exception: {e}")

async def handle_incoming_messages(event):
    if not event.out or not event.raw_text: return
    text = event.raw_text

    prefix = get_prefix()
    if text.startswith(prefix):
        parts = text.split(maxsplit=1)
        first_word = parts[0]
        if not first_word.startswith(prefix):
            return
        cmd = first_word[len(prefix):]
        if not cmd:
            return
        args = parts[1] if len(parts) > 1 else ""
        if cmd in modules_repo["commands"]:
            core_logger.debug(f"⚡ Вызов команды {prefix}{cmd} (аргументы: '{args}') в чате {event.chat_id}")
            if is_rate_limited():
                rem = get_rate_limit_remaining()
                core_logger.warning(f"Лимит API! Запрос {prefix}{cmd} заблокирован (осталось: {rem} сек.)")
                await event.edit(f"⚠️ **Лимит API!**\n⏱ Осталось: `{rem} сек.`")
                return
            try:
                await check_cmd_rate_limit()
                start_t = time.perf_counter()
                await modules_repo["commands"][cmd](client, event, args)
                exec_dur = time.perf_counter() - start_t
                core_logger.debug(f"✅ Команда {prefix}{cmd} выполнена за {exec_dur:.3f}с")
            except errors.FloodWaitError as e:
                core_logger.error(f"FloodWait в {prefix}{cmd}: {e.seconds} сек. (чат {event.chat_id})")
                await apply_flood_wait(e.seconds, source=f"Команда {prefix}{cmd}")
                await event.edit(f"⚠️ **FloodWait:** `{e.seconds} сек.`")
            except Exception as e:
                core_logger.error(f"Ошибка при выполнении команды [{prefix}{cmd}] в чате {event.chat_id}:\n{traceback.format_exc()}")
                await event.edit(f"**Ошибка [{prefix}{cmd}]:**\n`{e}`")

async def send_bot_status_msg(event):
    uptime_sec = int(time.time() - get_userbot_start_time())
    h, rem = divmod(uptime_sec, 3600)
    m, s = divmod(rem, 60)
    text = (
        f"⚙️ **Статус UBTG:**\n\n"
        f"⏱ **Аптайм:** `{h}ч {m}м {s}с`\n"
        f"📦 **Модулей:** `{len(modules_repo['modules'])}`\n"
        f"🛠 **Команд:** `{len(modules_repo['commands'])}`\n\n"
        f"💻 CPU: `{psutil.cpu_percent()}%` | RAM: `{psutil.virtual_memory().percent}%`"
    )
    await event.respond(text)

def setup_core_bot_handlers(bot_client):
    @bot_client.on(events.InlineQuery)
    async def inline_handler(event):
        query = event.text.strip()
        core_logger.debug(f"🔍 Инлайн-запрос: '{query}' от {event.sender_id}")
        if query in inline_payload_cache:
            payload = inline_payload_cache.pop(query)
            await event.answer([event.builder.article("Вывод", text=payload["text"], buttons=payload.get("buttons"))], cache_time=0)
        else:
            await event.answer([event.builder.article("Ассистент", text="🤖 Бот активен!", buttons=[[Button.url("TG", "https://t.me")]])], cache_time=1)

    @bot_client.on(events.CallbackQuery)
    async def callback_handler(event):
        data = event.data.decode("utf-8") if isinstance(event.data, bytes) else str(event.data)
        core_logger.debug(f"🔘 Нажата инлайн-кнопка (data: '{data}') от пользователя {event.sender_id}")
        if data == "bot_status": return await (event.answer("Загрузка...") or send_bot_status_msg(event))
        elif data == "bot_ping": return await (event.answer("Замер...") or event.respond("🏓 ПОНГ!"))
        
        for prefix in sorted(callback_handlers.keys(), key=len, reverse=True):
            if data.startswith(prefix):
                try:
                    await callback_handlers[prefix](event, data)
                    core_logger.debug(f"✅ Callback '{prefix}' успешно обработан")
                except errors.MessageNotModifiedError: await event.answer()
                except Exception as e:
                    core_logger.error(f"Ошибка в callback '{prefix}' (data: '{data}'):\n{traceback.format_exc()}")
                    await event.answer(f"Ошибка: {e}", alert=True)
                return

    @bot_client.on(events.NewMessage(incoming=True))
    async def bot_pm_handler(event):
        if not event.is_private or (get_owner_id() and event.sender_id != get_owner_id()): return
        text = event.raw_text.strip()
        core_logger.debug(f"📩 Сообщение боту в ЛС: '{text}' от {event.sender_id}")
        if text.startswith("/start") or text.startswith("/help"):
            me = await bot_client.get_me()
            await event.respond(f"👋 **Привет! Я бот UBTG.** (@{me.username})\n\n`/status` — Статус\n`/ping` — Отклик\n`/restart` — Перезапуск",
                                buttons=[[Button.inline("📊 Статус", b"bot_status"), Button.inline("🏓 Пинг", b"bot_ping")]])
        elif text.startswith("/status"): await send_bot_status_msg(event)
        elif text.startswith("/ping"): await event.respond("🏓 ПОНГ!")
        elif text.startswith("/restart"):
            msg = await event.respond("🔄 **Перезапуск...**")
            await restart_userbot(client, event.chat_id, msg.id)

async def _run_bg_task_safe(task_func, client):
    """Обертка для безопасного запуска фоновых задач с логированием ошибок."""
    task_name = getattr(task_func, '__name__', str(task_func))
    try:
        await task_func(client)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        core_logger.error(f"Ошибка в фоновой задаче '{task_name}':\n{traceback.format_exc()}")

async def main():
    set_main_client(client)
    await client.connect()

    # 2. Этап асинхронных Pre-Auth модулей (Генерация QR, web ui и тд.)
    for mod in init_mods:
        if hasattr(mod, 'pre_auth'):
            await mod.pre_auth(client)

    # Проверка, что после всех init-модулей мы действительно авторизованы
    if not await client.is_user_authorized():
        core_logger.error("Клиент не авторизован после выполнения init-модулей!")
        sys.exit(1)

    me = await client.get_me()
    set_owner_id(me.id)
    core_logger.info(f"👤 Владелец: {me.first_name} (ID: {me.id})")

    bot_token, bot_username, is_first_run = await auto_setup_bot(client, me)
    try:
        bot_client = TelegramClient('bot_session', API_ID, API_HASH, proxy=proxy_config)
        await bot_client.start(bot_token=bot_token)
    except Exception as e:
        if os.path.exists("bot_session.session"): os.remove("bot_session.session")
        bot_client = TelegramClient('bot_session', API_ID, API_HASH, proxy=proxy_config)
        await bot_client.start(bot_token=bot_token)
        
    set_bot(bot_client, bot_username)
    setup_core_bot_handlers(bot_client)
    core_logger.info(f"🤖 Бот активен (@{bot_username})!")

    # === НАСТРОЙКА ЧАТА ЛОГОВ ubtg-logs ===
    try:
        log_chat_id = await ensure_log_chat(client, bot_client, bot_username)
        core_logger.info(f"📁 Чат логов ubtg-logs готов (ID: {log_chat_id})")
    except Exception as ex_chat:
        core_logger.error(f"Не удалось инициализировать чат ubtg-logs: {ex_chat}")

    try: await client.send_message(f"@{bot_username}", "/start"); await asyncio.sleep(0.5)
    except: pass

    core_logger.info("Загружаем модули...")
    load_modules()

    client.add_event_handler(handle_incoming_messages, events.NewMessage(outgoing=True))
    core_logger.info(f"Запущено команд: {len(modules_repo['commands'])}")
    for task in modules_repo["background_tasks"]:
        core_logger.debug(f"🚀 Старт фоновой задачи: {getattr(task, '__name__', str(task))}")
        asyncio.create_task(_run_bg_task_safe(task, client))
    
    await notify_after_restart()

    if not os.path.exists("restart_info.json"):
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        
        # Обновляем конфиг перед отправкой уведомления, чтобы сохранить is_first_run корректно
        CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core_conf.json")
        config = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)

        if is_first_run:
            await send_bot_notification(f"🎉 **UBTG Установлен!**\n\n👤 [{me.first_name}](tg://user?id={me.id})\n📦 Модулей: `{len(modules_repo['modules'])}`")
            config["is_first_run"] = False
            with open(CONFIG_FILE, "w", encoding="utf-8") as f: json.dump(config, f, indent=4)
        else:
            await send_bot_notification(f"🚀 **UBTG Запущен!**\n\n👤 [{me.first_name}](tg://user?id={me.id})\n📦 Модулей: `{len(modules_repo['modules'])}`")

    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\n[Core] Остановлен.")
    finally:
        try:
            bot = get_bot()
            if bot and bot.is_connected(): client.loop.run_until_complete(bot.disconnect())
        except: pass
        try:
            if client and client.is_connected(): client.loop.run_until_complete(client.disconnect())
        except: pass

#вторая тестовая обнова