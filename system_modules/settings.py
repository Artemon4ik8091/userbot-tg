import os
import sys
import json
import re
import asyncio
from registry import register_cmd, set_module_meta, restart_userbot

# Системный модуль управления настройками ядра и встроенного бота
set_module_meta(
    name="Settings",
    desc="Системное управление настройками ядра юзербота и токеном встроенного бота.",
    system=True
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "core_conf.json")

def load_core_config():
    """Загружает текущую конфигурацию из core_conf.json"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_core_config(config_data):
    """Сохраняет обновленную конфигурацию в core_conf.json"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4, ensure_ascii=False)

@register_cmd("settings", desc="Показывает системную информацию о настройках ядра")
async def settings_cmd(client, event, args):
    config = load_core_config()
    bot_username = config.get("bot_username", "Не привязан")
    app_id = config.get("app_id", "Н/Д")
    proxy = config.get("proxy")
    
    if proxy and isinstance(proxy, dict) and proxy.get("addr") and proxy.get("port"):
        p_type = proxy.get("proxy_type", "http")
        proxy_status = f"{p_type}://{proxy.get('addr')}:{proxy.get('port')}"
    else:
        proxy_status = "Не используется"

    text = (
        "⚙️ **Системные Настройки Юзербота**\n\n"
        f"🤖 **Встроенный бот:** `@{bot_username}`\n"
        f"🔑 **App ID:** `{app_id}`\n"
        f"🌐 **Прокси:** `{proxy_status}`\n\n"
        "🛠 **Команды управления:**\n"
        "• `.resettoken` — Сбросить токен бота в @BotFather и перезапустить ядро\n"
    )
    await event.edit(text)

@register_cmd("resettoken", desc="Сбрасывает токен встроенного бота через @BotFather, обновляет core_conf.json и перезапускает бот.")
async def reset_token_cmd(client, event, args):
    await event.edit("🔄 **Запрос нового токена для бота у @BotFather...**")

    config = load_core_config()
    bot_username = config.get("bot_username")

    if not bot_username:
        return await event.edit("❌ **Ошибка:** В `core_conf.json` не найден юзернейм бота (`bot_username`).")

    try:
        bf_entity = await client.get_input_entity("BotFather")
        new_token = None

        async with client.conversation(bf_entity, timeout=30) as conv:
            # 1. Отправляем /revoke в @BotFather
            await conv.send_message("/revoke")
            resp = await conv.get_response()

            # 2. Выбираем юзернейм бота
            if any(w in resp.text.lower() for w in ["choose a bot", "выберите бота", "revoke"]):
                clicked = False
                if resp.buttons:
                    for row in resp.buttons:
                        for btn in row:
                            if bot_username.lower() in (btn.text or "").lower():
                                try:
                                    await btn.click()
                                    clicked = True
                                    break
                                except Exception:
                                    pass
                        if clicked:
                            break

                if not clicked:
                    await conv.send_message(f"@{bot_username}")

                resp = await conv.get_response()

            # 3. Находим сгенерированный токен
            match = re.search(r"(\d+:[A-Za-z0-9_-]+)", resp.text)
            if match:
                new_token = match.group(1)

        if new_token:
            config["bot_token"] = new_token
            save_core_config(config)

            msg_text = (
                f"✅ **Токен бота @{bot_username} успешно сброшен!**\n\n"
                f"🔑 **Новый токен:** `{new_token}`\n"
                f"⚙️ Обновлено в `core_conf.json`."
            )
            await event.edit(f"{msg_text}\n\n🔄 Перезапускаем юзербота для привязки нового токена...")
            await restart_userbot(client, event.chat_id, event.id, custom_text=msg_text)

        else:
            await event.edit(f"❌ **Не удалось извлечь новый токен от @BotFather.**\nОтвет: `{resp.text}`")

    except Exception as e:
        await event.edit(f"❌ **Ошибка при взаимодействии с @BotFather:**\n`{e}`")

@register_cmd("resetbottoken", desc="Сбросить токен бота (аналог .resettoken)")
async def reset_bot_token_alias(client, event, args):
    await reset_token_cmd(client, event, args)
