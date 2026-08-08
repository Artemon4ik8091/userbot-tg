import os
import sys
import time
import json
import asyncio
import importlib
import random
import re
import psutil
from datetime import datetime
from telethon import TelegramClient, events, errors, Button
from telethon.sessions import MemorySession
import qrcode

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
    is_rate_limited,
    get_rate_limit_remaining,
    apply_flood_wait,
    check_cmd_rate_limit
)

# --- НАСТРОЙКИ КОНФИГА ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "core_conf.json")

def get_proxy_config(config):
    """
    Извлекает настройки прокси из конфигурации ядра (core_conf.json).
    Прокси не обязателен. Если прокси не задан в конфиге или выключен, возвращает None.
    """
    if not isinstance(config, dict):
        return None

    proxy = config.get("proxy")
    if not proxy or not isinstance(proxy, dict):
        return None

    addr = proxy.get("addr")
    port = proxy.get("port")
    
    if not addr or port is None:
        return None

    try:
        port = int(port)
    except (ValueError, TypeError):
        return None

    proxy_type = str(proxy.get("proxy_type", "http")).lower()

    proxy_dict = {
        'proxy_type': proxy_type,
        'addr': str(addr),
        'port': port
    }

    if proxy.get("username"):
        proxy_dict['username'] = str(proxy.get("username"))
    if proxy.get("password"):
        proxy_dict['password'] = str(proxy.get("password"))
    if "rdns" in proxy:
        proxy_dict['rdns'] = bool(proxy.get("rdns"))

    return proxy_dict

def load_or_create_config():
    """Загружает конфиг core_conf.json, а если его нет - запрашивает данные у пользователя и создает."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                if "app_id" in config and "hash_id" in config:
                    return config
        except Exception as e:
            print(f"[Core] ⚠️ Ошибка при чтении конфига: {e}. Создаем новый.")

    print("\n" + "="*50)
    print("=== ПЕРВЫЙ ЗАПУСК: НАСТРОЙКА API ТЕЛЕГРАМА ===")
    print("Получить app_id и hash_id можно на сайте https://my.telegram.org")
    print("="*50)
    
    while True:
        try:
            app_id_input = input("Введите ваш app_id (только цифры): ").strip()
            app_id = int(app_id_input)
            break
        except ValueError:
            print("❌ Ошибка: app_id должен состоять только из цифр. Попробуйте еще раз.")
            
    hash_id = input("Введите ваш hash_id (строка): ").strip()
    
    config_data = {
        "app_id": app_id,
        "hash_id": hash_id
    }

    print("\n💡 Настройка юзернейма встроенного Telegram бота.")
    print("   Оставьте пустым (нажмите Enter) для авто-поиска или случайной генерации.")
    desired_bot_un = input("Введите желаемый юзернейм бота (например, my_cool_ub_bot) [Enter — авто]: ").strip()
    if desired_bot_un:
        config_data["desired_bot_username"] = desired_bot_un.lstrip("@")

    use_proxy = input("\nХотите настроить прокси? (y/N): ").strip().lower()
    if use_proxy in ['y', 'yes', 'да', 'д']:
        proxy_type = input("Тип прокси (http/socks5/socks4) [по умолчанию http]: ").strip().lower() or "http"
        addr = input("Адрес прокси (например, 127.0.0.1): ").strip()
        while True:
            try:
                port_str = input("Порт прокси: ").strip()
                if not port_str:
                    print("❌ Ошибка: порт не может быть пустым.")
                    continue
                port = int(port_str)
                break
            except ValueError:
                print("❌ Ошибка: порт должен быть числом.")
        username = input("Логин прокси (оставьте пустым, если не требуется): ").strip()
        password = input("Пароль прокси (оставьте пустым, если не требуется): ").strip()

        proxy_dict = {
            "proxy_type": proxy_type,
            "addr": addr,
            "port": port
        }
        if username:
            proxy_dict["username"] = username
        if password:
            proxy_dict["password"] = password

        config_data["proxy"] = proxy_dict
    
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4, ensure_ascii=False)
        
    print(f"[Core] ✅ Настройки успешно сохранены в файл {CONFIG_FILE}!\n")
    return config_data

def save_core_config(config_data):
    """Сохраняет актуальный конфиг ядра в core_conf.json"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4, ensure_ascii=False)

# Получаем креды до инициализации клиента
raw_config = load_or_create_config()
API_ID = int(raw_config["app_id"])
API_HASH = raw_config["hash_id"]
proxy_config = get_proxy_config(raw_config)

if proxy_config:
    print(f"[Core] 🌐 Используется прокси ({proxy_config['proxy_type']}://{proxy_config['addr']}:{proxy_config['port']})")
else:
    print("[Core] 🌐 Прокси не используется (не прописан в конфиге)")

# Инициализируем юзербот клиента
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
    """
    Проверяет наличие токена бота в core_conf.json.
    Если его нет — обращается к @BotFather:
    1. Пробует создать бота с указанным пользователем юзернеймом (если был введён).
    2. Если юзернейм занят/не указан — ищет существующего бота юзербота (/token).
    3. Если подхоящий бот не найден — создаёт нового бота со случайным юзернеймом.
    """
    config = load_or_create_config()
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

            # --- ЭТАП 1: Попытка создания с указанным юзернеймом ---
            if desired_username:
                target_un = desired_username
                if not target_un.lower().endswith("bot"):
                    target_un = f"{target_un}_bot"

                print(f"[Core] 🎯 Пытаемся создать бота с указанным юзернеймом: @{target_un}...")
                await conv.send_message("/newbot")
                resp = await conv.get_response()

                if any(phrase in resp.text.lower() for phrase in ["name for your bot", "how are we going to call it", "new bot"]):
                    bot_name = f"{me.first_name}'s Assistant"
                    await conv.send_message(bot_name)
                    resp = await conv.get_response()

                    await conv.send_message(target_un)
                    resp = await conv.get_response()

                    match = re.search(r"(\d+:[A-Za-z0-9_-]+)", resp.text)
                    if match:
                        found_token = match.group(1)
                        found_username = target_un
                        print(f"[Core] 🎉 Успешно создан бот с указанным юзернеймом @{found_username}!")
                    else:
                        print(f"[Core] ⚠️ Юзернейм @{target_un} занят или недоступен.")
                        print("[Core] 🔄 Сбрасываем диалог и переходим к авто-поиску/созданию со случайным юзернеймом...")
                        await conv.send_message("/cancel")
                        try:
                            await conv.get_response()
                        except Exception:
                            pass

            # --- ЭТАП 2: Поиск существующего подходящего бота ---
            if not found_token:
                print("[Core] 🔍 Проверяем наличие существующих ботов...")
                await conv.send_message("/token")
                resp = await conv.get_response()

                no_bots_keywords = ["don't have any", "no bots", "у вас нет", "нет ботов", "you have no"]
                has_no_bots = any(kw in resp.text.lower() for kw in no_bots_keywords)

                if not has_no_bots:
                    candidate_bots = []

                    if resp.buttons:
                        for row_idx, row in enumerate(resp.buttons):
                            for col_idx, btn in enumerate(row):
                                btn_text = btn.text or ""
                                m = re.search(r"@?([A-Za-z0-9_]+_bot)", btn_text, re.IGNORECASE)
                                if m:
                                    candidate_bots.append((m.group(1), btn))

                    if not candidate_bots:
                        for m in re.finditer(r"@([A-Za-z0-9_]+_bot)", resp.text, re.IGNORECASE):
                            candidate_bots.append((m.group(1), None))

                    if candidate_bots:
                        # Ищем бота, предназначенного именно для юзербота (содержащего _ub_, userbot или assistant)
                        chosen = None
                        for b in candidate_bots:
                            un_lower = b[0].lower()
                            if "_ub_" in un_lower or "userbot" in un_lower or "assistant" in un_lower:
                                chosen = b
                                break

                        if chosen:
                            target_username, btn_obj = chosen
                            print(f"[Core] 💡 Найден подходящий бот юзербота: @{target_username}")

                            if btn_obj is not None:
                                try:
                                    await btn_obj.click()
                                except Exception:
                                    await conv.send_message(f"@{target_username}")
                            else:
                                await conv.send_message(f"@{target_username}")

                            token_resp = await conv.get_response()
                            match = re.search(r"(\d+:[A-Za-z0-9_-]+)", token_resp.text)
                            if match:
                                found_token = match.group(1)
                                found_username = target_username
                                print(f"[Core] ✅ Успешно получен токен для существующего бота @{found_username}")
                        else:
                            print("[Core] ℹ️ Подходящих ботов для юзербота не найдено среди существующих.")

            # --- ЭТАП 3: Создание нового бота со случайным юзернеймом ---
            if not found_token:
                print("[Core] ➕ Создаем нового бота со случайным юзернеймом...")
                await conv.send_message("/newbot")
                resp = await conv.get_response()

                if any(phrase in resp.text.lower() for phrase in ["name for your bot", "how are we going to call it", "new bot"]):
                    bot_name = f"{me.first_name}'s Assistant"
                    await conv.send_message(bot_name)
                    resp = await conv.get_response()

                    base_un = (me.username or f"user_{me.id}").lower()
                    suffix = random.randint(1000, 9999)
                    target_username = f"{base_un}_ub_{suffix}_bot"
                    await conv.send_message(target_username)
                    resp = await conv.get_response()

                    match = re.search(r"(\d+:[A-Za-z0-9_-]+)", resp.text)
                    if match:
                        found_token = match.group(1)
                        found_username = target_username
                        print(f"[Core] 🎉 Новый бот @{found_username} успешно создан через @BotFather!")

            # --- ЭТАП 3: Настройка Inline-режима ---
            if found_token and found_username:
                try:
                    await conv.send_message("/setinline")
                    setinline_resp = await conv.get_response()
                    if any(w in setinline_resp.text.lower() for w in ["choose a bot", "выберите бота", "which bot"]):
                        await conv.send_message(f"@{found_username}")
                        setinline_resp = await conv.get_response()

                    if any(w in setinline_resp.text.lower() for w in ["placeholder", "текст", "label", "empty inline"]):
                        await conv.send_message("Search")
                        await conv.get_response()
                except Exception as ex_inline:
                    print(f"[Core] ⚠️ Внимание при проверке inline режима: {ex_inline}")

                config["bot_token"] = found_token
                config["bot_username"] = found_username
                config["is_first_run"] = True
                save_core_config(config)
                return found_token, found_username, True

    except Exception as e:
        print(f"[Core] ⚠️ Не удалось автоматически настроить бота через @BotFather: {e}")

    # Фолбэк: если автонастройка не сработало, просим ввод токена ручками
    print("\n" + "="*50)
    print("=== НАСТРОЙКА ВСТРОЕННОГО ТЕЛЕГРАМ БОТА ===")
    print("Создайте бота через @BotFather и вставьте его токен ниже.")
    print("="*50)

    bot_token = input("Введите Bot Token (например: 123456789:ABC...): ").strip()
    
    # Извлекаем username бота через временный клиент в оперативной памяти
    temp_bot = TelegramClient(MemorySession(), API_ID, API_HASH, proxy=proxy_config)
    await temp_bot.start(bot_token=bot_token)
    temp_me = await temp_bot.get_me()
    bot_username = temp_me.username or f"id_{temp_me.id}"
    await temp_bot.disconnect()

    config["bot_token"] = bot_token
    config["bot_username"] = bot_username
    config["is_first_run"] = True
    save_core_config(config)
    print(f"[Core] ✅ Бот @{bot_username} сохранен в {CONFIG_FILE}!\n")
    return bot_token, bot_username, True

def load_modules():
    """Динамически подгружает все .py файлы из папок system_modules и modules"""
    base_dir = os.path.dirname(__file__)
    
    folders_to_load = {
        'system_modules': True,
        'modules': False
    }

    for folder_name, is_system in folders_to_load.items():
        folder_path = os.path.join(base_dir, folder_name)
        
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
            print(f"[Core] 📁 Создана папка для модулей: {folder_name}/")
            continue

        if folder_path not in sys.path:
            sys.path.insert(0, folder_path)

        for file in os.listdir(folder_path):
            if file.endswith('.py') and not file.startswith('__'):
                module_name = file[:-3]
                try:
                    importlib.import_module(module_name)
                    
                    if is_system:
                        if module_name in modules_repo["modules"]:
                            modules_repo["modules"][module_name]["system"] = True
                        else:
                            modules_repo["modules"][module_name] = {
                                "name": module_name.capitalize(),
                                "desc": "Системный модуль (без описания)",
                                "commands": {},
                                "system": True
                            }
                    
                    icon = "⚙️" if is_system else "📦"
                    print(f"[Core] {icon} Модуль '{module_name}' успешно загружен!")
                except Exception as e:
                    print(f"[Core] ❌ Ошибка при загрузке модуля '{module_name}': {e}")

async def notify_after_restart():
    """Сообщает об успешном рестарте юзербота."""
    restart_info = pop_restart_info()
    if not restart_info:
        return

    try:
        elapsed = time.time() - restart_info.get("time", time.time())
        text = f"✅ **Успешно перезагружен!**\n⏱ Заняло: `{elapsed:.2f} сек.`"
        await client.edit_message(
            restart_info["chat_id"],
            restart_info["message_id"],
            text
        )
        print("[Core] Уведомление о перезагрузке отправлено.")
        await send_bot_notification(text)
    except Exception as e:
        print(f"[Core] ⚠️ Не удалось отредактировать сообщение после рестарта: {e}")

async def handle_incoming_messages(event):
    """Слушает сообщения и триггерит команды модулей юзербота"""
    if not event.out:
        return

    text = event.raw_text
    print(f"[Debug] Отправлено сообщение: '{text}'")

    if text.startswith('.'):
        parts = text.split(maxsplit=1)
        cmd = parts[0][1:]
        args = parts[1] if len(parts) > 1 else ""

        print(f"[Debug] Обнаружена команда: .{cmd} с аргументами: '{args}'")

        if cmd in modules_repo["commands"]:
            if is_rate_limited():
                rem = get_rate_limit_remaining()
                mins, secs = divmod(rem, 60)
                await event.edit(
                    f"⚠️ **Запросы к Telegram API временно ограничены!**\n"
                    f"⏱ Осталось подождать: `{rem} сек.` (`{mins}м {secs}с`)\n"
                    f"🤖 Уведомление об ограничении отправлено от лица бота."
                )
                return

            try:
                await check_cmd_rate_limit()
                await modules_repo["commands"][cmd](client, event, args)
                print(f"[Debug] Команда .{cmd} успешно выполнена!")
            except errors.FloodWaitError as e:
                print(f"[Core] ⚠️ Пойман FloodWaitError: {e.seconds} сек.")
                await apply_flood_wait(e.seconds, source=f"Команда .{cmd}")
                await event.edit(
                    f"⚠️ **Сработало ограничение Telegram API (FloodWait):** `{e.seconds} сек.`\n"
                    f"🤖 Уведомление с подробностями отправлено от лица бота."
                )
            except PermissionError as pe:
                await event.edit(f"⚠️ {pe}")
            except Exception as e:
                print(f"[Debug] ❌ Ошибка при выполнении .{cmd}: {e}")
                await event.edit(f"**Ошибка в модуле [.{cmd}]:**\n`{e}`")

async def send_bot_status_msg(event):
    """Отправляет актуальный статус юзербота в бот"""
    uptime_sec = int(time.time() - get_userbot_start_time())
    hours, remainder = divmod(uptime_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}ч {minutes}м {seconds}с"

    cpu_usage = psutil.cpu_percent()
    ram_usage = psutil.virtual_memory().percent

    modules_count = len(modules_repo["modules"])
    commands_count = len(modules_repo["commands"])

    status_text = (
        f"⚙️ **Статус UBTG Userbot:**\n\n"
        f"⏱ **Аптайм:** `{uptime_str}`\n"
        f"📦 **Загружено модулей:** `{modules_count}`\n"
        f"🛠 **Всего команд:** `{commands_count}`\n\n"
        f"💻 **Нагрузка системы:**\n"
        f"• CPU: `{cpu_usage}%`\n"
        f"• RAM: `{ram_usage}%`"
    )
    await event.respond(status_text)

def setup_core_bot_handlers(bot_client):
    """Подключает базовые обработчики Telegram Бота"""

    @bot_client.on(events.InlineQuery)
    async def inline_handler(event):
        query = event.text.strip()
        if query in inline_payload_cache:
            payload = inline_payload_cache.pop(query)
            builder = event.builder
            result = builder.article(
                title="Инлайн Вывод",
                text=payload["text"],
                buttons=payload.get("buttons")
            )
            await event.answer([result], cache_time=0)
        else:
            builder = event.builder
            result = builder.article(
                title="Юзербот Ассистент",
                text="🤖 Встроенный бот юзербота активен!",
                buttons=[[Button.url("Юзербот", "https://t.me")]]
            )
            await event.answer([result], cache_time=1)

    @bot_client.on(events.CallbackQuery)
    async def callback_handler(event):
        data = event.data.decode("utf-8") if isinstance(event.data, bytes) else str(event.data)

        if data == "bot_status":
            await event.answer("Загрузка...")
            await send_bot_status_msg(event)
            return
        elif data == "bot_ping":
            await event.answer("Замер пинга...")
            start = time.time()
            diff = (time.time() - start) * 1000
            await event.respond(f"🏓 **ПОНГ!**\n⏱ Отклик бота: `{diff:.2f} мс`")
            return

        # Сортируем префиксы по длине от самых длинных к коротким, чтобы длинные совпадали первыми
        sorted_prefixes = sorted(callback_handlers.keys(), key=len, reverse=True)
        for prefix in sorted_prefixes:
            if data.startswith(prefix):
                handler = callback_handlers[prefix]
                try:
                    await handler(event, data)
                except errors.MessageNotModifiedError:
                    await event.answer()
                except Exception as e:
                    if "not modified" in str(e).lower():
                        await event.answer()
                    else:
                        print(f"[Bot] ❌ Ошибка в обработчике кнопок ({prefix}): {e}")
                        await event.answer(f"Ошибка: {e}", alert=True)
                return

    @bot_client.on(events.NewMessage)
    async def bot_pm_handler(event):
        if not event.is_private:
            return

        owner_id = get_owner_id()
        if owner_id and event.sender_id != owner_id:
            await event.respond("⛔ **Доступ запрещен.** Это персональный бот юзербота.")
            return

        text = event.raw_text.strip()
        if text.startswith("/start") or text.startswith("/help"):
            bot_me = await bot_client.get_me()
            start_msg = (
                f"👋 **Привет! Я встроенный Telegram Бот твоего UBTG Userbot.** (@{bot_me.username})\n\n"
                f"Я являюсь неотъемлемой частью ядра юзербота и присылаю системные уведомления.\n\n"
                f"🛠 **Доступные команды:**\n"
                f"• `/status` — Статус юзербота и системы\n"
                f"• `/ping` — Проверка отклика бота\n"
                f"• `/restart` — Дистанционный перезапуск юзербота\n"
            )
            buttons = [
                [Button.inline("📊 Статус", b"bot_status"), Button.inline("🏓 Пинг", b"bot_ping")]
            ]
            await event.respond(start_msg, buttons=buttons)

        elif text.startswith("/status"):
            await send_bot_status_msg(event)

        elif text.startswith("/ping"):
            start = time.time()
            msg = await event.respond("🏓 Замер пинга...")
            diff = (time.time() - start) * 1000
            await msg.edit(f"🏓 **ПОНГ!**\n⏱ Отклик бота: `{diff:.2f} мс`")

        elif text.startswith("/restart"):
            await event.respond("🔄 **Запущен дистанционный перезапуск юзербота...**")
            save_restart_info(event.chat_id, event.id)
            try:
                await client.disconnect()
            except Exception:
                pass
            python = sys.executable
            script = os.path.abspath(sys.argv[0])
            os.execv(python, [python, script] + sys.argv[1:])

async def main():
    await client.connect()

    if not await client.is_user_authorized():
        print("=== Запуск генерации QR-кода ===")
        qr_login = await client.qr_login()
        qr = qrcode.QRCode()
        qr.add_data(qr_login.url)
        print("\n" + "="*60)
        qr.print_tty()
        print("="*60 + "\n")
        try:
            await qr_login.wait(timeout=60)
            print("Ура! Успешно залогинились!")
        except errors.SessionPasswordNeededError:
            password = input("У тебя включен облачный пароль (2FA). Введи его сюда: ")
            await client.sign_in(password=password)
        except Exception as e:
            print(f"Ошибка при входе: {e}")
            await client.disconnect()
            return

    me = await client.get_me()
    set_owner_id(me.id)
    print(f"[Core] 👤 Владелец: {me.first_name} (ID: {me.id})")

    # Инициализация и автонастройка встроенного Telegram Бота
    bot_token, bot_username, is_first_run = await auto_setup_bot(client, me)
    try:
        bot_client = TelegramClient(
            'bot_session',
            API_ID,
            API_HASH,
            device_model="MacBook Pro",
            system_version="macOS 14.5",
            app_version="10.11.1",
            proxy=proxy_config
        )
        await bot_client.start(bot_token=bot_token)
    except Exception as e:
        print(f"[Core] ⚠️ Сессия бота повреждена или несовместима ({e}). Пересоздаем bot_session.session...")
        if os.path.exists("bot_session.session"):
            try:
                os.remove("bot_session.session")
            except Exception:
                pass
        
        bot_client = TelegramClient(
            'bot_session',
            API_ID,
            API_HASH,
            device_model="MacBook Pro",
            system_version="macOS 14.5",
            app_version="10.11.1",
            proxy=proxy_config
        )
        await bot_client.start(bot_token=bot_token)
        
    set_bot(bot_client, bot_username)
    setup_core_bot_handlers(bot_client)
    print(f"[Core] 🤖 Встроенный Telegram Бот активен (@{bot_username})!")

    # --- ИНИЦИАЛИЗАЦИЯ ДИАЛОГА (Рукопожатие) ---
    try:
        await client.send_message(f"@{bot_username}", "/start")
        await asyncio.sleep(0.5)
    except Exception as e:
        print(f"[Core] ⚠️ Не удалось отправить приветственный /start боту: {e}")

    print("\n[Core] Загружаем модули...")
    load_modules()

    client.add_event_handler(handle_incoming_messages, events.NewMessage(outgoing=True))

    print(f"\n[Core] Запущено команд: {len(modules_repo['commands'])}")
    print(f"[Core] Запущено фоновых задач: {len(modules_repo['background_tasks'])}")
    print("\n=== UBTG Userbot полностью готов к работе! ===")

    for task in modules_repo["background_tasks"]:
        asyncio.create_task(task(client))

    await notify_after_restart()

    # Уведомление владельца через бота при обычном запуске
    if not os.path.exists("restart_info.json"):
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

        if is_first_run:
            welcome_text = (
                f"🎉 **Поздравляем с установкой UBTG Userbot!**\n\n"
                f"🤖 Ваш персональный Telegram Бот (@{bot_username}) успешно привязан и активирован.\n\n"
                f"👤 **Владелец:** [{me.first_name}](tg://user?id={me.id})\n"
                f"⏱ **Время установки:** `{now_str}`\n"
                f"📦 **Загружено модулей:** `{len(modules_repo['modules'])}`\n"
                f"⚙️ **Загружено команд:** `{len(modules_repo['commands'])}`\n\n"
                f"💬 Отправляйте `/start` или `/status` в этот диалог для управления юзерботом."
            )
            await send_bot_notification(welcome_text)
            config = load_or_create_config()
            config["is_first_run"] = False
            save_core_config(config)
        else:
            startup_text = (
                f"🚀 **UBTG Userbot успешно запущен!**\n\n"
                f"👤 **Владелец:** [{me.first_name}](tg://user?id={me.id})\n"
                f"⏱ **Время:** `{now_str}`\n"
                f"📦 **Модулей:** `{len(modules_repo['modules'])}`\n"
                f"⚙️ **Команд:** `{len(modules_repo['commands'])}`"
            )
            await send_bot_notification(startup_text)

    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\n[Core] Юзербот остановлен вручную.")
    finally:
        try:
            bot = get_bot()
            if bot and bot.is_connected():
                client.loop.run_until_complete(bot.disconnect())
        except Exception:
            pass

        try:
            if client and client.is_connected():
                client.loop.run_until_complete(client.disconnect())
        except Exception:
            pass