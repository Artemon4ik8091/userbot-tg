import os
import sys
import json
import time
import traceback
from telethon import Button, errors
from registry import (
    register_cmd,
    register_callback,
    set_module_meta,
    global_config,
    load_config,
    save_config,
    get_config,
    set_config,
    delete_config,
    normalize_module_name,
    restart_userbot,
    send_inline,
    get_owner_id,
    get_bot,
    get_main_client,
    modules_repo,
    get_logger
)

logger = get_logger("Config")

set_module_meta(
    name="Config",
    desc="Интерактивное управление глобальной конфигурацией модулей с разделением на системные/пользовательские и инлайн-редактором.",
    system=True
)

# --- УПРАВЛЕНИЕ СЕССИЯМИ ИНЛАЙН-МЕНЮ ---
_cfg_sessions = {}
ITEMS_PER_PAGE_MAIN = 6
ITEMS_PER_PAGE_MOD = 6


def is_authorized_user(sender_id):
    """Проверяет права владельца для изменения конфигурации."""
    owner_id = get_owner_id()
    if not owner_id:
        return True
    return sender_id == owner_id


def _clean_old_sessions():
    """Удаляет сессии старше 2 часов."""
    now = time.time()
    expired = [sid for sid, data in _cfg_sessions.items() if now - data.get("created_at", 0) > 7200]
    for sid in expired:
        _cfg_sessions.pop(sid, None)


def parse_value(val: str):
    """
    Превращает строку в подходящий тип данных:
    булево (True/False), целое (int), дробное (float), JSON список/словарь или строку.
    """
    if not isinstance(val, str):
        return val

    val_stripped = val.strip()
    val_lower = val_stripped.lower()

    # 1. Булевы значения
    if val_lower in ('true', 'on', 'yes', 'y', 'да', 'вкл'):
        return True
    if val_lower in ('false', 'off', 'no', 'n', 'нет', 'выкл'):
        return False
    if val_lower in ('none', 'null', 'nil'):
        return None

    # 2. Попытка распарсить JSON структуры (списки, словари)
    if (val_stripped.startswith('[') and val_stripped.endswith(']')) or \
       (val_stripped.startswith('{') and val_stripped.endswith('}')):
        try:
            return json.loads(val_stripped)
        except Exception:
            pass

    # 3. Целые числа
    try:
        return int(val_stripped)
    except ValueError:
        pass

    # 4. Числа с плавающей точкой
    try:
        return float(val_stripped)
    except ValueError:
        pass

    # 5. Строки с кавычками "..." или '...' -> снимаем внешние кавычки
    if (val_stripped.startswith('"') and val_stripped.endswith('"') and len(val_stripped) >= 2) or \
       (val_stripped.startswith("'") and val_stripped.endswith("'") and len(val_stripped) >= 2):
        return val_stripped[1:-1]

    # 6. Обычная строка
    return val_stripped


def _resolve_module_meta(mod_name):
    """
    Возвращает метаданные модуля (имя, иконка, описание, системный статус).
    """
    canon = normalize_module_name(mod_name)
    
    mod_info = modules_repo.get("modules", {}).get(canon)
    if not mod_info:
        for cand in [canon, mod_name, f"modules.{canon}", f"system_modules.{canon}"]:
            if cand in modules_repo.get("modules", {}):
                mod_info = modules_repo["modules"][cand]
                break

    known_system_names = (
        "module_ping", "module_info", "module_update", "config", "settings",
        "installer", "gh_installer", "update", "info", "help", "restart", "terminal"
    )

    if mod_info:
        display_name = mod_info.get("name") or canon.capitalize()
        desc = mod_info.get("desc") or "Описание отсутствует"
        is_system = mod_info.get("system", False)
    else:
        display_name = canon.replace("module_", "").capitalize()
        desc = "Модуль конфигурации"
        is_system = canon in known_system_names or canon.startswith("system_")

    icon = "⚙️" if is_system else "📦"
    return {
        "canon_name": canon,
        "display_name": display_name,
        "icon": icon,
        "desc": desc,
        "is_system": is_system
    }


def _is_system_module(mod_name):
    """Проверяет, является ли модуль системным."""
    return _resolve_module_meta(mod_name)["is_system"]


def _get_all_modules_list():
    """
    Возвращает упорядоченный список всех доступных для настройки модулей строго без дубликатов.
    """
    load_config()
    modules_set = set()

    for m in global_config.keys():
        modules_set.add(normalize_module_name(m))

    for m in modules_repo.get("modules", {}).keys():
        modules_set.add(normalize_module_name(m))

    # Сортируем: сначала те, у которых уже есть параметры в global_config
    def sort_key(m):
        has_cfg = m in global_config and len(global_config[m]) > 0
        return (0 if has_cfg else 1, m.lower())

    return sorted(list(modules_set), key=sort_key)


def _get_module_keys_list(mod_name):
    """Возвращает отсортированный список ключей для указанного модуля."""
    load_config()
    canon = normalize_module_name(mod_name)
    mod_dict = global_config.get(canon, {})
    return sorted(list(mod_dict.keys()))


def _get_or_create_session(session_id=None, chat_id=None):
    """Получает или создает сессию инлайн-меню."""
    _clean_old_sessions()
    now = time.time()
    all_mods = _get_all_modules_list()
    sys_mods = [m for m in all_mods if _is_system_module(m)]
    usr_mods = [m for m in all_mods if not _is_system_module(m)]
    
    if session_id and session_id in _cfg_sessions:
        session = _cfg_sessions[session_id]
        session["modules_list"] = all_mods
        session["sys_modules"] = sys_mods
        session["user_modules"] = usr_mods
        return session_id, session

    sid = session_id or f"c_{int(now * 1000) % 100000000}"
    session = {
        "session_id": sid,
        "created_at": now,
        "chat_id": chat_id,
        "modules_list": all_mods,
        "sys_modules": sys_mods,
        "user_modules": usr_mods,
        "current_cat": "sys",
        "cat_page": 0,
        "keys_page": 0,
        "selected_mod_idx": 0,
        "selected_key_idx": 0
    }
    _cfg_sessions[sid] = session
    return sid, session


def _format_val_preview(val):
    """Краткое форматирование значения для кнопок."""
    if isinstance(val, bool):
        return "🟢 True" if val else "🔴 False"
    if val is None:
        return "🔘 None"
    if isinstance(val, (int, float)):
        return f"🔢 {val}"
    if isinstance(val, str):
        if len(val) > 16:
            return f'📝 "{val[:13]}..."'
        return f'📝 "{val}"'
    if isinstance(val, list):
        return f"📋 [{len(val)}]"
    if isinstance(val, dict):
        return f"📦 {{{len(val)}}}"
    val_str = str(val)
    return val_str[:16] + "..." if len(val_str) > 16 else val_str


def _get_val_type_name(val):
    """Понятное название типа данных."""
    if isinstance(val, bool):
        return "Логический (True/False)"
    if isinstance(val, int):
        return "Целое число (int)"
    if isinstance(val, float):
        return "Дробное число (float)"
    if isinstance(val, str):
        return "Строка (str)"
    if isinstance(val, list):
        return "Список (list/JSON)"
    if isinstance(val, dict):
        return "Словарь (dict/JSON)"
    if val is None:
        return "Пустое значение (None)"
    return type(val).__name__


def _format_full_val(val):
    """Полное форматирование значения для текстового блока."""
    if isinstance(val, (dict, list)):
        try:
            return json.dumps(val, indent=2, ensure_ascii=False)
        except Exception:
            return repr(val)
    if isinstance(val, str):
        return val
    return repr(val)


# =================================================================================
# КОНСТРУКТОРЫ ЭКРАНОВ ИНЛАЙН-МЕНЮ
# =================================================================================

def build_root_menu(session_id, alert_msg=None):
    """
    Экран 1 (Корневой): Разделение на категории (Системные / Пользовательские).
    """
    sid, session = _get_or_create_session(session_id)
    load_config()

    sys_mods = session.get("sys_modules", [])
    usr_mods = session.get("user_modules", [])

    sys_params_count = sum(len(global_config.get(m, {})) for m in sys_mods if isinstance(global_config.get(m), dict))
    usr_params_count = sum(len(global_config.get(m, {})) for m in usr_mods if isinstance(global_config.get(m), dict))
    total_params = sys_params_count + usr_params_count

    alert_block = f"💬 **{alert_msg}**\n\n" if alert_msg else ""
    text = (
        f"{alert_block}"
        f"⚙️ **Менеджер Конфигураций UBTG**\n\n"
        f"📁 **Файл конфигурации:** `Global_config.json`\n"
        f"🔑 **Всего параметров:** `{total_params}`\n\n"
        f"📂 **Категории модулей:**\n"
        f"⚙️ **Системные модули:** `{len(sys_mods)}` *(параметров: {sys_params_count})*\n"
        f"📦 **Пользовательские модули:** `{len(usr_mods)}` *(параметров: {usr_params_count})*\n\n"
        f"💡 *Выберите категорию модулей для настройки:*"
    )

    buttons = [
        [
            Button.inline(f"⚙️ Системные ({len(sys_mods)})", f"cfg_cat:{sid}:sys:0".encode()),
            Button.inline(f"📦 Пользовательские ({len(usr_mods)})", f"cfg_cat:{sid}:usr:0".encode())
        ],
        [
            Button.inline("📋 Весь конфиг", f"cfg_raw:{sid}".encode()),
            Button.inline("🔄 Обновить", f"cfg_refresh:{sid}".encode())
        ],
        [
            Button.inline("🔄 Перезапустить юзербота", f"cfg_rst:{sid}".encode()),
            Button.inline("❌ Закрыть", f"cfg_close:{sid}".encode())
        ]
    ]

    return text, buttons


def build_category_menu(session_id, cat_type="sys", page=0, alert_msg=None):
    """
    Экран 2: Список модулей выбранной категории (системные или пользовательские).
    """
    sid, session = _get_or_create_session(session_id)
    session["current_cat"] = cat_type
    load_config()

    is_sys = (cat_type == "sys")
    cat_title = "Системные модули" if is_sys else "Пользовательские модули"
    cat_icon = "⚙️" if is_sys else "📦"
    cat_desc = (
        "Модули ядра и системные компоненты юзербота."
        if is_sys else
        "Пользовательские модули и расширения."
    )

    target_modules = session.get("sys_modules" if is_sys else "user_modules", [])
    total_mods = len(target_modules)

    items_per_page = ITEMS_PER_PAGE_MAIN
    total_pages = max(1, (total_mods + items_per_page - 1) // items_per_page) if total_mods > 0 else 1
    page = max(0, min(page, total_pages - 1))
    session["cat_page"] = page

    start_idx = page * items_per_page
    end_idx = min(start_idx + items_per_page, total_mods)
    page_modules = target_modules[start_idx:end_idx]

    alert_block = f"💬 **{alert_msg}**\n\n" if alert_msg else ""
    text = (
        f"{alert_block}"
        f"{cat_icon} **{cat_title}**\n\n"
        f"📖 {cat_desc}\n"
        f"📊 **Модулей в категории:** `{total_mods}`\n\n"
        f"💡 *Выберите модуль для просмотра и изменения параметров:*"
    )

    if total_mods == 0:
        text += "\n\n*(В этой категории пока нет настроенных модулей)*"

    buttons = []
    # Кнопки модулей (по 2 в ряд)
    row = []
    for rel_idx, mod_name in enumerate(page_modules):
        mod_idx = session["modules_list"].index(mod_name) if mod_name in session["modules_list"] else 0
        meta = _resolve_module_meta(mod_name)
        param_count = len(global_config.get(mod_name, {}))
        lock_badge = " 🔒" if is_sys else ""
        btn_text = f"{meta['icon']} {meta['display_name']}{lock_badge} ({param_count})"
        row.append(Button.inline(btn_text, f"cfg_m:{sid}:{cat_type}:{mod_idx}:0".encode()))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Пагинация
    if total_pages > 1:
        prev_p = (page - 1) % total_pages
        next_p = (page + 1) % total_pages
        buttons.append([
            Button.inline("◀️ Назад", f"cfg_cat:{sid}:{cat_type}:{prev_p}".encode()),
            Button.inline(f"📄 {page + 1}/{total_pages}", f"cfg_cat:{sid}:{cat_type}:{page}".encode()),
            Button.inline("Вперед ▶️", f"cfg_cat:{sid}:{cat_type}:{next_p}".encode())
        ])

    # Навигация
    buttons.append([
        Button.inline("◀️ К выбору категории", f"cfg_home:{sid}".encode()),
        Button.inline("🔄 Обновить", f"cfg_cat:{sid}:{cat_type}:{page}".encode())
    ])
    buttons.append([
        Button.inline("🔄 Перезапустить юзербота", f"cfg_rst:{sid}".encode()),
        Button.inline("❌ Закрыть", f"cfg_close:{sid}".encode())
    ])

    return text, buttons


def build_module_menu(session_id, mod_idx, keys_page=0, alert_msg=None, cat_type=None):
    """
    Экран 3: Меню параметров конкретного модуля.
    """
    sid, session = _get_or_create_session(session_id)
    modules_list = session["modules_list"]

    if mod_idx < 0 or mod_idx >= len(modules_list):
        return build_root_menu(session_id, "⚠️ Модуль не найден.")

    mod_name = modules_list[mod_idx]
    session["selected_mod_idx"] = mod_idx

    meta = _resolve_module_meta(mod_name)
    cat_type = cat_type or ("sys" if meta["is_system"] else "usr")
    session["current_cat"] = cat_type

    keys_list = _get_module_keys_list(mod_name)
    total_keys = len(keys_list)

    items_per_page = ITEMS_PER_PAGE_MOD
    total_pages = max(1, (total_keys + items_per_page - 1) // items_per_page) if total_keys > 0 else 1
    keys_page = max(0, min(keys_page, total_pages - 1))
    session["keys_page"] = keys_page

    start_idx = keys_page * items_per_page
    end_idx = min(start_idx + items_per_page, total_keys)
    page_keys = keys_list[start_idx:end_idx]

    sys_badge = "🔒 *Системный модуль*" if meta["is_system"] else "📦 *Пользовательский модуль*"
    alert_block = f"💬 **{alert_msg}**\n\n" if alert_msg else ""

    text = (
        f"{alert_block}"
        f"{meta['icon']} **Модуль:** `{meta['display_name']}` (`{mod_name}`)\n"
        f"📖 **Описание:** {meta['desc']}\n"
        f"🏷 **Категория:** {sys_badge}\n\n"
    )

    mod_cfg = global_config.get(mod_name, {})
    if total_keys > 0:
        text += f"⚙️ **Параметры ({total_keys}):**\n"
        for k in page_keys:
            v = mod_cfg[k]
            type_name = _get_val_type_name(v).split()[0]
            val_str = repr(v) if not isinstance(v, str) else f'"{v}"'
            text += f"• `{k}` = `{val_str}` *({type_name})*\n"
        text += "\n💡 *Нажмите на параметр ниже для редактирования:*"
    else:
        text += "⚙️ **Параметры пока не заданы.**\n\n💡 *Вы можете добавить новый параметр кнопкой ниже:*"

    buttons = []
    # Кнопки для каждого параметра
    for rel_k_idx, key in enumerate(page_keys):
        k_idx = start_idx + rel_k_idx
        val = mod_cfg[key]
        preview = _format_val_preview(val)
        btn_label = f"🔑 {key}: {preview}"
        buttons.append([Button.inline(btn_label, f"cfg_p:{sid}:{mod_idx}:{k_idx}".encode())])

    # Пагинация параметров (если много)
    if total_pages > 1:
        prev_p = (keys_page - 1) % total_pages
        next_p = (keys_page + 1) % total_pages
        buttons.append([
            Button.inline("◀️ Назад", f"cfg_kp:{sid}:{mod_idx}:{prev_p}".encode()),
            Button.inline(f"📄 {keys_page + 1}/{total_pages}", f"cfg_m:{sid}:{cat_type}:{mod_idx}:{keys_page}".encode()),
            Button.inline("Вперед ▶️", f"cfg_kp:{sid}:{mod_idx}:{next_p}".encode())
        ])

    # Действия над модулем
    mod_actions = [Button.inline("➕ Добавить параметр", f"cfg_addk:{sid}:{mod_idx}".encode())]
    if total_keys > 0:
        mod_actions.append(Button.inline("🗑 Сбросить модуль", f"cfg_askdm:{sid}:{mod_idx}".encode()))
    buttons.append(mod_actions)

    # Навигация
    cat_btn_label = "◀️ К системным" if meta["is_system"] else "◀️ К пользовательским"
    buttons.append([
        Button.inline(cat_btn_label, f"cfg_cat:{sid}:{cat_type}:{session.get('cat_page', 0)}".encode()),
        Button.inline("🏠 Все категории", f"cfg_home:{sid}".encode())
    ])
    buttons.append([
        Button.inline("🔄 Перезапуск", f"cfg_rst:{sid}".encode()),
        Button.inline("❌ Закрыть", f"cfg_close:{sid}".encode())
    ])

    return text, buttons


def build_param_menu(session_id, mod_idx, key_idx, alert_msg=None):
    """
    Экран 4: Редактирование конкретного параметра с адаптивной интерактивной клавиатурой.
    """
    sid, session = _get_or_create_session(session_id)
    modules_list = session["modules_list"]

    if mod_idx < 0 or mod_idx >= len(modules_list):
        return build_root_menu(session_id, "⚠️ Модуль не найден.")

    mod_name = modules_list[mod_idx]
    keys_list = _get_module_keys_list(mod_name)

    if key_idx < 0 or key_idx >= len(keys_list):
        return build_module_menu(session_id, mod_idx, 0, "⚠️ Параметр не найден.")

    key = keys_list[key_idx]
    session["selected_mod_idx"] = mod_idx
    session["selected_key_idx"] = key_idx

    val = global_config.get(mod_name, {}).get(key)
    meta = _resolve_module_meta(mod_name)
    cat_type = session.get("current_cat", "sys" if meta["is_system"] else "usr")
    type_name = _get_val_type_name(val)
    full_val_formatted = _format_full_val(val)

    alert_block = f"💬 **{alert_msg}**\n\n" if alert_msg else ""

    text = (
        f"{alert_block}"
        f"⚙️ **Редактирование параметра**\n\n"
        f"{meta['icon']} **Модуль:** `{meta['display_name']}` (`{mod_name}`)\n"
        f"🔑 **Параметр:** `{key}`\n"
        f"📊 **Тип данных:** `{type_name}`\n\n"
        f"📌 **Текущее значение:**\n"
        f"```{full_val_formatted}```\n\n"
        f"💡 *Используйте кнопки ниже для быстрого изменения:*"
    )

    buttons = []

    # --- 1. АДАПТИВНЫЕ КНОПКИ ДЛЯ BOOLEAN ---
    if isinstance(val, bool):
        toggle_icon = "🔴 Выключить" if val else "🟢 Включить"
        buttons.append([
            Button.inline("🟢 True (Вкл)", f"cfg_sb:{sid}:{mod_idx}:{key_idx}:1".encode()),
            Button.inline("🔴 False (Выкл)", f"cfg_sb:{sid}:{mod_idx}:{key_idx}:0".encode())
        ])
        buttons.append([
            Button.inline(f"🔄 Переключить ({toggle_icon})", f"cfg_tog:{sid}:{mod_idx}:{key_idx}".encode())
        ])

    # --- 2. АДАПТИВНЫЕ КНОПКИ ДЛЯ INTEGER ---
    elif isinstance(val, int):
        buttons.append([
            Button.inline("➖ 10", f"cfg_num:{sid}:{mod_idx}:{key_idx}:-10".encode()),
            Button.inline("➖ 5", f"cfg_num:{sid}:{mod_idx}:{key_idx}:-5".encode()),
            Button.inline("➕ 5", f"cfg_num:{sid}:{mod_idx}:{key_idx}:+5".encode()),
            Button.inline("➕ 10", f"cfg_num:{sid}:{mod_idx}:{key_idx}:+10".encode())
        ])
        buttons.append([
            Button.inline("➖ 1", f"cfg_num:{sid}:{mod_idx}:{key_idx}:-1".encode()),
            Button.inline("🔄 Знака (+/-)", f"cfg_sign:{sid}:{mod_idx}:{key_idx}".encode()),
            Button.inline("➕ 1", f"cfg_num:{sid}:{mod_idx}:{key_idx}:+1".encode())
        ])
        buttons.append([
            Button.inline("0", f"cfg_setn:{sid}:{mod_idx}:{key_idx}:0".encode()),
            Button.inline("1", f"cfg_setn:{sid}:{mod_idx}:{key_idx}:1".encode()),
            Button.inline("5", f"cfg_setn:{sid}:{mod_idx}:{key_idx}:5".encode()),
            Button.inline("10", f"cfg_setn:{sid}:{mod_idx}:{key_idx}:10".encode()),
            Button.inline("100", f"cfg_setn:{sid}:{mod_idx}:{key_idx}:100".encode())
        ])
        buttons.append([
            Button.inline("✏️ Ввести своё число", f"cfg_manual:{sid}:{mod_idx}:{key_idx}".encode())
        ])

    # --- 3. АДАПТИВНЫЕ КНОПКИ ДЛЯ FLOAT ---
    elif isinstance(val, float):
        buttons.append([
            Button.inline("➖ 1.0", f"cfg_num:{sid}:{mod_idx}:{key_idx}:-1.0".encode()),
            Button.inline("➖ 0.5", f"cfg_num:{sid}:{mod_idx}:{key_idx}:-0.5".encode()),
            Button.inline("➕ 0.5", f"cfg_num:{sid}:{mod_idx}:{key_idx}:+0.5".encode()),
            Button.inline("➕ 1.0", f"cfg_num:{sid}:{mod_idx}:{key_idx}:+1.0".encode())
        ])
        buttons.append([
            Button.inline("➖ 0.1", f"cfg_num:{sid}:{mod_idx}:{key_idx}:-0.1".encode()),
            Button.inline("🔄 Знака (+/-)", f"cfg_sign:{sid}:{mod_idx}:{key_idx}".encode()),
            Button.inline("➕ 0.1", f"cfg_num:{sid}:{mod_idx}:{key_idx}:+0.1".encode())
        ])
        buttons.append([
            Button.inline("0.0", f"cfg_setn:{sid}:{mod_idx}:{key_idx}:0.0".encode()),
            Button.inline("0.5", f"cfg_setn:{sid}:{mod_idx}:{key_idx}:0.5".encode()),
            Button.inline("1.0", f"cfg_setn:{sid}:{mod_idx}:{key_idx}:1.0".encode()),
            Button.inline("2.0", f"cfg_setn:{sid}:{mod_idx}:{key_idx}:2.0".encode()),
            Button.inline("5.0", f"cfg_setn:{sid}:{mod_idx}:{key_idx}:5.0".encode())
        ])
        buttons.append([
            Button.inline("✏️ Ввести своё число", f"cfg_manual:{sid}:{mod_idx}:{key_idx}".encode())
        ])

    # --- 4. АДАПТИВНЫЕ КНОПКИ ДЛЯ STRING ---
    elif isinstance(val, str):
        buttons.append([
            Button.inline("🧹 Очистить (пустая строка)", f"cfg_clr:{sid}:{mod_idx}:{key_idx}".encode()),
            Button.inline("✏️ Изменить текст", f"cfg_manual:{sid}:{mod_idx}:{key_idx}".encode())
        ])

    # --- 5. ДЛЯ LIST / DICT / NONE ---
    else:
        buttons.append([
            Button.inline("🧹 Очистить (сделать пустым)", f"cfg_clr:{sid}:{mod_idx}:{key_idx}".encode()),
            Button.inline("✏️ Инструкция по изменению", f"cfg_manual:{sid}:{mod_idx}:{key_idx}".encode())
        ])

    # Общие действия над параметром
    buttons.append([
        Button.inline("🗑 Удалить этот ключ", f"cfg_askdk:{sid}:{mod_idx}:{key_idx}".encode()),
        Button.inline("🔄 Перезапустить юзербота", f"cfg_rst:{sid}".encode())
    ])

    # Навигация
    buttons.append([
        Button.inline("◀️ К модулю", f"cfg_m:{sid}:{cat_type}:{mod_idx}:{session.get('keys_page', 0)}".encode()),
        Button.inline("🏠 В главное меню", f"cfg_home:{sid}".encode()),
        Button.inline("❌ Закрыть", f"cfg_close:{sid}".encode())
    ])

    return text, buttons


def build_manual_input_help(session_id, mod_idx, key_idx):
    """Экран с инструкцией и готовой командой для копирования."""
    sid, session = _get_or_create_session(session_id)
    modules_list = session["modules_list"]
    mod_name = modules_list[mod_idx]
    keys_list = _get_module_keys_list(mod_name)
    key = keys_list[key_idx]
    meta = _resolve_module_meta(mod_name)

    text = (
        f"✏️ **Ручное изменение параметра `{key}`**\n\n"
        f"{meta['icon']} **Модуль:** `{meta['display_name']}` (`{mod_name}`)\n"
        f"🔑 **Параметр:** `{key}`\n\n"
        f"Для установки нового значения отправьте команду:\n"
        f"`.cfg set {mod_name} {key} <новое_значение>`\n\n"
        f"📌 **Примеры команд:**\n"
        f"• Число: `.cfg set {mod_name} {key} 15`\n"
        f"• Текст: `.cfg set {mod_name} {key} Привет мир!`\n"
        f"• Дробное: `.cfg set {mod_name} {key} 2.5`\n"
        f"• Булево: `.cfg set {mod_name} {key} true`\n"
        f"• JSON: `.cfg set {mod_name} {key} [\"a\", \"b\"]`\n\n"
        f"💡 *Значение автоматически запишется в `Global_config.json`.*"
    )

    buttons = [
        [Button.inline("◀️ Назад к параметру", f"cfg_p:{sid}:{mod_idx}:{key_idx}".encode())],
        [Button.inline("🏠 Главное меню", f"cfg_home:{sid}".encode())]
    ]
    return text, buttons


def build_add_param_help(session_id, mod_idx):
    """Экран помощи по добавлению нового параметра."""
    sid, session = _get_or_create_session(session_id)
    modules_list = session["modules_list"]
    mod_name = modules_list[mod_idx]
    meta = _resolve_module_meta(mod_name)
    cat_type = session.get("current_cat", "sys" if meta["is_system"] else "usr")

    text = (
        f"➕ **Добавление нового параметра в модуль `{meta['display_name']}`**\n\n"
        f"Чтобы добавить новый параметр, отправьте команду:\n"
        f"`.cfg set {mod_name} <ключ> <значение>`\n\n"
        f"📌 **Пример:**\n"
        f"`.cfg set {mod_name} custom_delay 5`\n"
        f"`.cfg set {mod_name} enable_feature true`\n\n"
        f"💡 *После отправки команды параметр сразу появится в этом меню.*"
    )

    buttons = [
        [Button.inline("◀️ К параметрам модуля", f"cfg_m:{sid}:{cat_type}:{mod_idx}:0".encode())],
        [Button.inline("🏠 Главное меню", f"cfg_home:{sid}".encode())]
    ]
    return text, buttons


def build_confirm_del_key(session_id, mod_idx, key_idx):
    """Экран подтверждения удаления параметра."""
    sid, session = _get_or_create_session(session_id)
    modules_list = session["modules_list"]
    mod_name = modules_list[mod_idx]
    keys_list = _get_module_keys_list(mod_name)
    key = keys_list[key_idx]

    text = (
        f"⚠️ **Подтверждение удаления параметра**\n\n"
        f"Вы действительно хотите удалить параметр `{key}` из модуля `{mod_name}`?\n\n"
        f"❗ *Значение будет стерто из `Global_config.json`.*"
    )

    buttons = [
        [Button.inline("🗑 Да, удалить параметр", f"cfg_dodk:{sid}:{mod_idx}:{key_idx}".encode())],
        [Button.inline("❌ Отмена", f"cfg_p:{sid}:{mod_idx}:{key_idx}".encode())]
    ]
    return text, buttons


def build_confirm_del_mod(session_id, mod_idx):
    """Экран подтверждения удаления настроек всего модуля."""
    sid, session = _get_or_create_session(session_id)
    modules_list = session["modules_list"]
    mod_name = modules_list[mod_idx]
    meta = _resolve_module_meta(mod_name)
    cat_type = session.get("current_cat", "sys" if meta["is_system"] else "usr")

    text = (
        f"⚠️ **Подтверждение сброса модуля**\n\n"
        f"Вы действительно хотите удалить ВСЕ параметры конфигурации модуля `{meta['display_name']}` (`{mod_name}`)?\n\n"
        f"❗ *Вся секция модуля будет удалена из `Global_config.json`.*"
    )

    buttons = [
        [Button.inline("🗑 Да, сбросить все настройки", f"cfg_dodm:{sid}:{mod_idx}".encode())],
        [Button.inline("❌ Отмена", f"cfg_m:{sid}:{cat_type}:{mod_idx}:0".encode())]
    ]
    return text, buttons


def build_raw_view(session_id):
    """Экран с полным JSON содержимым конфига."""
    sid, _ = _get_or_create_session(session_id)
    load_config()
    try:
        raw_json = json.dumps(global_config, indent=2, ensure_ascii=False)
    except Exception:
        raw_json = repr(global_config)

    if len(raw_json) > 3000:
        raw_json = raw_json[:3000] + "\n... [Обрезано по лимиту]"

    text = (
        f"📋 **Содержимое файла `Global_config.json`:**\n\n"
        f"```{raw_json}```"
    )

    buttons = [
        [
            Button.inline("🏠 В главное меню", f"cfg_home:{sid}".encode()),
            Button.inline("🔄 Обновить", f"cfg_raw:{sid}".encode())
        ],
        [Button.inline("❌ Закрыть", f"cfg_close:{sid}".encode())]
    ]
    return text, buttons


# =================================================================================
# ОБРАБОТЧИКИ НАЖАТИЙ НА ИНЛАЙН-КНОПКИ
# =================================================================================

@register_callback("cfg_home:")
async def cb_cfg_home(event, data):
    """Возврат в корневое главное меню (выбор категорий)."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    sid = data[len("cfg_home:"):].decode() if isinstance(data, bytes) else data[len("cfg_home:"):]
    text, buttons = build_root_menu(sid)
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        await event.answer()


@register_callback("cfg_cat:")
async def cb_cfg_category(event, data):
    """Переход в список модулей выбранной категории (системные / пользовательские)."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    payload = data[len("cfg_cat:"):].decode() if isinstance(data, bytes) else data[len("cfg_cat:"):]
    try:
        parts = payload.split(":")
        sid = parts[0]
        cat_type = parts[1]
        page = int(parts[2]) if len(parts) > 2 else 0
    except Exception:
        return await event.answer("⚠️ Ошибка навигации", alert=True)

    text, buttons = build_category_menu(sid, cat_type, page)
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        await event.answer()


@register_callback("cfg_m:")
async def cb_cfg_module(event, data):
    """Переход в меню конкретного модуля."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    payload = data[len("cfg_m:"):].decode() if isinstance(data, bytes) else data[len("cfg_m:"):]
    try:
        parts = payload.split(":")
        sid = parts[0]
        if len(parts) == 4:
            cat_type = parts[1]
            mod_idx = int(parts[2])
            page = int(parts[3])
        else:
            cat_type = None
            mod_idx = int(parts[1])
            page = int(parts[2]) if len(parts) > 2 else 0
    except Exception:
        return await event.answer("⚠️ Ошибка навигации", alert=True)

    text, buttons = build_module_menu(sid, mod_idx, page, cat_type=cat_type)
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        await event.answer()


@register_callback("cfg_kp:")
async def cb_cfg_keys_page(event, data):
    """Пагинация параметров внутри модуля."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    payload = data[len("cfg_kp:"):].decode() if isinstance(data, bytes) else data[len("cfg_kp:"):]
    try:
        parts = payload.split(":")
        sid = parts[0]
        mod_idx = int(parts[1])
        page = int(parts[2])
    except Exception:
        return await event.answer("⚠️ Ошибка навигации", alert=True)

    text, buttons = build_module_menu(sid, mod_idx, page)
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        await event.answer()


@register_callback("cfg_p:")
async def cb_cfg_param(event, data):
    """Переход в карточку редактирования параметра."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    payload = data[len("cfg_p:"):].decode() if isinstance(data, bytes) else data[len("cfg_p:"):]
    try:
        parts = payload.split(":")
        sid = parts[0]
        mod_idx = int(parts[1])
        key_idx = int(parts[2])
    except Exception:
        return await event.answer("⚠️ Ошибка навигации", alert=True)

    text, buttons = build_param_menu(sid, mod_idx, key_idx)
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        await event.answer()


@register_callback("cfg_tog:")
async def cb_cfg_toggle_bool(event, data):
    """Мгновенное переключение булева параметра (True <-> False)."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    payload = data[len("cfg_tog:"):].decode() if isinstance(data, bytes) else data[len("cfg_tog:"):]
    try:
        parts = payload.split(":")
        sid = parts[0]
        mod_idx = int(parts[1])
        key_idx = int(parts[2])
    except Exception:
        return await event.answer("⚠️ Ошибка параметра", alert=True)

    _, session = _get_or_create_session(sid)
    modules_list = session["modules_list"]
    if mod_idx >= len(modules_list):
        return await event.answer("⚠️ Модуль не найден", alert=True)

    mod_name = modules_list[mod_idx]
    keys_list = _get_module_keys_list(mod_name)
    if key_idx >= len(keys_list):
        return await event.answer("⚠️ Ключ не найден", alert=True)

    key = keys_list[key_idx]
    curr_val = bool(global_config.get(mod_name, {}).get(key, False))
    new_val = not curr_val

    set_config(mod_name, key, new_val)
    status_icon = "🟢 Включено (True)" if new_val else "🔴 Выключено (False)"
    await event.answer(f"✅ {key}: {status_icon}")

    text, buttons = build_param_menu(sid, mod_idx, key_idx, f"✅ Параметр `{key}` переключен на `{new_val}`")
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        pass


@register_callback("cfg_sb:")
async def cb_cfg_set_bool(event, data):
    """Прямая установка True или False."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    payload = data[len("cfg_sb:"):].decode() if isinstance(data, bytes) else data[len("cfg_sb:"):]
    try:
        parts = payload.split(":")
        sid = parts[0]
        mod_idx = int(parts[1])
        key_idx = int(parts[2])
        new_val = bool(int(parts[3]))
    except Exception:
        return await event.answer("⚠️ Ошибка", alert=True)

    _, session = _get_or_create_session(sid)
    modules_list = session["modules_list"]
    mod_name = modules_list[mod_idx]
    keys_list = _get_module_keys_list(mod_name)
    key = keys_list[key_idx]

    set_config(mod_name, key, new_val)
    status_icon = "🟢 True" if new_val else "🔴 False"
    await event.answer(f"✅ Установлено: {status_icon}")

    text, buttons = build_param_menu(sid, mod_idx, key_idx, f"✅ `{key}` = `{new_val}`")
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        pass


@register_callback("cfg_num:")
async def cb_cfg_step_number(event, data):
    """Ступенчатое изменение числа (+1, -1, +5, -5, +0.5, -0.5 и т.д.)."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    payload = data[len("cfg_num:"):].decode() if isinstance(data, bytes) else data[len("cfg_num:"):]
    try:
        parts = payload.split(":")
        sid = parts[0]
        mod_idx = int(parts[1])
        key_idx = int(parts[2])
        delta_str = parts[3]
    except Exception:
        return await event.answer("⚠️ Ошибка", alert=True)

    _, session = _get_or_create_session(sid)
    modules_list = session["modules_list"]
    mod_name = modules_list[mod_idx]
    keys_list = _get_module_keys_list(mod_name)
    key = keys_list[key_idx]

    curr_val = global_config.get(mod_name, {}).get(key, 0)
    
    if isinstance(curr_val, float) or "." in delta_str:
        try:
            delta = float(delta_str)
            new_val = round(float(curr_val) + delta, 4)
        except ValueError:
            new_val = float(delta_str)
    else:
        try:
            delta = int(delta_str)
            new_val = int(curr_val) + delta
        except ValueError:
            new_val = int(delta_str)

    set_config(mod_name, key, new_val)
    await event.answer(f"✅ {key} = {new_val}")

    text, buttons = build_param_menu(sid, mod_idx, key_idx, f"✅ `{key}` изменено на `{new_val}`")
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        pass


@register_callback("cfg_setn:")
async def cb_cfg_set_num_preset(event, data):
    """Установка фиксированного пресета числа (0, 1, 5, 10 и т.д.)."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    payload = data[len("cfg_setn:"):].decode() if isinstance(data, bytes) else data[len("cfg_setn:"):]
    try:
        parts = payload.split(":")
        sid = parts[0]
        mod_idx = int(parts[1])
        key_idx = int(parts[2])
        val_str = parts[3]
    except Exception:
        return await event.answer("⚠️ Ошибка", alert=True)

    _, session = _get_or_create_session(sid)
    modules_list = session["modules_list"]
    mod_name = modules_list[mod_idx]
    keys_list = _get_module_keys_list(mod_name)
    key = keys_list[key_idx]

    if "." in val_str:
        new_val = float(val_str)
    else:
        new_val = int(val_str)

    set_config(mod_name, key, new_val)
    await event.answer(f"✅ {key} = {new_val}")

    text, buttons = build_param_menu(sid, mod_idx, key_idx, f"✅ `{key}` = `{new_val}`")
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        pass


@register_callback("cfg_sign:")
async def cb_cfg_invert_sign(event, data):
    """Смена знака числа (+/-)."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    payload = data[len("cfg_sign:"):].decode() if isinstance(data, bytes) else data[len("cfg_sign:"):]
    try:
        parts = payload.split(":")
        sid = parts[0]
        mod_idx = int(parts[1])
        key_idx = int(parts[2])
    except Exception:
        return await event.answer("⚠️ Ошибка", alert=True)

    _, session = _get_or_create_session(sid)
    modules_list = session["modules_list"]
    mod_name = modules_list[mod_idx]
    keys_list = _get_module_keys_list(mod_name)
    key = keys_list[key_idx]

    curr_val = global_config.get(mod_name, {}).get(key, 0)
    if isinstance(curr_val, (int, float)):
        new_val = -curr_val
        set_config(mod_name, key, new_val)
        await event.answer(f"✅ {key} = {new_val}")
        text, buttons = build_param_menu(sid, mod_idx, key_idx, f"✅ `{key}` инвертировано на `{new_val}`")
        try:
            await event.edit(text, buttons=buttons)
        except errors.MessageNotModifiedError:
            pass


@register_callback("cfg_clr:")
async def cb_cfg_clear_val(event, data):
    """Очистка строкового, списочного или словарного параметра."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    payload = data[len("cfg_clr:"):].decode() if isinstance(data, bytes) else data[len("cfg_clr:"):]
    try:
        parts = payload.split(":")
        sid = parts[0]
        mod_idx = int(parts[1])
        key_idx = int(parts[2])
    except Exception:
        return await event.answer("⚠️ Ошибка", alert=True)

    _, session = _get_or_create_session(sid)
    modules_list = session["modules_list"]
    mod_name = modules_list[mod_idx]
    keys_list = _get_module_keys_list(mod_name)
    key = keys_list[key_idx]

    curr_val = global_config.get(mod_name, {}).get(key)
    if isinstance(curr_val, list):
        new_val = []
    elif isinstance(curr_val, dict):
        new_val = {}
    else:
        new_val = ""

    set_config(mod_name, key, new_val)
    await event.answer(f"🧹 {key} очищен")

    text, buttons = build_param_menu(sid, mod_idx, key_idx, f"🧹 Параметр `{key}` очищен!")
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        pass


@register_callback("cfg_manual:")
async def cb_cfg_manual_help(event, data):
    """Экран подсказки для ручного ввода команды."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    payload = data[len("cfg_manual:"):].decode() if isinstance(data, bytes) else data[len("cfg_manual:"):]
    try:
        parts = payload.split(":")
        sid = parts[0]
        mod_idx = int(parts[1])
        key_idx = int(parts[2])
    except Exception:
        return await event.answer("⚠️ Ошибка", alert=True)

    text, buttons = build_manual_input_help(sid, mod_idx, key_idx)
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        await event.answer()


@register_callback("cfg_addk:")
async def cb_cfg_add_key_help(event, data):
    """Экран подсказки для добавления нового ключа."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    payload = data[len("cfg_addk:"):].decode() if isinstance(data, bytes) else data[len("cfg_addk:"):]
    try:
        parts = payload.split(":")
        sid = parts[0]
        mod_idx = int(parts[1])
    except Exception:
        return await event.answer("⚠️ Ошибка", alert=True)

    text, buttons = build_add_param_help(sid, mod_idx)
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        await event.answer()


@register_callback("cfg_askdk:")
async def cb_cfg_ask_del_key(event, data):
    """Запрос подтверждения удаления ключа."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    payload = data[len("cfg_askdk:"):].decode() if isinstance(data, bytes) else data[len("cfg_askdk:"):]
    try:
        parts = payload.split(":")
        sid = parts[0]
        mod_idx = int(parts[1])
        key_idx = int(parts[2])
    except Exception:
        return await event.answer("⚠️ Ошибка", alert=True)

    text, buttons = build_confirm_del_key(sid, mod_idx, key_idx)
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        await event.answer()


@register_callback("cfg_dodk:")
async def cb_cfg_do_del_key(event, data):
    """Исполнение удаления ключа из конфига."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    payload = data[len("cfg_dodk:"):].decode() if isinstance(data, bytes) else data[len("cfg_dodk:"):]
    try:
        parts = payload.split(":")
        sid = parts[0]
        mod_idx = int(parts[1])
        key_idx = int(parts[2])
    except Exception:
        return await event.answer("⚠️ Ошибка", alert=True)

    _, session = _get_or_create_session(sid)
    modules_list = session["modules_list"]
    mod_name = modules_list[mod_idx]
    keys_list = _get_module_keys_list(mod_name)
    key = keys_list[key_idx]

    delete_config(mod_name, key)
    await event.answer(f"🗑 Ключ '{key}' удален")

    text, buttons = build_module_menu(sid, mod_idx, 0, f"🗑 Параметр `{key}` успешно удален из `{mod_name}`!")
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        pass


@register_callback("cfg_askdm:")
async def cb_cfg_ask_del_mod(event, data):
    """Запрос подтверждения удаления всех настроек модуля."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    payload = data[len("cfg_askdm:"):].decode() if isinstance(data, bytes) else data[len("cfg_askdm:"):]
    try:
        parts = payload.split(":")
        sid = parts[0]
        mod_idx = int(parts[1])
    except Exception:
        return await event.answer("⚠️ Ошибка", alert=True)

    text, buttons = build_confirm_del_mod(sid, mod_idx)
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        await event.answer()


@register_callback("cfg_dodm:")
async def cb_cfg_do_del_mod(event, data):
    """Исполнение удаления всех настроек модуля."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    payload = data[len("cfg_dodm:"):].decode() if isinstance(data, bytes) else data[len("cfg_dodm:"):]
    try:
        parts = payload.split(":")
        sid = parts[0]
        mod_idx = int(parts[1])
    except Exception:
        return await event.answer("⚠️ Ошибка", alert=True)

    _, session = _get_or_create_session(sid)
    modules_list = session["modules_list"]
    mod_name = modules_list[mod_idx]
    meta = _resolve_module_meta(mod_name)
    cat_type = "sys" if meta["is_system"] else "usr"

    delete_config(mod_name)
    await event.answer(f"🗑 Модуль '{mod_name}' сброшен")

    # Обновляем списки сессии
    all_mods = _get_all_modules_list()
    session["modules_list"] = all_mods
    session["sys_modules"] = [m for m in all_mods if _is_system_module(m)]
    session["user_modules"] = [m for m in all_mods if not _is_system_module(m)]

    text, buttons = build_category_menu(sid, cat_type, 0, f"🗑 Все настройки модуля `{mod_name}` сброшены!")
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        pass


@register_callback("cfg_raw:")
async def cb_cfg_raw(event, data):
    """Просмотр чистого JSON конфига."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    sid = data[len("cfg_raw:"):].decode() if isinstance(data, bytes) else data[len("cfg_raw:"):]
    text, buttons = build_raw_view(sid)
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        await event.answer()


@register_callback("cfg_refresh:")
async def cb_cfg_refresh(event, data):
    """Принудительное обновление текущего экрана меню."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    sid = data[len("cfg_refresh:"):].decode() if isinstance(data, bytes) else data[len("cfg_refresh:"):]
    load_config()
    await event.answer("🔄 Конфигурация обновлена!")
    text, buttons = build_root_menu(sid)
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        pass


@register_callback("cfg_rst:")
async def cb_cfg_restart(event, data):
    """Перезапуск юзербота с сохранением контекста сообщения."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    await event.answer("🔄 Перезапуск юзербота...")
    try:
        await event.edit("🔄 **Перезагрузка юзербота для применения настроек...**", buttons=None)
    except Exception:
        pass

    custom_text = "✅ **Настройки успешно применены! Юзербот перезагружен.**"
    await restart_userbot(get_main_client(), event.chat_id, event.id, custom_text=custom_text, event=event)


@register_callback("cfg_close:")
async def cb_cfg_close(event, data):
    """Закрытие инлайн-меню конфига."""
    sid = data[len("cfg_close:"):].decode() if isinstance(data, bytes) else data[len("cfg_close:"):]
    _cfg_sessions.pop(sid, None)
    await event.answer("Закрыто")
    try:
        await event.delete()
    except Exception:
        try:
            await event.edit("❌ **Менеджер конфигураций закрыт.**", buttons=None)
        except Exception:
            pass


# =================================================================================
# КОМАНДЫ ЮЗЕРБОТА
# =================================================================================

@register_cmd("cfg", desc="Менеджер конфига с инлайн-кнопками. Юзай: .cfg или .cfg help")
@register_cmd("config", desc="Алиас для .cfg")
@register_cmd("settings_cfg", desc="Алиас для .cfg")
async def config_manager(client, event, args):
    """
    Основная команда управления конфигурацией.
    • Без аргументов или `.cfg menu`/`.cfg ui` — открывает интерактивное инлайн-меню с категориями и кнопками.
    • `.cfg list` — текстовый список всех настроек.
    • `.cfg get <модуль> [ключ]` — получить значение.
    • `.cfg set <модуль> <ключ> <значение>` — установить параметр.
    • `.cfg del <модуль> <ключ>` — удалить параметр.
    • `.cfg delmod <модуль>` — удалить всю секцию модуля.
    • `.cfg restart` — перезагрузить юзербота.
    • `.cfg raw` — чистый JSON.
    • `.cfg help` — подробная справка.
    """
    raw_args = args.strip() if args else ""
    parts = raw_args.split()
    action = parts[0].lower() if parts else "menu"

    # --- 1. СПРАВКА (.cfg help) ---
    if action in ("help", "h", "?"):
        help_text = (
            "⚙️ **Менеджер Конфигураций UBTG**\n\n"
            "🎮 **Интерактивный режим (с кнопками):**\n"
            "• `.cfg` (или `.config`) — открыть главное меню с разделением на системные и пользовательские модули\n\n"
            "⌨️ **Быстрые текстовые команды:**\n"
            "• `.cfg list` — показать все текущие параметры в виде списка\n"
            "• `.cfg get <модуль> [ключ]` — показать параметры конкретного модуля\n"
            "• `.cfg set <модуль> <ключ> <значение>` — установить или изменить параметр\n"
            "• `.cfg del <модуль> <ключ>` — удалить параметр из конфига\n"
            "• `.cfg delmod <модуль>` — удалить все параметры модуля\n"
            "• `.cfg raw` — отобразить полный JSON файл `Global_config.json`\n"
            "• `.cfg restart` — перезагрузить юзербота для применения всех изменений\n\n"
            "📌 **Примеры:**\n"
            "• `.cfg set module_ping custom_reply Привет!`\n"
            "• `.cfg set compliments speed 1.5`\n"
            "• `.cfg set compliments mention_target false`\n\n"
            "💡 *В интерактивном меню логические (True/False) и числовые параметры можно менять в 1 клик!*"
        )
        return await event.edit(help_text)

    # --- 2. ИНЛАЙН-МЕНЮ (.cfg / .cfg menu / .cfg ui) ---
    if action in ("menu", "ui", "inline", "start"):
        sid, _ = _get_or_create_session(chat_id=event.chat_id)
        text, buttons = build_root_menu(sid)
        try:
            await send_inline(
                client,
                event.chat_id,
                text,
                buttons=buttons,
                reply_to=getattr(event, "reply_to_msg_id", None)
            )
            await event.delete()
        except Exception as inline_ex:
            logger.warning(f"send_inline fallback: {inline_ex}")
            await event.edit(text)
        return

    # --- 3. СПИСОК ВСЕХ НАСТРОЕК (.cfg list) ---
    if action == "list":
        load_config()
        if not global_config:
            return await event.edit("⚙️ Конфиг пока пуст. Используйте `.cfg` для настройки модулей.")

        all_mods = _get_all_modules_list()
        sys_mods = [m for m in all_mods if _is_system_module(m) and m in global_config]
        usr_mods = [m for m in all_mods if not _is_system_module(m) and m in global_config]

        text = "⚙️ **Глобальная Конфигурация (Global_config.json):**\n\n"

        if sys_mods:
            text += "🔒 **Системные модули:**\n"
            for mod in sys_mods:
                meta = _resolve_module_meta(mod)
                params = global_config[mod]
                text += f"⚙️ **{meta['display_name']}** (`{mod}`)\n"
                if isinstance(params, dict):
                    for k, v in params.items():
                        type_name = _get_val_type_name(v).split()[0]
                        val_str = repr(v) if not isinstance(v, str) else f'"{v}"'
                        text += f"  ├ `{k}` = `{val_str}` *({type_name})*\n"
                else:
                    text += f"  └ `{repr(params)}`\n"
                text += "\n"

        if usr_mods:
            text += "📦 **Пользовательские модули:**\n"
            for mod in usr_mods:
                meta = _resolve_module_meta(mod)
                params = global_config[mod]
                text += f"📦 **{meta['display_name']}** (`{mod}`)\n"
                if isinstance(params, dict):
                    for k, v in params.items():
                        type_name = _get_val_type_name(v).split()[0]
                        val_str = repr(v) if not isinstance(v, str) else f'"{v}"'
                        text += f"  ├ `{k}` = `{val_str}` *({type_name})*\n"
                else:
                    text += f"  └ `{repr(params)}`\n"
                text += "\n"

        text += "💡 *Используй `.cfg` для изменения параметров через удобные кнопки.*"
        return await event.edit(text)

    # --- 4. ПОЛУЧИТЬ ПАРАМЕТР (.cfg get <модуль> [ключ]) ---
    elif action == "get":
        if len(parts) < 2:
            return await event.edit("❌ Укажи имя модуля. Пример: `.cfg get module_ping`")

        mod_name = parts[1]
        load_config()

        # Ищем точное совпадение или нечувствительное к регистру
        found_mod = None
        for m in global_config.keys():
            if m.lower() == mod_name.lower():
                found_mod = m
                break

        if not found_mod or not global_config.get(found_mod):
            return await event.edit(f"❌ В конфиге нет данных для модуля `{mod_name}`.\n💡 Проверьте список: `.cfg list`")

        meta = _resolve_module_meta(found_mod)

        # Если указан конкретный ключ
        if len(parts) >= 3:
            key = parts[2]
            if key not in global_config[found_mod]:
                return await event.edit(f"❌ Параметр `{key}` не найден в модуле `{found_mod}`.")

            val = global_config[found_mod][key]
            text = (
                f"{meta['icon']} **Модуль:** `{meta['display_name']}` (`{found_mod}`)\n"
                f"🔑 **Параметр:** `{key}`\n"
                f"📊 **Тип:** `{_get_val_type_name(val)}`\n"
                f"📌 **Значение:** `{_format_full_val(val)}`"
            )
            return await event.edit(text)

        # Показываем все параметры модуля
        text = f"{meta['icon']} **Конфиг для {meta['display_name']} (`{found_mod}`):**\n\n"
        for k, v in global_config[found_mod].items():
            type_name = _get_val_type_name(v).split()[0]
            val_str = repr(v) if not isinstance(v, str) else f'"{v}"'
            text += f"• `{k}` = `{val_str}` *({type_name})*\n"

        text += f"\n💡 *Для изменения используй:* `.cfg set {found_mod} <ключ> <значение>`"
        return await event.edit(text)

    # --- 5. УСТАНОВИТЬ ПАРАМЕТР (.cfg set <модуль> <ключ> <значение>) ---
    elif action == "set":
        if len(parts) < 4:
            return await event.edit(
                "❌ **Недостаточно аргументов!**\n"
                "📌 **Формат:** `.cfg set <модуль> <ключ> <значение>`\n"
                "💡 **Пример:** `.cfg set module_ping custom_reply Привет мир!`"
            )

        mod_name = parts[1]
        key = parts[2]
        raw_value = " ".join(parts[3:])

        # Автоматическое определение типов
        parsed_value = parse_value(raw_value)

        # Сохранение в конфиг
        set_config(mod_name, key, parsed_value)

        meta = _resolve_module_meta(mod_name)
        type_name = _get_val_type_name(parsed_value)
        val_repr = _format_full_val(parsed_value)

        success_text = (
            f"✅ **Параметр успешно сохранен!**\n\n"
            f"{meta['icon']} **Модуль:** `{meta['display_name']}` (`{mod_name}`)\n"
            f"🔑 **Параметр:** `{key}`\n"
            f"📊 **Тип:** `{type_name}`\n"
            f"📌 **Новое значение:**\n```{val_repr}```\n\n"
            f"💾 *Сохранено в `Global_config.json`.*"
        )

        buttons = [
            [
                Button.inline("🔄 Применить и перезапустить", b"cfg_quick_restart"),
                Button.inline("⚙️ Открыть в меню", b"cfg_quick_menu")
            ]
        ]

        try:
            await send_inline(
                client,
                event.chat_id,
                success_text,
                buttons=buttons,
                reply_to=getattr(event, "reply_to_msg_id", None)
            )
            await event.delete()
        except Exception:
            await event.edit(f"{success_text}\n\n💡 *Для применения настроек ядра используйте `.restart`.*")
        return

    # --- 6. УДАЛИТЬ ПАРАМЕТР (.cfg del <модуль> <ключ>) ---
    elif action in ("del", "delete", "rm", "remove"):
        if len(parts) < 3:
            return await event.edit("❌ Укажите модуль и ключ: `.cfg del <модуль> <ключ>`")

        mod_name = parts[1]
        key = parts[2]

        if delete_config(mod_name, key):
            await event.edit(f"🗑 **Параметр `{key}` успешно удален из модуля `{mod_name}`!**")
        else:
            await event.edit(f"❌ Параметр `{key}` в модуле `{mod_name}` не найден.")
        return

    # --- 7. УДАЛИТЬ СЕКЦИЮ МОДУЛЯ (.cfg delmod <модуль>) ---
    elif action in ("delmod", "clearmod", "rmmod"):
        if len(parts) < 2:
            return await event.edit("❌ Укажите имя модуля: `.cfg delmod <модуль>`")

        mod_name = parts[1]
        if delete_config(mod_name):
            await event.edit(f"🗑 **Все параметры модуля `{mod_name}` успешно удалены из конфигурации!**")
        else:
            await event.edit(f"❌ Модуль `{mod_name}` не найден в конфигурации.")
        return

    # --- 8. СЫРОЙ JSON (.cfg raw / .cfg export) ---
    elif action in ("raw", "json", "export"):
        load_config()
        raw_json = json.dumps(global_config, indent=2, ensure_ascii=False)
        if len(raw_json) > 3500:
            raw_json = raw_json[:3500] + "\n... [Обрезано по лимиту]"
        return await event.edit(f"📋 **Global_config.json:**\n\n```{raw_json}```")

    # --- 9. ПЕРЕЗАПУСК (.cfg restart / .cfg apply) ---
    elif action in ("restart", "apply"):
        await event.edit("🔄 **Перезагрузка юзербота для применения настроек конфигурации...**")
        custom_text = "✅ **Настройки применены! Юзербот успешно перезагружен.**"
        await restart_userbot(client, event.chat_id, event.id, custom_text=custom_text)
        return

    else:
        await event.edit(
            f"❌ Неизвестное действие `{action}`.\n"
            f"💡 Введите `.cfg` для интерактивного меню или `.cfg help` для справки."
        )


# =================================================================================
# ОБРАБОТЧИКИ БЫСТРЫХ КНОПОК ПОСЛЕ .CFG SET
# =================================================================================

@register_callback("cfg_quick_restart")
async def cb_cfg_quick_restart(event, data):
    """Быстрый перезапуск по кнопке после .cfg set."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    await event.answer("🔄 Перезапуск...")
    try:
        await event.edit("🔄 **Перезагрузка юзербота...**", buttons=None)
    except Exception:
        pass
    custom_text = "✅ **Настройки успешно применены! Юзербот перезагружен.**"
    await restart_userbot(get_main_client(), event.chat_id, event.id, custom_text=custom_text, event=event)


@register_callback("cfg_quick_menu")
async def cb_cfg_quick_menu(event, data):
    """Быстрое открытие главного меню конфига после .cfg set."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Доступно только владельцу!", alert=True)

    sid, _ = _get_or_create_session(chat_id=event.chat_id)
    text, buttons = build_root_menu(sid)
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        await event.answer()