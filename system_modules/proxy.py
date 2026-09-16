# ---------------------------------------------------------------------------------
# Name: Proxy Manager
# Description: Системный модуль для управления сетевым прокси ядра, пулом пользовательских прокси и маршрутизацией для модулей.
# ---------------------------------------------------------------------------------

import os
import sys
import json
import time
import asyncio
from telethon import Button, errors
from registry import (
    register_cmd,
    register_callback,
    set_module_meta,
    init_config,
    get_config,
    set_config,
    get_logger,
    get_prefix,
    send_inline,
    get_bot_username,
    get_main_client,
    get_owner_id,
    get_core_proxy_config,
    get_core_proxy_url,
    set_core_proxy,
    clear_core_proxy,
    get_module_proxy_permissions,
    get_module_proxy_permission,
    set_module_proxy_permission,
    is_module_proxy_enabled,
    normalize_module_name,
    is_host_mode,
    # Функции пула пользовательских прокси и маршрутизации
    get_user_proxies,
    get_user_proxy,
    add_user_proxy,
    remove_user_proxy,
    get_module_proxy_route,
    set_module_proxy_route,
    get_module_effective_proxy_url,
    check_proxy_ping
)

logger = get_logger("ProxyManager")

# Метаданные системного модуля
set_module_meta(
    name="Proxy Manager",
    desc="Управление системным прокси ядра, пользовательскими прокси и сетевой маршрутизацией модулей.",
    system=True
)

init_config("proxy_manager", {
    "modules": {},
    "core_proxy": None,
    "user_proxies": {}
})

# Кэш последних результатов пинга {alias: {"ok": bool, "ping_ms": float, "ip": str, "error": str}}
_cached_pings = {}


def is_authorized_user(sender_id: int) -> bool:
    """Проверяет права владельца юзербота."""
    owner_id = get_owner_id()
    return not owner_id or sender_id == owner_id


def get_installed_user_modules() -> list[str]:
    """Возвращает список установленных пользовательских модулей из modules/."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mod_dir = os.path.join(base_dir, "modules")
    if not os.path.exists(mod_dir):
        return []
    modules = []
    for f in sorted(os.listdir(mod_dir)):
        if f.endswith(".py") and not f.startswith("__"):
            modules.append(f[:-3])
    return modules


def build_proxy_menu(pings_cache: dict = None) -> tuple[str, list]:
    """Генерирует текст и инлайн-кнопки главного меню управления сетевыми прокси."""
    p = get_prefix() or "."
    core_proxy_url = get_core_proxy_url()
    core_cfg = get_core_proxy_config()
    host_mode = is_host_mode()
    user_proxies = get_user_proxies()
    installed = get_installed_user_modules()
    pings = pings_cache if pings_cache is not None else _cached_pings

    # 1. Системный прокси
    if core_proxy_url and core_cfg:
        status_line = f"🟢 **Активен:** `{core_proxy_url}`" + (" *(зафиксирован хостом)*" if host_mode else "")
        proto_line = f"⚙️ **Тип:** `{core_cfg.get('proxy_type', 'http').upper()}` | **Хост:** `{core_cfg.get('addr')}:{core_cfg.get('port')}`"
        if "core" in pings:
            cp = pings["core"]
            proto_line += f" | ⚡️ `{cp.get('ping_ms')} ms`" if cp.get("ok") else " | ⚡️ `🔴 оффлайн`"
    else:
        status_line = "⚪️ **Не настроен** *(модули выходят в сеть напрямую)*"
        proto_line = "ℹ️ *Прокси хостинга не задан.*" if host_mode else f"💡 Настройка: `{p}proxy set <url>`"

    # 2. Пользовательские прокси
    u_lines = []
    if user_proxies:
        for alias, u in user_proxies.items():
            ping_str = ""
            if alias in pings:
                pr = pings[alias]
                if pr.get("ok"):
                    ip_part = f", IP: `{pr.get('ip')}`" if pr.get("ip") else ""
                    ping_str = f" — 🟢 `{pr.get('ping_ms')} ms`{ip_part}"
                else:
                    ping_str = f" — 🔴 `сбой: {pr.get('error', 'ошибка')}`"
            u_lines.append(f"• 🏷 `{alias}` («{u.get('name', alias)}»): `{u.get('url')}`{ping_str}")
    else:
        u_lines.append("⚪️ *Пользовательские прокси еще не добавлены.*")
        u_lines.append(f"💡 Добавить свой прокси: `{p}proxy add <алиас> <url> [название]`")

    # 3. Маршрутизация модулей
    modules_text = []
    if not installed:
        modules_text.append("🤷‍♂️ *Нет установленных пользовательских модулей в `modules/`.*")
    else:
        for mod in installed:
            canon = normalize_module_name(mod)
            route = get_module_proxy_route(canon)
            if route == "core":
                status_icon = "🌐"
                status_desc = "**Системный (Core)**"
            elif route in user_proxies:
                status_icon = "🟢"
                u_item = user_proxies[route]
                status_desc = f"🏷 **{u_item.get('name', route)}** (`{route}`)"
            else:
                status_icon = "⚪️"
                status_desc = "*Прямое подключение (ВЫКЛ)*"
            modules_text.append(f"• {status_icon} `{mod}`: {status_desc}")

    host_banner = (
        "🛡 **Режим хостинга (`--host`):** системный прокси зафиксирован.\n"
        "Вы можете свободно подключать свои прокси для скачанных модулей!\n\n"
    ) if host_mode else ""

    text = (
        "🌐 **Сетевой Прокси-Менеджер Модулей**\n\n"
        f"{host_banner}"
        "🔌 **Системный прокси (Core):**\n"
        f"{status_line}\n"
        f"{proto_line}\n\n"
        f"🗂 **Пользовательские прокси ({len(user_proxies)}):**\n"
        + "\n".join(u_lines) + "\n\n"
        "📋 **Маршрутизация трафика модулей:**\n"
        "*(Нажмите на кнопку модуля ниже, чтобы выбрать его прокси-сервер)*\n\n"
        + "\n".join(modules_text)
    )

    buttons = []

    # Кнопки для каждого модуля (открывают выбор маршрута)
    if installed:
        cur_row = []
        for mod in installed:
            canon = normalize_module_name(mod)
            route = get_module_proxy_route(canon)
            if route == "core":
                btn_lbl = f"🌐 {mod[:8]} (Core)"
            elif route in user_proxies:
                btn_lbl = f"🏷 {mod[:8]} ({route[:6]})"
            else:
                btn_lbl = f"⚪️ {mod[:8]} (Выкл)"
            cur_row.append(Button.inline(btn_lbl, f"px_mod:{canon}".encode()))
            if len(cur_row) == 2:
                buttons.append(cur_row)
                cur_row = []
        if cur_row:
            buttons.append(cur_row)

        # Действия со всеми модулями и пинг
        buttons.append([
            Button.inline("⚡️ Проверить пинги", b"px_ping_all"),
            Button.inline("🚫 Отключить всем", b"px_all_off")
        ])

    # Нижняя панель действий
    buttons.append([
        Button.inline("➕ Добавить прокси", b"px_add_help"),
        Button.inline("⚙️ Справка", b"px_help")
    ])
    buttons.append([
        Button.inline("❌ Закрыть", b"px_close")
    ])

    return text, buttons


def build_module_route_menu(module_name: str) -> tuple[str, list]:
    """Генерирует инлайн-меню выбора маршрута прокси для конкретного модуля."""
    canon = normalize_module_name(module_name)
    cur_route = get_module_proxy_route(canon)
    core_url = get_core_proxy_url()
    user_proxies = get_user_proxies()

    if cur_route == "core":
        cur_desc = f"🌐 Системный прокси (`{core_url or 'не настроен'}`)"
    elif cur_route in user_proxies:
        u = user_proxies[cur_route]
        cur_desc = f"🏷 Пользовательский: «{u.get('name', cur_route)}» (`{u.get('url')}`)"
    else:
        cur_desc = "⚪️ Прямое подключение без прокси (ВЫКЛ)"

    text = (
        f"⚙️ **Маршрутизация прокси для модуля `{canon}`**\n\n"
        f"Текущий маршрут: **{cur_desc}**\n\n"
        "Выберите прокси-сервер для этого модуля:\n"
        "• **Прямое подключение** — модуль выходит в сеть напрямую.\n"
        "• **Системный (Core)** — общий прокси ядра юзербота.\n"
        "• **Пользовательский** — один из добавленных вами независимых прокси."
    )

    buttons = []

    # 1. Прямое vs Системный
    off_mark = " ✅" if cur_route == "off" else ""
    core_mark = " ✅" if cur_route == "core" else ""
    buttons.append([
        Button.inline(f"⚪️ Прямое (Выкл){off_mark}", f"px_setr:{canon}:off".encode()),
        Button.inline(f"🌐 Системный{core_mark}", f"px_setr:{canon}:core".encode())
    ])

    # 2. Пользовательские прокси
    if user_proxies:
        u_row = []
        for alias, u in user_proxies.items():
            mark = " ✅" if cur_route == alias else ""
            disp = u.get("name", alias)[:14]
            u_row.append(Button.inline(f"🏷 {disp}{mark}", f"px_setr:{canon}:{alias}".encode()))
            if len(u_row) == 2:
                buttons.append(u_row)
                u_row = []
        if u_row:
            buttons.append(u_row)

    buttons.append([Button.inline("⬅️ Назад в меню", b"px_back")])
    return text, buttons


# ==============================================================================
# CALLBACK-ОБРАБОТЧИКИ
# ==============================================================================

@register_callback("px_mod:")
async def cb_proxy_module_select(event, data):
    """Открывает меню выбора маршрута для конкретного модуля."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    await event.answer()
    raw = data[len("px_mod:"):].decode("utf-8") if isinstance(data, bytes) else data[len("px_mod:"):]
    mod_name = raw.strip()

    text, buttons = build_module_route_menu(mod_name)
    try:
        await event.edit(text, buttons=buttons, link_preview=False)
    except errors.MessageNotModifiedError:
        pass
    except Exception as e:
        logger.error(f"Ошибка открытия меню модуля: {e}")


@register_callback("px_setr:")
async def cb_proxy_set_route(event, data):
    """Назначает выбранный маршрут прокси модулю."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    raw = data[len("px_setr:"):].decode("utf-8") if isinstance(data, bytes) else data[len("px_setr:"):]
    parts = raw.split(":", 1)
    if len(parts) != 2:
        return await event.answer("⚠️ Неверный формат команды", alert=True)

    mod_name, route = parts[0], parts[1]
    canon = normalize_module_name(mod_name)
    set_module_proxy_route(canon, route)

    if route == "off":
        toast = f"⚪️ Прямое подключение для '{canon}'"
    elif route == "core":
        toast = f"🌐 Системный прокси для '{canon}'"
    else:
        toast = f"🏷 Прокси '{route}' для '{canon}'"

    await event.answer(toast)
    text, buttons = build_module_route_menu(canon)
    try:
        await event.edit(text, buttons=buttons, link_preview=False)
    except errors.MessageNotModifiedError:
        pass
    except Exception as e:
        logger.error(f"Ошибка сохранения маршрута: {e}")


@register_callback("px_ping_all")
async def cb_proxy_ping_all(event, data):
    """Проверяет пинг всех настроенных прокси (ядра и пользовательских)."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    await event.answer("⏳ Измеряю пинг всех прокси...")

    tasks = {}
    # Проверяем прокси ядра
    if get_core_proxy_config():
        tasks["core"] = check_proxy_ping("core")

    # Проверяем пользовательские прокси
    user_proxies = get_user_proxies()
    for alias in user_proxies:
        tasks[alias] = check_proxy_ping(alias)

    if tasks:
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for alias, res in zip(tasks.keys(), results):
            if isinstance(res, dict):
                _cached_pings[alias] = res
            else:
                _cached_pings[alias] = {"ok": False, "error": str(res)}

    text, buttons = build_proxy_menu(pings_cache=_cached_pings)
    try:
        await event.edit(text, buttons=buttons, link_preview=False)
    except errors.MessageNotModifiedError:
        pass
    except Exception as e:
        logger.error(f"Ошибка обновления пингов: {e}")


@register_callback("px_all_off")
async def cb_proxy_all_off(event, data):
    """Отключение прокси для всех модулей."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    installed = get_installed_user_modules()
    for mod in installed:
        set_module_proxy_route(mod, "off")

    await event.answer("🚫 Все модули переведены на прямое подключение!")
    text, buttons = build_proxy_menu()
    try:
        await event.edit(text, buttons=buttons, link_preview=False)
    except errors.MessageNotModifiedError:
        pass


@register_callback("px_add_help")
async def cb_proxy_add_help(event, data):
    """Справка по добавлению пользовательских прокси."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    await event.answer()
    p = get_prefix() or "."
    help_text = (
        "➕ **Как добавить свой прокси:**\n\n"
        "Вы можете добавить один или несколько собственных прокси и привязать каждый к нужным модулям.\n\n"
        f"**Команда добавления:**\n"
        f"`{p}proxy add <алиас> <url> [понятное название]`\n\n"
        "**Примеры:**\n"
        f"• `{p}proxy add de socks5://user:pass@1.2.3.4:1080 Германия SOCKS5`\n"
        f"• `{p}proxy add nl http://user:pass@5.6.7.8:8080 Нидерланды HTTP`\n"
        f"• `{p}proxy add fast socks5://127.0.0.1:1080 Локальный Fast`\n\n"
        f"**Удалить прокси:** `{p}proxy del <алиас>`\n"
        f"**Проверить пинг:** `{p}proxy ping <алиас>`\n\n"
        "💡 *Служебные и скачанные модули изолированы: неверный прокси не повлияет на работу всего юзербота!*"
    )
    buttons = [[Button.inline("⬅️ Назад в меню", b"px_back")]]
    try:
        await event.edit(help_text, buttons=buttons, link_preview=False)
    except errors.MessageNotModifiedError:
        pass


@register_callback("px_help")
async def cb_proxy_help(event, data):
    """Подробная справка по всем возможностям прокси-менеджера."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    await event.answer()
    p = get_prefix() or "."
    host_mode = is_host_mode()

    if host_mode:
        host_desc = (
            "🔒 **Режим хостинга (`--host`):**\n"
            "Системный прокси ядра зафиксирован хостом и защищен от изменений.\n"
            "Однако вы можете добавлять неограниченное количество **своих пользовательских прокси** "
            "и привязывать их к скачанным модулям!\n\n"
        )
    else:
        host_desc = (
            "🔌 **Системный прокси ядра:**\n"
            f"• `{p}proxy set <url>` — задать прокси ядра\n"
            f"• `{p}proxy clear` — удалить прокси ядра\n\n"
        )

    help_text = (
        "🌐 **Справка по Прокси-Менеджеру**\n\n"
        f"{host_desc}"
        "🗂 **Управление пользовательскими прокси:**\n"
        f"• `{p}proxy add <алиас> <url> [название]` — добавить прокси в пул\n"
        f"• `{p}proxy del <алиас>` — удалить прокси из пула\n"
        f"• `{p}proxy list` — список всех прокси с пингом и статусом\n"
        f"• `{p}proxy ping [алиас | url]` — проверить пинг и внешний IP\n\n"
        "📋 **Маршрутизация для модулей:**\n"
        f"• `{p}proxy route <модуль> <алиас | core | off>` — назначить прокси модулю\n"
        f"• `{p}proxy on <модуль>` — включить прокси для модуля\n"
        f"• `{p}proxy off <модуль>` — выключить прокси (прямое подключение)\n"
        f"• `{p}proxy all off` — отключить прокси для всех модулей\n\n"
        "💡 *В интерактивном меню все маршруты можно переключать в 1 клик!*"
    )

    buttons = [[Button.inline("⬅️ Назад в меню", b"px_back")]]
    try:
        await event.edit(help_text, buttons=buttons, link_preview=False)
    except errors.MessageNotModifiedError:
        pass


@register_callback("px_back")
async def cb_proxy_back(event, data):
    """Возврат в главное меню прокси."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    await event.answer()
    text, buttons = build_proxy_menu()
    try:
        await event.edit(text, buttons=buttons, link_preview=False)
    except errors.MessageNotModifiedError:
        pass


@register_callback("px_close")
async def cb_proxy_close(event, data):
    """Закрытие меню прокси."""
    try:
        await event.delete()
    except Exception:
        try:
            await event.edit("❌ **Меню прокси закрыто.**", buttons=None)
        except Exception:
            pass


# ==============================================================================
# КОМАНДЫ CLI (.proxy ...)
# ==============================================================================

@register_cmd("proxy", desc="Управление сетевыми прокси и маршрутизацией модулей")
@register_cmd("proxymods", desc="Алиас для .proxy")
@register_cmd("modproxy", desc="Алиас для .proxy")
async def proxy_manager_cmd(client, event, args):
    """Главная команда управления прокси."""
    p = get_prefix() or "."
    raw_args = (args or "").strip()

    # Открытие интерактивного меню
    if not raw_args or raw_args.lower() in ("menu", "ui"):
        text, buttons = build_proxy_menu()
        bot_username = get_bot_username()
        if bot_username:
            try:
                await send_inline(client, event.chat_id, text, buttons=buttons, reply_to=event.reply_to_msg_id)
                await event.delete()
                return
            except Exception as e:
                logger.debug(f"send_inline fallback в .proxy: {e}")
        return await event.edit(text, link_preview=False)

    parts = raw_args.split()
    action = parts[0].lower()

    # 1. Список прокси / Статус
    if action in ("status", "list", "pool"):
        user_proxies = get_user_proxies()
        core_url = get_core_proxy_url()
        lines = ["🌐 **Список настроенных прокси-серверов:**\n"]

        # Core
        lines.append("🔌 **Системный прокси ядра:**")
        if core_url:
            lines.append(f"• URL: `{core_url}`" + (" *(зафиксирован хостом)*" if is_host_mode() else ""))
        else:
            lines.append("• *Не настроен (прямое подключение)*")
        lines.append("")

        # User pool
        lines.append(f"🗂 **Пользовательские прокси ({len(user_proxies)}):**")
        if not user_proxies:
            lines.append("• *Нет добавленных пользовательских прокси.*")
        else:
            perms = get_module_proxy_permissions()
            for alias, u in user_proxies.items():
                using_mods = [m for m, r in perms.items() if r == alias]
                mods_str = f", модули: `{', '.join(using_mods)}`" if using_mods else ", *не назначен*"
                lines.append(f"• 🏷 `{alias}` («**{u.get('name', alias)}**»)")
                lines.append(f"  URL: `{u.get('url')}`{mods_str}")
        lines.append(f"\n💡 Добавить: `{p}proxy add <алиас> <url> [название]`")
        return await event.edit("\n".join(lines), link_preview=False)

    # 2. Добавление пользовательского прокси
    if action == "add":
        if len(parts) < 3:
            return await event.edit(
                f"❌ Неверный формат команды!\n\n"
                f"💡 Формат: `{p}proxy add <алиас> <url> [название]`\n"
                f"Пример: `{p}proxy add de socks5://user:pass@1.2.3.4:1080 Мой сервер в Германии`"
            )
        alias = parts[1].strip()
        url = parts[2].strip()
        name = " ".join(parts[3:]).strip() if len(parts) > 3 else f"Прокси {alias}"

        ok, msg, entry = add_user_proxy(alias, url, name)
        if not ok:
            return await event.edit(f"❌ **Ошибка добавления:** {msg}")

        # Проверяем пинг сразу при добавлении
        await event.edit(f"⏳ Прокси сохранен! Проверяю пинг и подключение к `{alias}`...")
        ping_res = await check_proxy_ping(entry)
        _cached_pings[alias] = ping_res

        if ping_res.get("ok"):
            ip_info = f"\n🌐 **Внешний IP:** `{ping_res.get('ip')}`" if ping_res.get("ip") else ""
            status_text = f"🟢 **Пинг:** `{ping_res.get('ping_ms')} ms` (Успешно){ip_info}"
        else:
            status_text = f"⚠️ **Сбой подключения:** `{ping_res.get('error')}`"

        return await event.edit(
            f"✅ **Пользовательский прокси успешно сохранен!**\n\n"
            f"🏷 **Алиас:** `{alias}`\n"
            f"📝 **Название:** «{entry['name']}»\n"
            f"🌐 **URL:** `{entry['url']}`\n"
            f"{status_text}\n\n"
            f"💡 Чтобы назначить его модулю: `{p}proxy route <модуль> {alias}`"
        )

    # 3. Удаление пользовательского прокси
    if action in ("del", "delete", "rm"):
        if len(parts) < 2:
            return await event.edit(f"❌ Укажите алиас прокси: `{p}proxy del <алиас>`")
        alias = parts[1].strip()
        if not remove_user_proxy(alias):
            return await event.edit(f"❌ Прокси с алиасом `{alias}` не найден в списке.")
        _cached_pings.pop(alias, None)
        return await event.edit(
            f"✅ Пользовательский прокси `{alias}` удален.\n"
            f"Модули, использовавшие его, переведены на прямое подключение."
        )

    # 4. Проверка пинга и подключения
    if action in ("ping", "check", "test"):
        target = parts[1].strip() if len(parts) > 1 else None

        if target:
            await event.edit(f"⏳ Измеряю пинг к `{target}`...")
            res = await check_proxy_ping(target)
            if res.get("ok"):
                ip_info = f"\n🌐 **Выходной IP:** `{res.get('ip')}`" if res.get("ip") else ""
                tunnel_info = " (Туннелирование OK)" if res.get("tunnel_ok") else ""
                return await event.edit(
                    f"⚡️ **Результат проверки прокси `{target}`:**\n\n"
                    f"🟢 **Статус:** Доступен{tunnel_info}\n"
                    f"⏱ **Пинг:** `{res.get('ping_ms')} ms`"
                    f"{ip_info}"
                )
            else:
                return await event.edit(
                    f"⚡️ **Результат проверки прокси `{target}`:**\n\n"
                    f"🔴 **Статус:** Недоступен\n"
                    f"⚠️ **Ошибка:** `{res.get('error')}`"
                )
        else:
            # Пинг всех прокси
            await event.edit("⏳ Измеряю пинг всех настроенных прокси...")
            user_proxies = get_user_proxies()
            tasks = {}
            if get_core_proxy_config():
                tasks["core"] = check_proxy_ping("core")
            for a in user_proxies:
                tasks[a] = check_proxy_ping(a)

            if not tasks:
                return await event.edit("ℹ️ Нет настроенных прокси для проверки пинга.")

            res_list = await asyncio.gather(*tasks.values(), return_exceptions=True)
            report = ["⚡️ **Сводка проверки пинга прокси:**\n"]
            for a, r in zip(tasks.keys(), res_list):
                if isinstance(r, dict) and r.get("ok"):
                    _cached_pings[a] = r
                    ip_str = f" | IP: `{r.get('ip')}`" if r.get("ip") else ""
                    report.append(f"• `{a}`: 🟢 `{r.get('ping_ms')} ms`{ip_str}")
                else:
                    err = r.get("error") if isinstance(r, dict) else str(r)
                    _cached_pings[a] = {"ok": False, "error": err}
                    report.append(f"• `{a}`: 🔴 `сбой ({err})`")

            return await event.edit("\n".join(report))

    # 5. Маршрутизация конкретного модуля (route / set <модуль> <маршрут>)
    if action in ("route", "setmod"):
        if len(parts) < 3:
            return await event.edit(
                f"❌ Формат: `{p}proxy route <модуль> <алиас | core | off>`\n"
                f"Пример: `{p}proxy route gemini de`"
            )
        mod_name = normalize_module_name(parts[1])
        target_route = parts[2].strip()

        # Валидация маршрута
        if target_route not in ("off", "core") and target_route not in get_user_proxies():
            avail = ["off", "core"] + list(get_user_proxies().keys())
            return await event.edit(
                f"❌ Неизвестный прокси `{target_route}`!\n"
                f"💡 Доступные варианты: `{', '.join(avail)}`"
            )

        set_module_proxy_route(mod_name, target_route)
        if target_route == "off":
            desc = "⚪️ Прямое подключение (прокси отключен)"
        elif target_route == "core":
            desc = "🌐 Системный прокси ядра"
        else:
            u_info = get_user_proxy(target_route)
            desc = f"🏷 «{u_info.get('name', target_route)}» (`{target_route}`)"

        return await event.edit(f"✅ Для модуля `{mod_name}` назначен маршрут:\n**{desc}**")

    # 6. Установка прокси ядра или модуля через `set`
    if action == "set":
        # Если 3 аргумента: .proxy set <модуль> <маршрут>
        if len(parts) >= 3:
            mod_name = normalize_module_name(parts[1])
            target_route = parts[2].strip()
            if target_route not in ("off", "core") and target_route not in get_user_proxies():
                avail = ["off", "core"] + list(get_user_proxies().keys())
                return await event.edit(f"❌ Неизвестный прокси `{target_route}`! Доступно: `{', '.join(avail)}`")
            set_module_proxy_route(mod_name, target_route)
            return await event.edit(f"✅ Для модуля `{mod_name}` назначен прокси `{target_route}`.")

        # Если 2 аргумента: .proxy set <url> (настройка системного прокси)
        if len(parts) == 2:
            if is_host_mode():
                return await event.edit(
                    "🔒 **Действие заблокировано!**\n\n"
                    "Юзербот запущен с флагом `--host` (режим хостинга).\n"
                    "Изменение системного прокси ядра запрещено хостом.\n\n"
                    f"💡 Вы можете добавить свой прокси для скачанных модулей:\n"
                    f"`{p}proxy add <алиас> <url> [название]`"
                )
            proxy_url = parts[1].strip()
            result = set_core_proxy(proxy_url)
            if not result:
                return await event.edit(f"❌ Неверный формат URL прокси `{proxy_url}`!")
            active_url = get_core_proxy_url()
            return await event.edit(
                f"✅ **Системный прокси ядра настроен!**\n\n"
                f"🌐 **URL:** `{active_url}`\n"
                f"⚙️ **Тип:** `{result.get('proxy_type', 'http').upper()}`"
            )

        return await event.edit(f"❌ Укажите параметры: `{p}proxy set <url>` или `{p}proxy route <модуль> <прокси>`")

    # 7. Очистка системного прокси
    if action in ("clear", "unset"):
        if is_host_mode():
            return await event.edit("🔒 **В режиме хостинга удаление прокси ядра запрещено.**")
        clear_core_proxy()
        return await event.edit("✅ **Системный прокси ядра удален.**")

    # 8. Быстрое включение .proxy on <модуль>
    if action in ("on", "enable"):
        if len(parts) < 2:
            return await event.edit(f"❌ Укажите имя модуля: `{p}proxy on <модуль>` (или `{p}proxy all on`)")
        if parts[1].lower() == "all":
            # Назначаем core или первый пользовательский прокси
            avail_route = "core" if get_core_proxy_config() else (next(iter(get_user_proxies()), "off"))
            for m in get_installed_user_modules():
                set_module_proxy_route(m, avail_route)
            return await event.edit(f"🟢 **Прокси (`{avail_route}`) включен для всех модулей!**")
        mod_name = normalize_module_name(parts[1])
        avail_route = "core" if get_core_proxy_config() else (next(iter(get_user_proxies()), "off"))
        set_module_proxy_route(mod_name, avail_route)
        return await event.edit(f"🟢 **Прокси для модуля `{mod_name}` включен (`{avail_route}`)!**")

    # 9. Быстрое выключение .proxy off <модуль>
    if action in ("off", "disable"):
        if len(parts) < 2:
            return await event.edit(f"❌ Укажите имя модуля: `{p}proxy off <модуль>` (или `{p}proxy all off`)")
        if parts[1].lower() == "all":
            for m in get_installed_user_modules():
                set_module_proxy_route(m, "off")
            return await event.edit("⚪️ **Прокси отключен для всех модулей (прямое подключение).**")
        mod_name = normalize_module_name(parts[1])
        set_module_proxy_route(mod_name, "off")
        return await event.edit(f"⚪️ **Прокси для модуля `{mod_name}` отключен (прямое подключение).**")

    # 10. Массовые команды .proxy all
    if action == "all":
        if len(parts) >= 2 and parts[1].lower() in ("on", "enable"):
            avail_route = parts[2].strip() if len(parts) > 2 else ("core" if get_core_proxy_config() else next(iter(get_user_proxies()), "off"))
            for m in get_installed_user_modules():
                set_module_proxy_route(m, avail_route)
            return await event.edit(f"🟢 **Прокси (`{avail_route}`) включен для всех модулей!**")
        elif len(parts) >= 2 and parts[1].lower() in ("off", "disable"):
            for m in get_installed_user_modules():
                set_module_proxy_route(m, "off")
            return await event.edit("⚪️ **Прокси отключен для всех модулей!**")

    return await event.edit(
        f"❓ Неизвестная команда `{raw_args}`.\n\n"
        f"💡 Доступные команды:\n"
        f"• `{p}proxy` — интерактивное меню\n"
        f"• `{p}proxy add <алиас> <url> [название]` — добавить свой прокси\n"
        f"• `{p}proxy del <алиас>` — удалить свой прокси\n"
        f"• `{p}proxy ping [алиас | url]` — проверить пинг и IP\n"
        f"• `{p}proxy route <модуль> <алиас | core | off>` — маршрут для модуля\n"
        f"• `{p}proxy list` — список всех прокси"
    )


# ==============================================================================
# КОМАНДЫ ДЛЯ ПОДТВЕРЖДЕНИЯ ПРОКСИ ПРИ УСТАНОВКЕ МОДУЛЯ
# ==============================================================================

@register_cmd("allowproxy", desc="Разрешить использование прокси для ожидающего модуля")
@register_cmd("proxyyes", desc="Алиас для .allowproxy")
async def allow_proxy_cmd(client, event, args):
    """Подтверждение использования прокси при установке модуля."""
    from gh_installer import _pending_proxy_confirmations, perform_module_install
    chat_key = str(event.chat_id)
    item = _pending_proxy_confirmations.pop(chat_key, None)
    if not item:
        now = time.time()
        for ck, val in list(_pending_proxy_confirmations.items()):
            if now - val["time"] < 300:
                item = val
                _pending_proxy_confirmations.pop(ck, None)
                break

    if not item or (time.time() - item["time"] > 300):
        return await event.edit("⚠️ Нет ожидающих подтверждения запросов на прокси.")

    alias = item["alias"]
    allow_break = item.get("allow_break", False)

    # Назначаем системный или первый доступный пользовательский прокси
    avail_route = "core" if get_core_proxy_config() else (next(iter(get_user_proxies()), "core"))
    set_module_proxy_route(alias, avail_route)
    await event.edit(f"🟢 `Прокси ({avail_route}) разрешен! Продолжаю установку '{alias}'...`")
    await perform_module_install(
        client,
        event.chat_id,
        event.id,
        alias,
        event=event,
        allow_break_system_packages=allow_break,
        proxy_choice=True
    )


@register_cmd("denyproxy", desc="Отклонить использование прокси для ожидающего модуля (прямое подключение)")
@register_cmd("proxyno", desc="Алиас для .denyproxy")
async def deny_proxy_cmd(client, event, args):
    """Отклонение использования прокси при установке модуля."""
    from gh_installer import _pending_proxy_confirmations, perform_module_install
    chat_key = str(event.chat_id)
    item = _pending_proxy_confirmations.pop(chat_key, None)
    if not item:
        now = time.time()
        for ck, val in list(_pending_proxy_confirmations.items()):
            if now - val["time"] < 300:
                item = val
                _pending_proxy_confirmations.pop(ck, None)
                break

    if not item or (time.time() - item["time"] > 300):
        return await event.edit("⚠️ Нет ожидающих подтверждения запросов на прокси.")

    alias = item["alias"]
    allow_break = item.get("allow_break", False)
    set_module_proxy_route(alias, "off")
    await event.edit(f"⚪️ `Прямое подключение сохранено. Продолжаю установку '{alias}'...`")
    await perform_module_install(
        client,
        event.chat_id,
        event.id,
        alias,
        event=event,
        allow_break_system_packages=allow_break,
        proxy_choice=False
    )
