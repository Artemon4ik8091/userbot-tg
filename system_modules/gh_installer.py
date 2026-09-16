import os
import sys
import re
import asyncio
import importlib
import traceback
import json
import aiohttp
import time
import hashlib
from telethon import Button, errors
from registry import (
    register_cmd,
    register_bg,
    register_callback,
    set_module_meta,
    modules_repo,
    restart_userbot,
    get_logger,
    send_inline,
    get_owner_id,
    get_bot,
    get_bot_username,
    get_main_client,
    get_config,
    set_config,
    init_config,
    send_bot_notification,
    get_prefix,
    module_requests_proxy,
    get_module_proxy_permission,
    set_module_proxy_permission,
    is_module_proxy_enabled,
    get_core_proxy_url
)

logger = get_logger("GHInstaller")

# Метаданные системного модуля
set_module_meta(
    name="Package Manager",
    desc="Установка модулей из репозитория Gitea, фоновый чекер обновлений модулей, поиск с фото и кнопками.",
    system=True
)

init_config("gh_installer", {
    "auto_check_modules": True,
    "check_interval": 900,  # 15 минут в секундах
    "snoozed_hashes": {},
    "allow_break_system_packages": False
})

# 🔗 Ссылка на репозиторий (только Gitea)
REPO_SOURCES = [
    {
        "name": "Gitea",
        "index_url": "https://gitea.com/aswer/ubtg-repo/raw/branch/main/index.json",
        "raw_base_url": "https://gitea.com/aswer/ubtg-repo/raw/branch/main/",
        "web_base_url": "https://gitea.com/aswer/ubtg-repo/src/branch/main/"
    }
]

# Для обратной совместимости
INDEX_URL = REPO_SOURCES[0]["index_url"]

# --- НАСТРОЙКИ КЭША И СЕССИЙ ---
CACHE_TIMEOUT = 3600  # 1 час кэша
_repo_cache = None
_last_repo_update = 0
_search_sessions = {}
_last_notified_module_hashes = {}  # {alias: remote_hash}

# Кэш удаленного содержимого файлов для быстрого сравнения хэшей
_remote_content_cache = {}  # {path: (code, sha256, timestamp)}
_REMOTE_CACHE_TTL = 300  # 5 минут


def get_file_hash(content):
    """Возвращает SHA-256 хэш строки кода (без лишних пробелов по краям)."""
    if isinstance(content, str):
        content = content.strip().encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def get_local_module_code(file_name):
    """Возвращает исходный код локально установленного модуля."""
    mod_dir = get_modules_dir()
    file_path = os.path.join(mod_dir, file_name)
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def get_local_module_hash(file_name):
    """Возвращает SHA-256 хэш локально установленного модуля."""
    code = get_local_module_code(file_name)
    if code is None:
        return None
    return get_file_hash(code)


def get_modules_dir():
    """Возвращает путь к директории modules/ юзербота."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mod_dir = os.path.join(base_dir, 'modules')
    if not os.path.exists(mod_dir):
        try:
            os.makedirs(mod_dir, exist_ok=True)
        except Exception:
            pass
    return mod_dir


def is_authorized_user(sender_id):
    """Проверяет права владельца для защищенных действий (установка/удаление)."""
    owner_id = get_owner_id()
    if not owner_id:
        return True
    return sender_id == owner_id


def is_module_installed(file_name_or_module_name):
    """Проверяет, установлен ли модуль в папке modules."""
    mod_dir = get_modules_dir()
    if not os.path.exists(mod_dir):
        return False
    name = file_name_or_module_name if file_name_or_module_name.endswith('.py') else f"{file_name_or_module_name}.py"
    return os.path.exists(os.path.join(mod_dir, name))


def find_installed_module_file(mod):
    """Ищет установленный файл модуля в modules/ (по file_name, alias или module_name)."""
    mod_dir = get_modules_dir()
    if not os.path.exists(mod_dir):
        return None
    for candidate in [mod.get("file_name", ""), f"{mod.get('alias', '')}.py", f"{mod.get('module_name', '')}.py"]:
        if candidate and candidate.endswith(".py"):
            path = os.path.join(mod_dir, candidate)
            if os.path.exists(path):
                return candidate
    return None


def is_pep668_error(err_msg: str) -> bool:
    """Проверяет, вызвана ли ошибка pip ограничением PEP 668 (externally-managed-environment)."""
    if not err_msg:
        return False
    msg_lower = err_msg.lower()
    return (
        "--break-system-packages" in msg_lower
        or "externally-managed-environment" in msg_lower
        or "pep 668" in msg_lower
    )


async def pip_install(package_name, break_system_packages=False):
    """Асинхронная установка пакетов через pip с поддержкой флага --break-system-packages."""
    logger.info(f"Запуск установки пакета pip: {package_name} (break_system_packages={break_system_packages})")
    cmd = [sys.executable, "-m", "pip", "install"]
    if break_system_packages:
        cmd.append("--break-system-packages")
    if isinstance(package_name, (list, tuple)):
        cmd.extend(package_name)
    else:
        cmd.append(str(package_name))

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    
    if process.returncode == 0:
        return True, ""
    
    err_msg = stderr.decode('utf-8', errors='ignore').strip()
    if not err_msg:
        err_msg = stdout.decode('utf-8', errors='ignore').strip()
    return False, err_msg


async def fetch_repo_index(force=False):
    """
    Скачивает и парсит index.json из репозитория с кэшированием.
    Опрашивает репозиторий Gitea.
    """
    global _repo_cache, _last_repo_update
    
    current_time = time.time()
    if not force and _repo_cache is not None:
        if current_time - _last_repo_update < CACHE_TIMEOUT:
            return _repo_cache, ""
            
    last_err = ""
    async with aiohttp.ClientSession() as session:
        for repo in REPO_SOURCES:
            name = repo["name"]
            url = repo["index_url"]
            try:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        text_data = await resp.text()
                        _repo_cache = json.loads(text_data)
                        _last_repo_update = current_time
                        logger.info(f"Индекс успешно загружен из {name} ({url})")
                        return _repo_cache, ""
                    else:
                        last_err = f"{name} вернул HTTP {resp.status}"
                        logger.warning(f"Ошибка загрузки индекса из {name} ({url}): HTTP {resp.status}")
            except Exception as e:
                last_err = f"Ошибка сети при запросе к {name}: {e}"
                logger.warning(f"Не удалось получить индекс из {name} ({url}): {e}")

    # Fallback: сохраненный кэш в памяти
    if _repo_cache is not None:
        return _repo_cache, f"Использован сохраненный кэш ({last_err})"

    # Fallback: проверка локального файла index.json репозитория при сетевой ошибке
    local_index_candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "ubtg-repo", "index.json"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "index.json")
    ]
    for candidate in local_index_candidates:
        if os.path.exists(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    _repo_cache = json.load(f)
                    _last_repo_update = current_time
                    return _repo_cache, "Использован локальный файл index.json"
            except Exception:
                pass

    return None, f"Не удалось получить index.json из репозитория Gitea. {last_err}"


def get_candidate_download_urls(path_or_url):
    """
    Возвращает список пар (source_name, url) для скачивания файла из Gitea.
    """
    urls = []
    rel_path = path_or_url
    for repo in REPO_SOURCES:
        raw_base = repo["raw_base_url"]
        if rel_path.startswith(raw_base):
            rel_path = rel_path[len(raw_base):]
            break
        for prefix in [
            "https://gitea.com/aswer/ubtg-repo/raw/branch/main/",
            "https://raw.githubusercontent.com/Artemon4ik8091/ubtg-repo/refs/heads/main/",
            "https://raw.githubusercontent.com/Artemon4ik8091/ubtg-repo/main/",
            "https://github.com/Artemon4ik8091/ubtg-repo/raw/main/"
        ]:
            if rel_path.startswith(prefix):
                rel_path = rel_path[len(prefix):]
                break

    if not rel_path.startswith("http://") and not rel_path.startswith("https://"):
        rel_clean = rel_path.lstrip('/')
        for repo in REPO_SOURCES:
            urls.append((repo["name"], repo["raw_base_url"] + rel_clean))
    else:
        urls.append(("Direct", path_or_url))
        
    return urls


async def download_module_file(path_or_url, timeout=20):
    """
    Скачивает файл модуля из репозитория Gitea.
    Возвращает кортеж: (content: str | None, error_msg: str, source_name: str)
    """
    candidates = get_candidate_download_urls(path_or_url)
    last_error = ""
    
    async with aiohttp.ClientSession() as session:
        for source_name, url in candidates:
            try:
                async with session.get(url, timeout=timeout) as resp:
                    if resp.status == 200:
                        content = await resp.text()
                        logger.info(f"Файл успешно скачан из {source_name}: {url}")
                        return content, "", source_name
                    else:
                        last_error = f"{source_name} вернул HTTP {resp.status}"
                        logger.warning(f"Ошибка скачивания из {source_name} ({url}): HTTP {resp.status}")
            except Exception as e:
                last_error = f"{source_name} ({type(e).__name__}: {e})"
                logger.warning(f"Ошибка соединения с {source_name} ({url}): {e}")

    return None, f"Не удалось скачать файл с Gitea. ({last_error})", ""


async def get_remote_module_content(path, timeout=15):
    """
    Получает код удаленного модуля с кэшированием хэша.
    Возвращает (code: str | None, sha256: str | None).
    """
    now = time.time()
    if path in _remote_content_cache:
        code, sha, ts = _remote_content_cache[path]
        if now - ts < _REMOTE_CACHE_TTL:
            return code, sha

    code, err, _ = await download_module_file(path, timeout=timeout)
    if code is not None:
        sha = get_file_hash(code)
        _remote_content_cache[path] = (code, sha, now)
        return code, sha
    return None, None


async def check_single_module_update(mod):
    """
    Проверяет, есть ли свежая версия для указанного модуля в репозитории.
    Возвращает кортеж (is_installed: bool, has_update: bool).
    """
    file_name = mod["file_name"]
    if not is_module_installed(file_name):
        return False, False

    local_hash = get_local_module_hash(file_name)
    if not local_hash:
        return True, False

    path = mod.get("path") or mod["file_url"]
    _, remote_hash = await get_remote_module_content(path)
    if remote_hash and remote_hash != local_hash:
        return True, True
    return True, False


def normalize_module_info(alias, raw_val, base_url=None):
    """
    Нормализует данные о модуле строго из полученного index.json.
    Если каких-то параметров нет в индексе, они остаются пустыми / дефолтными.
    """
    base_url = base_url or (REPO_SOURCES[0]["raw_base_url"])
    
    if isinstance(raw_val, str):
        path = raw_val
        name = alias.capitalize()
        desc = ""
        image = ""
        commands = []
        requires = []
    elif isinstance(raw_val, dict):
        path = raw_val.get("path", f"src/{alias}.py")
        name = raw_val.get("name") or alias.capitalize()
        desc = raw_val.get("desc") or raw_val.get("description") or ""
        image = raw_val.get("image") or raw_val.get("banner") or raw_val.get("preview") or ""
        commands = raw_val.get("commands") or []
        requires = raw_val.get("requires") or raw_val.get("deps") or []
    else:
        path = f"src/{alias}.py"
        name = alias.capitalize()
        desc = ""
        image = ""
        commands = []
        requires = []

    if path.startswith("http://") or path.startswith("https://"):
        file_url = path
    else:
        file_url = base_url + path.lstrip('/')

    if image and not (image.startswith("http://") or image.startswith("https://")):
        image = base_url + image.lstrip('/')

    file_name = file_url.split('/')[-1].split('?')[0]
    module_name = file_name[:-3] if file_name.endswith(".py") else file_name

    # Ссылка на исходник в веб-интерфейсе Gitea
    if not (path.startswith("http://") or path.startswith("https://")):
        web_url = REPO_SOURCES[0]["web_base_url"] + path.lstrip('/')
    else:
        web_url = file_url

    return {
        "alias": alias,
        "name": name,
        "desc": desc,
        "image": image,
        "path": path,
        "file_url": file_url,
        "web_url": web_url,
        "file_name": file_name,
        "module_name": module_name,
        "commands": commands,
        "requires": requires
    }


def _clean_old_sessions():
    """Очищает устаревшие поисковые сессии (старше 1 часа)."""
    now = time.time()
    expired = [sid for sid, sdata in _search_sessions.items() if now - sdata.get("created_at", 0) > 3600]
    for sid in expired:
        _search_sessions.pop(sid, None)


def build_card_view(session_id, index=0):
    """
    Формирует карточку модуля с отображением статуса:
    - ⚪️ Не установлен (кнопка Установить)
    - 🟢 Установлен / актуальная версия (кнопка Переустановить)
    - 🟠 Доступно обновление! (кнопка Обновить)
    """
    session = _search_sessions.get(session_id)
    if not session:
        return "⚠️ Сессия поиска устарела. Введите команду заново: `.ghsearch`", None

    items = session.get("items", [])
    if not items or index < 0 or index >= len(items):
        return "⚠️ Модуль не найден.", None

    session["current_idx"] = index
    alias = items[index]
    repo_index = session.get("repo_index", {})
    raw_val = repo_index.get(alias, {})
    mod = normalize_module_info(alias, raw_val)

    total = len(items)
    installed = is_module_installed(mod["file_name"])
    updates_map = session.get("updates_map", {})
    has_update = updates_map.get(alias, False)

    if not installed:
        status_str = "⚪️ **Не установлен**"
        action_btn_text = "📥 Установить"
        action_cb = f"gh_inst:{mod['alias']}"
    elif has_update:
        status_str = "🟠 **Доступно обновление!**"
        action_btn_text = "🆙 Обновить"
        action_cb = f"gh_upd:{mod['alias']}"
    else:
        status_str = "🟢 **Установлен (актуальная версия)**"
        action_btn_text = "🔄 Переустановить"
        action_cb = f"gh_inst:{mod['alias']}"

    # Если картинки нет — маркер не добавляется вообще
    img_embed = f"[\u200b]({mod['image']})" if mod.get("image") else ""

    query_str = session.get("query", "")
    header_prefix = f"🔍 **Результаты поиска:** `{query_str}`" if query_str != "all" else "📦 **Каталог модулей UBTG**"
    page_badge = f" `[{index + 1}/{total}]`" if total > 1 else ""

    # Описание модуля строго из репозитория или пометка об отсутствии
    desc_text = mod["desc"] if mod.get("desc") else "Описания не найдено"

    text = (
        f"{img_embed}{header_prefix}{page_badge}\n\n"
        f"🏷 **Название:** `{mod['name']}` (`{mod['alias']}`)\n"
        f"📖 **Описание:** {desc_text}\n"
    )

    if mod.get("commands"):
        cmds_str = ", ".join([f"`{c}`" if c.startswith(".") else f"`.{c}`" for c in mod["commands"]])
        text += f"🛠 **Команды:** {cmds_str}\n"

    if mod.get("requires"):
        reqs_str = ", ".join([f"`{r}`" for r in mod["requires"]])
        text += f"📦 **Зависимости:** {reqs_str}\n"

    text += f"📊 **Статус:** {status_str}\n"

    buttons = []

    # Ряд 1: Навигация между карточками (если результатов > 1)
    if total > 1:
        prev_idx = (index - 1) % total
        next_idx = (index + 1) % total
        buttons.append([
            Button.inline("◀️ Назад", f"gh_page:{session_id}:{prev_idx}".encode()),
            Button.inline(f"📄 {index + 1}/{total}", f"gh_list:{session_id}".encode()),
            Button.inline("Вперед ▶️", f"gh_page:{session_id}:{next_idx}".encode())
        ])

    # Ряд 2: Кнопка установки/обновления и переход к общему списку
    action_btn = Button.inline(action_btn_text, action_cb.encode())
    if total > 1:
        buttons.append([
            action_btn,
            Button.inline(f"📋 Списком ({total})", f"gh_list:{session_id}".encode())
        ])
    else:
        buttons.append([action_btn])

    # Ряд 3: Исходный код и закрытие
    row3 = []
    src_link = mod.get("web_url") or mod.get("file_url")
    if src_link:
        row3.append(Button.url("🔗 Исходник", src_link))
    row3.append(Button.inline("❌ Закрыть", f"gh_close:{session_id}".encode()))
    buttons.append(row3)

    return text, buttons


def build_list_view(session_id):
    """
    Формирует текстовый список всех найденных модулей с быстрыми кнопками и статусом обновлений.
    """
    session = _search_sessions.get(session_id)
    if not session:
        return "⚠️ Сессия поиска устарела. Введите `.ghsearch`", None

    items = session.get("items", [])
    total = len(items)
    repo_index = session.get("repo_index", {})
    updates_map = session.get("updates_map", {})
    query_str = session.get("query", "")
    header = f"📋 **Найдено модулей ({total}) по запросу '{query_str}':**\n\n" if query_str != "all" else f"📋 **Все модули репозитория ({total}):**\n\n"

    text = header
    for idx, alias in enumerate(items, 1):
        mod = normalize_module_info(alias, repo_index.get(alias, {}))
        installed = is_module_installed(mod["file_name"])
        has_update = updates_map.get(alias, False)
        
        if not installed:
            badge = "⚪️"
            upd_note = ""
        elif has_update:
            badge = "🆙"
            upd_note = " — *(Доступно обновление!)*"
        else:
            badge = "🟢"
            upd_note = ""

        raw_desc = mod.get("desc") or "Описания не найдено"
        desc_cut = raw_desc[:55] + "..." if len(raw_desc) > 55 else raw_desc
        text += f"**{idx}.** {badge} **{mod['name']}** (`{alias}`){upd_note}\n"
        text += f"   └ *{desc_cut}*\n"

    text += "\n💡 *Нажмите на номер ниже для перехода к карточке или используйте `.ghinstall <имя>`*"

    buttons = []
    # Быстрые кнопки номеров страниц (по 5 в ряд)
    cur_row = []
    for i in range(total):
        cur_row.append(Button.inline(f"{i + 1}", f"gh_page:{session_id}:{i}".encode()))
        if len(cur_row) == 5:
            buttons.append(cur_row)
            cur_row = []
    if cur_row:
        buttons.append(cur_row)

    cur_idx = session.get("current_idx", 0)
    buttons.append([
        Button.inline("🖼 Вернуться к карточкам", f"gh_page:{session_id}:{cur_idx}".encode()),
        Button.inline("❌ Закрыть", f"gh_close:{session_id}".encode())
    ])

    return text, buttons


_pending_bsp_confirmations = {}  # {chat_id: {"alias": alias, "time": timestamp, ...}}


async def show_pep668_confirmation(client, chat_id, message_id, package_alias, missing_pkg, event=None):
    """
    Предупреждает пользователя об ошибке PEP 668 и флаге --break-system-packages,
    запрашивая подтверждение на продолжение установки.
    """
    prefix = get_prefix() or "."
    warn_text = (
        f"⚠️ **Внимание: Защита системного окружения (PEP 668)!**\n\n"
        f"📦 Модуль `{package_alias}` требует библиотеку `{missing_pkg}`.\n"
        f"🔒 В вашей операционной системе активен режим `externally-managed-environment`, "
        f"который блокирует автоматическую установку пакетов в системный Python.\n\n"
        f"ℹ️ Для установки этой библиотеки в текущую систему требуется флаг:\n"
        f"`--break-system-packages`\n\n"
        f"⚠️ **Предупреждение:** Этот флаг принудительно устанавливает пакет в системное окружение ОС. "
        f"В большинстве случаев это безопасно для модулей юзербота, но может нести "
        f"потенциальный риск конфликта с системными пакетами дистрибутива Linux.\n\n"
        f"❓ **Вы точно хотите продолжить установку с этим флагом?**"
    )

    buttons = [
        [
            Button.inline("✅ Да, продолжить (--break-system-packages)", f"gh_bsp:yes:{package_alias}".encode()),
        ],
        [
            Button.inline("🔄 Всегда разрешать (для всех модулей)", f"gh_bsp:always:{package_alias}".encode()),
        ],
        [
            Button.inline("❌ Отменить установку", f"gh_bsp:cancel:{package_alias}".encode())
        ]
    ]

    _pending_bsp_confirmations[str(chat_id)] = {
        "alias": package_alias,
        "missing_pkg": missing_pkg,
        "chat_id": chat_id,
        "message_id": message_id,
        "time": time.time()
    }

    # 1. Если event - это inline callback query, редактируем его кнопками
    if event and hasattr(event, "edit"):
        try:
            await event.edit(warn_text, buttons=buttons)
            return
        except Exception:
            pass

    # 2. Пробуем отправить инлайн-сообщение с кнопками через send_inline
    bot_username = get_bot_username()
    target_client = client or get_main_client()
    if target_client and bot_username:
        try:
            await send_inline(
                target_client,
                chat_id,
                warn_text,
                buttons=buttons
            )
            if event and hasattr(event, "delete"):
                try:
                    await event.delete()
                except Exception:
                    pass
            return
        except Exception as e:
            logger.debug(f"send_inline в show_pep668_confirmation не сработал: {e}")

    # 3. Fallback: редактируем исходное сообщение обычным текстом с подсказками
    fallback_text = (
        warn_text +
        f"\n\n💡 **Варианты действий:**\n"
        f"• Нажмите кнопку выше (если отображаются)\n"
        f"• Или введите команду: `{prefix}confirm` (или `{prefix}yes`)\n"
        f"• Или запустите с флагом: `{prefix}ghinstall {package_alias} --break-system-packages`\n"
        f"• Разрешить навсегда: `{prefix}cfg set gh_installer allow_break_system_packages true`\n"
        f"• Отмена: `{prefix}cancel`"
    )

    if event and hasattr(event, "edit"):
        try:
            await event.edit(fallback_text, buttons=None)
            return
        except Exception:
            pass

    bot = get_bot()
    if bot:
        try:
            await bot.edit_message(chat_id, message_id, fallback_text, buttons=None)
            return
        except Exception:
            pass

    if target_client:
        try:
            await target_client.edit_message(chat_id, message_id, fallback_text, buttons=None)
            return
        except Exception:
            pass


_pending_proxy_confirmations = {}  # {chat_id: {"alias": alias, "time": timestamp, ...}}


async def show_proxy_confirmation(client, chat_id, message_id, package_alias, module_name, event=None, allow_break_system_packages=False):
    """
    Запрашивает у пользователя разрешение на маршрутизацию сетевых запросов модуля через прокси ядра.
    """
    prefix = get_prefix() or "."
    core_proxy_url = get_core_proxy_url()
    proxy_status_str = f"`{core_proxy_url}`" if core_proxy_url else "*(прокси ядра пока не настроен, можно задать через `.proxy set`)*"

    warn_text = (
        f"🌐 **Запрос доступа к прокси для модуля `{package_alias}`!**\n\n"
        f"📦 Модуль `{package_alias}` запрашивает маршрутизацию сетевых запросов через прокси ядра.\n\n"
        f"🔌 **Текущий прокси ядра:**\n{proxy_status_str}\n\n"
        f"❓ **Разрешить модулю `{package_alias}` использовать прокси юзербота?**\n\n"
        f"• **Разрешить:** все HTTP/HTTPS запросы модуля пойдут через сетевой прокси ядра.\n"
        f"• **Отклонить:** модуль будет подключаться к сети напрямую без использования прокси.\n\n"
        f"💡 *Вы всегда сможете изменить выбор позже в меню `{prefix}proxy`.*"
    )

    buttons = [
        [
            Button.inline("✅ Разрешить прокси", f"gh_px:allow:{package_alias}".encode()),
            Button.inline("❌ Без прокси (напрямую)", f"gh_px:deny:{package_alias}".encode()),
        ],
        [
            Button.inline("⏹ Отменить установку", f"gh_px:cancel:{package_alias}".encode())
        ]
    ]

    _pending_proxy_confirmations[str(chat_id)] = {
        "alias": package_alias,
        "module_name": module_name,
        "chat_id": chat_id,
        "message_id": message_id,
        "allow_break": allow_break_system_packages,
        "time": time.time()
    }

    # 1. Если event - это inline callback query, редактируем его кнопками
    if event and hasattr(event, "edit"):
        try:
            await event.edit(warn_text, buttons=buttons)
            return
        except Exception:
            pass

    # 2. Пробуем отправить инлайн-сообщение с кнопками через send_inline
    bot_username = get_bot_username()
    target_client = client or get_main_client()
    if target_client and bot_username:
        try:
            await send_inline(
                target_client,
                chat_id,
                warn_text,
                buttons=buttons
            )
            if event and hasattr(event, "delete"):
                try:
                    await event.delete()
                except Exception:
                    pass
            return
        except Exception as e:
            logger.debug(f"send_inline в show_proxy_confirmation не сработал: {e}")

    # 3. Fallback: редактируем исходное сообщение обычным текстом с подсказками
    fallback_text = (
        warn_text +
        f"\n\n💡 **Варианты ответа:**\n"
        f"• Введите команду: `{prefix}allowproxy` (или `{prefix}proxyyes`)\n"
        f"• Или команду: `{prefix}denyproxy` (или `{prefix}proxyno`) для прямого доступа\n"
        f"• Или запустите с флагом: `{prefix}ghinstall {package_alias} --proxy` (или `--no-proxy`)\n"
        f"• Отмена: `{prefix}cancel`"
    )

    if event and hasattr(event, "edit"):
        try:
            await event.edit(fallback_text, buttons=None)
            return
        except Exception:
            pass

    bot = get_bot()
    if bot:
        try:
            await bot.edit_message(chat_id, message_id, fallback_text, buttons=None)
            return
        except Exception:
            pass

    if target_client:
        try:
            await target_client.edit_message(chat_id, message_id, fallback_text, buttons=None)
            return
        except Exception:
            pass


async def perform_module_install(client, chat_id, message_id, package_alias, event=None, allow_break_system_packages=False, proxy_choice=None):
    """
    Единая логика скачивания, установки зависимостей, импорта и перезапуска модуля.
    """
    logger.info(f"Начало установки модуля: {package_alias} (allow_break_system_packages={allow_break_system_packages}, proxy_choice={proxy_choice})")

    cfg_allow = bool(get_config("gh_installer", "allow_break_system_packages", False))
    allow_break = allow_break_system_packages or cfg_allow

    async def update_status(text_msg):
        if event:
            try:
                await event.edit(text_msg, buttons=None)
                return
            except Exception:
                pass
        bot = get_bot()
        if bot:
            try:
                await bot.edit_message(chat_id, message_id, text_msg, buttons=None)
                return
            except Exception:
                pass
        if client:
            try:
                await client.edit_message(chat_id, message_id, text_msg, buttons=None)
                return
            except Exception:
                pass

    try:
        await update_status(f"🔍 `Поиск пакета '{package_alias}' в репозитории...`")
        repo_index, err = await fetch_repo_index()
        if repo_index is None:
            return await update_status(f"❌ **Ошибка доступа к репозиторию!**\n`{err}`")

        if package_alias not in repo_index:
            # Принудительно пробуем обновить индекс (вдруг добавлен недавно в Gitea)
            repo_index, err = await fetch_repo_index(force=True)

        if not repo_index or package_alias not in repo_index:
            avail = ", ".join([f"`{k}`" for k in repo_index.keys()]) if repo_index else "нет"
            return await update_status(f"❌ Пакет `{package_alias}` не найден в репозитории Gitea.\n📦 **Доступные модули:**\n{avail}")

        mod = normalize_module_info(package_alias, repo_index[package_alias])
        file_name = mod["file_name"]
        module_name = mod["module_name"]
        path = mod.get("path") or mod["file_url"]

        await update_status(f"⏳ `Скачиваю {file_name}...`")

        code, dl_err, source_used = await download_module_file(path)
        if code is None:
            return await update_status(f"❌ **Ошибка скачивания `{file_name}`!**\n{dl_err}")

        logger.info(f"Модуль {package_alias} успешно скачан из {source_used}")

        modules_dir = get_modules_dir()
        if modules_dir not in sys.path:
            sys.path.insert(0, modules_dir)

        file_path = os.path.join(modules_dir, file_name)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code)

        importlib.invalidate_caches()

        # Анализ запроса на использование прокси ядра
        if proxy_choice is not None:
            set_module_proxy_permission(package_alias, proxy_choice)
        else:
            existing_perm = get_module_proxy_permission(package_alias)
            if existing_perm is not None:
                proxy_choice = existing_perm
            elif module_requests_proxy(code, mod):
                logger.info(f"Модуль {package_alias} запрашивает доступ к прокси ядра. Запрос подтверждения у пользователя.")
                return await show_proxy_confirmation(
                    client,
                    chat_id,
                    message_id,
                    package_alias,
                    module_name,
                    event=event,
                    allow_break_system_packages=allow_break
                )

        # Анализ явных зависимостей из комментариев # requires: ...
        requires_match = re.search(r"^\s*#\s*requires:\s*(.+)$", code, re.MULTILINE | re.IGNORECASE)
        deps = []
        if requires_match:
            deps = [d.strip() for d in re.split(r"[\s,]+", requires_match.group(1)) if d.strip()]
        if not deps and mod.get("requires"):
            deps = mod["requires"]

        # Исключаем имя самого модуля и алиас из зависимостей pip
        deps = [d for d in deps if d and d != module_name and d != package_alias]

        if deps:
            await update_status(f"📦 `Найдено зависимостей: {len(deps)}. Устанавливаю: {', '.join(deps)}...`")
            for dep in deps:
                success, pip_err = await pip_install(dep, break_system_packages=allow_break)
                if not success:
                    if not allow_break and is_pep668_error(pip_err):
                        logger.warning(f"PEP 668 при установке '{dep}' для '{package_alias}'. Запрос подтверждения.")
                        return await show_pep668_confirmation(client, chat_id, message_id, package_alias, dep, event=event)
                    tr_err = pip_err[-200:] if len(pip_err) > 200 else pip_err
                    await update_status(f"⚠️ Предупреждение pip при установке `{dep}`:\n`...{tr_err}`\nПродолжаю...")
                    await asyncio.sleep(2)

        # Динамический импорт / перезагрузка с авто-доустановкой
        max_attempts = 5
        imported_successfully = False

        for attempt in range(max_attempts):
            try:
                importlib.invalidate_caches()
                if module_name in sys.modules:
                    importlib.reload(sys.modules[module_name])
                else:
                    importlib.import_module(module_name)
                imported_successfully = True
                break
            except ModuleNotFoundError as err:
                missing_pkg = err.name
                if not missing_pkg or missing_pkg == module_name or missing_pkg == package_alias:
                    raise err

                await update_status(f"🔍 Модулю требуется библиотека `{missing_pkg}`. Устанавливаю через pip...")
                success, pip_err = await pip_install(missing_pkg, break_system_packages=allow_break)
                if not success:
                    if not allow_break and is_pep668_error(pip_err):
                        logger.warning(f"PEP 668 при установке '{missing_pkg}' для '{package_alias}'. Запрос подтверждения.")
                        return await show_pep668_confirmation(client, chat_id, message_id, package_alias, missing_pkg, event=event)
                    tr_err = pip_err[-300:] if len(pip_err) > 300 else pip_err
                    raise Exception(f"Не удалось установить `{missing_pkg}`.\nОшибка pip:\n`...{tr_err}`")

                await update_status(f"✅ Пакет `{missing_pkg}` установлен! Пробую запустить модуль...")
                await asyncio.sleep(1)

        if imported_successfully:
            snoozed = get_config("gh_installer", "snoozed_hashes", {})
            if isinstance(snoozed, dict) and package_alias in snoozed:
                snoozed.pop(package_alias, None)
                set_config("gh_installer", "snoozed_hashes", snoozed)
            _last_notified_module_hashes.pop(package_alias, None)

            proxy_note = ""
            if is_module_proxy_enabled(package_alias):
                core_p = get_core_proxy_url()
                proxy_note = f"\n🌐 *Трафик модуля направлен через прокси ядра (`{core_p or 'пока не задан'}`).*"
            elif get_module_proxy_permission(package_alias) is False:
                proxy_note = f"\n⚪️ *Модуль выходит в сеть напрямую без прокси.*"

            success_text = f"✅ **Пакет `{mod['name']}` (`{package_alias}`) успешно установлен!**{proxy_note}\n🔄 *Перезапускаю юзербота для применения изменений...*"
            await update_status(success_text)
            await asyncio.sleep(0.3)
            logger.info(f"Пакет {package_alias} ({module_name}) успешно установлен. Перезапуск...")
            final_text = f"✅ **Пакет `{mod['name']}` (`{package_alias}`) успешно установлен и готов к работе!**{proxy_note}"
            target_client = client or get_main_client()
            await restart_userbot(target_client, chat_id, message_id, custom_text=final_text, event=event)
        else:
            await update_status(f"❌ Не удалось запустить модуль `{module_name}`.")

    except Exception as e:
        tb_str = traceback.format_exc()
        truncated_tb = tb_str[-600:] if len(tb_str) > 600 else tb_str
        logger.error(f"Сбой установки {package_alias}: {e}\n{tb_str}")
        await update_status(f"❌ **Сбой установки!**\n\nℹ️ **Причина:** `{e}`\n\n📋 **Traceback:**\n`...{truncated_tb}`")


# ==========================================
# ОБРАБОТЧИКИ НАЖАТИЙ НА ИНЛАЙН КНОПКИ
# ==========================================

@register_callback("gh_page:")
async def cb_gh_page(event, data):
    """Переключение между карточками найденных модулей."""
    payload = data[len("gh_page:"):]
    try:
        session_id, idx_str = payload.rsplit(":", 1)
        idx = int(idx_str)
    except Exception:
        return await event.answer("⚠️ Ошибка навигации", alert=True)

    text, buttons = build_card_view(session_id, idx)
    try:
        await event.edit(text, buttons=buttons, link_preview=True)
    except errors.MessageNotModifiedError:
        await event.answer()
    except Exception as e:
        if "not modified" in str(e).lower():
            await event.answer()
        else:
            logger.error(f"Ошибка редактирования карточки: {e}")


@register_callback("gh_list:")
async def cb_gh_list(event, data):
    """Переключение на просмотр списка всех найденных модулей."""
    session_id = data[len("gh_list:"):]
    text, buttons = build_list_view(session_id)
    try:
        await event.edit(text, buttons=buttons, link_preview=False)
    except errors.MessageNotModifiedError:
        await event.answer()
    except Exception as e:
        if "not modified" in str(e).lower():
            await event.answer()
        else:
            logger.error(f"Ошибка переключения на список: {e}")


@register_callback("gh_close:")
async def cb_gh_close(event, data):
    """Закрытие поискового или проверочного меню."""
    session_id = data[len("gh_close:"):]
    _search_sessions.pop(session_id, None)
    await event.answer("Закрыто")
    try:
        await event.delete()
    except Exception:
        try:
            await event.edit("❌ **Меню закрыто.**", buttons=None)
        except Exception:
            pass


@register_callback("gh_inst:")
async def cb_gh_install(event, data):
    """Кнопка 'Установить' / 'Переустановить' из карточки."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    alias = data[len("gh_inst:"):]
    await event.answer(f"🚀 Запуск установки '{alias}'...")
    msg_id = getattr(event, "message_id", None) or getattr(event, "id", 0)
    await perform_module_install(get_main_client(), event.chat_id, msg_id, alias, event=event)


@register_callback("gh_bsp:")
async def cb_gh_break_system_packages(event, data):
    """Обработчик подтверждения флага --break-system-packages (PEP 668)."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    payload = data[len("gh_bsp:"):].decode("utf-8") if isinstance(data, bytes) else data[len("gh_bsp:"):]
    parts = payload.split(":", 1)
    if len(parts) < 2:
        return await event.answer("⚠️ Неверный формат данных.", alert=True)

    action, alias = parts[0], parts[1]
    _pending_bsp_confirmations.pop(str(event.chat_id), None)

    if action == "cancel":
        await event.answer("❌ Установка отменена.")
        try:
            modules_dir = get_modules_dir()
            for ext in [".py"]:
                fpath = os.path.join(modules_dir, f"{alias}{ext}")
                if os.path.exists(fpath) and alias not in sys.modules:
                    os.remove(fpath)
        except Exception:
            pass
        try:
            await event.edit(
                f"❌ **Установка модуля `{alias}` отменена пользователем.**\n\n"
                f"ℹ️ Для установки зависимостей требовался флаг `--break-system-packages`.",
                buttons=None
            )
        except Exception:
            pass
        return

    allow_always = (action == "always")
    if allow_always:
        set_config("gh_installer", "allow_break_system_packages", True)
        set_config("installer", "allow_break_system_packages", True)
        await event.answer("⚙️ Опция сохранена! Устанавливаю...", alert=False)
    else:
        await event.answer("🚀 Продолжаю установку с --break-system-packages...", alert=False)

    msg_id = getattr(event, "message_id", None) or getattr(event, "id", 0)
    try:
        await event.edit(
            f"⏳ `Продолжаю установку '{alias}' с флагом --break-system-packages...`",
            buttons=None
        )
    except Exception:
        pass

    target_client = get_main_client()
    await perform_module_install(
        target_client,
        event.chat_id,
        msg_id,
        alias,
        event=event,
        allow_break_system_packages=True
    )


@register_callback("gh_px:")
async def cb_gh_proxy(event, data):
    """Обработчик подтверждения использования прокси для модуля."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    payload = data[len("gh_px:"):].decode("utf-8") if isinstance(data, bytes) else data[len("gh_px:"):]
    parts = payload.split(":", 1)
    if len(parts) < 2:
        return await event.answer("⚠️ Неверный формат данных.", alert=True)

    action, alias = parts[0], parts[1]
    pending = _pending_proxy_confirmations.pop(str(event.chat_id), None)
    allow_break = pending.get("allow_break", False) if pending else False

    if action == "cancel":
        await event.answer("❌ Установка отменена.")
        try:
            modules_dir = get_modules_dir()
            for ext in [".py"]:
                fpath = os.path.join(modules_dir, f"{alias}{ext}")
                if os.path.exists(fpath) and alias not in sys.modules:
                    os.remove(fpath)
        except Exception:
            pass
        try:
            await event.edit(f"❌ **Установка модуля `{alias}` отменена пользователем.**", buttons=None)
        except Exception:
            pass
        return

    choice = (action == "allow")
    set_module_proxy_permission(alias, choice)
    status_msg = "Прокси разрешен 🟢" if choice else "Прямое подключение ⚪️"
    await event.answer(f"{status_msg}!")

    msg_id = getattr(event, "message_id", None) or getattr(event, "id", 0)
    try:
        await event.edit(
            f"⏳ `{status_msg}. Продолжаю установку модуля '{alias}'...`",
            buttons=None
        )
    except Exception:
        pass

    target_client = get_main_client()
    await perform_module_install(
        target_client,
        event.chat_id,
        msg_id,
        alias,
        event=event,
        allow_break_system_packages=allow_break,
        proxy_choice=choice
    )


@register_callback("gh_upd:")
async def cb_gh_update(event, data):
    """Кнопка 'Обновить' для конкретного модуля из карточки или чекера."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    alias = data[len("gh_upd:"):]
    await event.answer(f"🚀 Обновляю модуль '{alias}'...")
    msg_id = getattr(event, "message_id", None) or getattr(event, "id", 0)
    await perform_bulk_upgrade(get_main_client(), event.chat_id, msg_id, force_all=False, target_alias=alias, event=event)


@register_callback("gh_upd_all")
async def cb_gh_update_all(event, data):
    """Кнопка 'Обновить все доступные' из меню проверки обновлений."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    await event.answer("🚀 Запуск обновления всех устаревших модулей...")
    msg_id = getattr(event, "message_id", None) or getattr(event, "id", 0)
    await perform_bulk_upgrade(get_main_client(), event.chat_id, msg_id, force_all=False, event=event)


@register_callback("gh_check_recheck")
async def cb_gh_check_recheck(event, data):
    """Кнопка 'Проверить снова' в результатах проверки обновлений."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    await event.answer("🔄 Проверяю обновления...")
    await check_modules_cmd(get_main_client(), event, "")


@register_callback("bot_gh_upd_all")
async def cb_bot_gh_update_all(event, data):
    """Кнопка 'Обновить все' из уведомления встроенного бота."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    await event.answer("🚀 Запуск обновления всех устаревших модулей...")
    msg_id = getattr(event, "message_id", None) or getattr(event, "id", 0)
    await perform_bulk_upgrade(get_main_client(), event.chat_id, msg_id, force_all=False, event=event)


@register_callback("bot_gh_upd:")
async def cb_bot_gh_update(event, data):
    """Кнопка 'Обновить' конкретный модуль из уведомления встроенного бота."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    alias = data[len("bot_gh_upd:"):]
    await event.answer(f"🚀 Обновляю модуль '{alias}'...")
    msg_id = getattr(event, "message_id", None) or getattr(event, "id", 0)
    await perform_bulk_upgrade(get_main_client(), event.chat_id, msg_id, force_all=False, target_alias=alias, event=event)


@register_callback("bot_gh_snooze")
async def cb_bot_gh_snooze(event, data):
    """Кнопка 'Отложить' из уведомления встроенного бота об обновлениях модулей."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    repo_index, _ = await fetch_repo_index()
    snoozed = get_config("gh_installer", "snoozed_hashes", {})
    if not isinstance(snoozed, dict):
        snoozed = {}

    if repo_index:
        for alias, raw in repo_index.items():
            mod = normalize_module_info(alias, raw)
            target_file = find_installed_module_file(mod)
            if target_file:
                path = mod.get("path") or mod["file_url"]
                _, rem_hash = await get_remote_module_content(path)
                loc_hash = get_local_module_hash(target_file)
                if rem_hash and loc_hash and rem_hash != loc_hash:
                    snoozed[alias] = rem_hash

    set_config("gh_installer", "snoozed_hashes", snoozed)
    await event.answer("⏳ Уведомление отложено")

    p = get_prefix()
    hint = (
        "⏳ **Напоминание об обновлениях модулей отложено.**\n\n"
        "Бот больше не будет напоминать об этих версиях.\n"
        f"💡 Чтобы обновить их позже, используйте команду `{p}upgrade` "
        "или нажмите кнопку ниже."
    )
    buttons = [[Button.inline("🚀 Обновить сейчас", b"bot_gh_upd_all")]]
    try:
        await event.edit(hint, buttons=buttons)
    except errors.MessageNotModifiedError:
        pass


# ==========================================
# ФОНОВАЯ ЗАДАЧА: АВТО-ПРОВЕРКА ОБНОВЛЕНИЙ МОДУЛЕЙ
# ==========================================

async def check_and_notify_module_updates():
    """
    Проверяет репозиторий на наличие свежих версий для установленных модулей.
    Если найдены обновления, отправляет уведомление владельцу через встроенного бота.
    """
    repo_index, err = await fetch_repo_index(force=True)
    if not repo_index:
        logger.debug(f"Фоновый чек модулей: не удалось загрузить индекс: {err}")
        return

    installed_modules = []
    for alias, raw in repo_index.items():
        mod = normalize_module_info(alias, raw)
        target_file = find_installed_module_file(mod)
        if target_file:
            mod["file_name"] = target_file
            installed_modules.append((alias, mod))

    if not installed_modules:
        logger.debug("Фоновый чек модулей: нет установленных модулей из каталога репозитория.")
        return

    outdated = []
    snoozed_hashes = get_config("gh_installer", "snoozed_hashes", {})
    if not isinstance(snoozed_hashes, dict):
        snoozed_hashes = {}

    for alias, mod in installed_modules:
        path = mod.get("path") or mod["file_url"]
        _, remote_hash = await get_remote_module_content(path)
        if not remote_hash:
            continue

        local_hash = get_local_module_hash(mod["file_name"])
        if not local_hash:
            continue

        if remote_hash != local_hash:
            outdated.append((alias, mod, remote_hash))

    if not outdated:
        logger.debug("Фоновый чек модулей: все установленные модули актуальны.")
        return

    # Проверяем, есть ли хотя бы один модуль с новым хэшем, о котором еще не уведомляли
    needs_notification = False
    for alias, mod, r_hash in outdated:
        if snoozed_hashes.get(alias) != r_hash and _last_notified_module_hashes.get(alias) != r_hash:
            needs_notification = True
            break

    if not needs_notification:
        logger.debug("Фоновый чек модулей: уведомление об этих версиях уже было отправлено или отложено.")
        return

    p = get_prefix()
    count = len(outdated)
    lines = []
    for alias, mod, r_hash in outdated:
        lines.append(f"• 🆙 **{mod['name']}** (`{alias}`)")

    msg = (
        f"🔔 **Вышло обновление для модулей UBTG!**\n\n"
        f"В репозитории обнаружены новые версии (`{count}`):\n"
        + "\n".join(lines)
        + f"\n\n💡 *Вы можете обновить их кнопкой ниже или командой `{p}upgrade`.*"
    )

    buttons = [
        [Button.inline(f"🚀 Обновить все ({count})", b"bot_gh_upd_all")]
    ]

    # Индивидуальные кнопки для обновления первых модулей
    indiv_row = []
    for alias, mod, _ in outdated[:4]:
        indiv_row.append(Button.inline(f"🆙 {mod['name'][:12]}", f"bot_gh_upd:{alias}".encode()))
        if len(indiv_row) == 2:
            buttons.append(indiv_row)
            indiv_row = []
    if indiv_row:
        buttons.append(indiv_row)

    buttons.append([Button.inline("⏳ Отложить", b"bot_gh_snooze")])

    bot = get_bot()
    owner_id = get_owner_id()
    sent = False
    if bot and owner_id:
        try:
            await bot.send_message(owner_id, msg, buttons=buttons)
            sent = True
            logger.info(f"Уведомление об обновлении {count} модулей успешно отправлено владельцу через бота.")
        except Exception as ex:
            logger.warning(f"Ошибка отправки уведомления о модулях ботом: {ex}")

    if not sent:
        await send_bot_notification(msg)

    # Запоминаем отправленные хэши
    for alias, mod, r_hash in outdated:
        _last_notified_module_hashes[alias] = r_hash


@register_bg()
async def auto_module_update_checker(client):
    """
    Фоновый процесс: периодически опрашивает репозиторий модулей (Gitea)
    и сравнивает версии установленных модулей.
    При выходе обновления присылает уведомление владельцу через встроенного бота.
    """
    logger.debug("Запуск фонового чекера авто-обновлений модулей...")
    # Ждем 75 секунд после запуска, чтобы ядро успело полностью инициализироваться
    await asyncio.sleep(75)

    while True:
        try:
            enabled = get_config("gh_installer", "auto_check_modules", True)
            if enabled:
                await check_and_notify_module_updates()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Ошибка в фоновом чекере модулей: {e}")

        interval = get_config("gh_installer", "check_interval", 900)
        try:
            interval = int(interval)
            if interval < 60:
                interval = 60
        except (ValueError, TypeError):
            interval = 900

        await asyncio.sleep(interval)


# ==========================================
# ФУНКЦИЯ МАССОВОГО ОБНОВЛЕНИЯ СО СЧЁТЧИКОМ
# ==========================================

async def perform_bulk_upgrade(client, chat_id, message_id, force_all=False, target_alias=None, event=None):
    """
    Выполняет обновление модулей с живым счётчиком прогресса:
    [X/Y] Обновлено: ..., Осталось: ..., Текущий модуль: ...
    """
    async def update_status(text_msg):
        if event:
            try:
                await event.edit(text_msg, buttons=None)
                return
            except Exception:
                pass
        bot = get_bot()
        if bot:
            try:
                await bot.edit_message(chat_id, message_id, text_msg, buttons=None)
                return
            except Exception:
                pass
        if client:
            try:
                await client.edit_message(chat_id, message_id, text_msg, buttons=None)
                return
            except Exception:
                pass

    try:
        await update_status("🔄 `Загружаю индекс репозитория...`")
        repo_index, err = await fetch_repo_index(force=True)
        if repo_index is None:
            return await update_status(f"❌ **Ошибка доступа к репозиторию:**\n`{err}`")

        modules_dir = get_modules_dir()
        if modules_dir not in sys.path:
            sys.path.insert(0, modules_dir)

        # Определение списка модулей для обновления
        if target_alias:
            if target_alias not in repo_index:
                return await update_status(f"❌ Модуль `{target_alias}` не найден в репозитории.")
            targets = [target_alias]
        elif force_all:
            # Все установленные модули из репозитория
            targets = [
                alias for alias, raw in repo_index.items()
                if is_module_installed(normalize_module_info(alias, raw)["file_name"])
            ]
            if not targets:
                return await update_status("🤷‍♂️ **Нет установленных модулей из репозитория.**")
        else:
            # Только те модули, у которых есть новая версия
            await update_status("🔍 `Проверяю наличие обновлений для установленных модулей...`")
            installed_modules = []
            for alias, raw in repo_index.items():
                mod = normalize_module_info(alias, raw)
                if is_module_installed(mod["file_name"]):
                    installed_modules.append((alias, mod))

            if not installed_modules:
                return await update_status(
                    "🤷‍♂️ **Нет установленных модулей из репозитория.**\n"
                    "💡 Используйте `.ghsearch all` для просмотра каталога."
                )

            targets = []
            for alias, mod in installed_modules:
                _, has_upd = await check_single_module_update(mod)
                if has_upd:
                    targets.append(alias)

            if not targets:
                count_inst = len(installed_modules)
                return await update_status(
                    f"✅ **Все установленные модули ({count_inst}) уже обновлены до актуальной версии!**\n\n"
                    f"💡 *Если хотите принудительно переустановить все модули, используйте:* `.upgrade force`"
                )

        total_targets = len(targets)
        upgraded = []
        errors_list = []

        for idx, alias in enumerate(targets, 1):
            remaining = total_targets - idx
            mod = normalize_module_info(alias, repo_index[alias])
            mod_name = mod["name"]
            file_name = mod["file_name"]
            file_path = os.path.join(modules_dir, file_name)
            path = mod.get("path") or mod["file_url"]

            progress_bar = f"`[{idx}/{total_targets}]`"
            status_text = (
                f"⏳ **Обновление модулей** {progress_bar}\n\n"
                f"⚙️ **Текущий:** `{mod_name}` (`{alias}`)\n"
                f"📊 **Обновлено:** `{len(upgraded)}` | **Осталось:** `{remaining + 1}`\n\n"
                f"🔹 `Скачивание исходного кода...`"
            )
            await update_status(status_text)

            try:
                code, dl_err, source_used = await download_module_file(path)
                if code is None:
                    errors_list.append(f"{mod_name} ({alias}): {dl_err}")
                    continue

                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(code)

                # Очищаем кэш импорта
                importlib.invalidate_caches()

                # Проверка зависимостей
                requires_match = re.search(r"^\s*#\s*requires:\s*(.+)$", code, re.MULTILINE | re.IGNORECASE)
                deps = []
                if requires_match:
                    deps = [d.strip() for d in re.split(r"[\s,]+", requires_match.group(1)) if d.strip()]
                if not deps and mod.get("requires"):
                    deps = mod["requires"]
                deps = [d for d in deps if d and d != mod["module_name"] and d != alias]

                allow_break = bool(get_config("gh_installer", "allow_break_system_packages", False))
                if deps:
                    await update_status(
                        f"⏳ **Обновление модулей** {progress_bar}\n\n"
                        f"⚙️ **Текущий:** `{mod_name}` (`{alias}`)\n"
                        f"📊 **Обновлено:** `{len(upgraded)}` | **Осталось:** `{remaining + 1}`\n\n"
                        f"📦 `Установка зависимостей: {', '.join(deps)}...`"
                    )
                    for dep in deps:
                        await pip_install(dep, break_system_packages=allow_break)

                # Перезагрузка модуля
                module_name = mod["module_name"]
                try:
                    importlib.invalidate_caches()
                    if module_name in sys.modules:
                        importlib.reload(sys.modules[module_name])
                    else:
                        importlib.import_module(module_name)
                    upgraded.append(f"{mod_name} (`{alias}`) *({source_used})*")
                except ModuleNotFoundError as err_mod:
                    if err_mod.name and err_mod.name != module_name and err_mod.name != alias:
                        await pip_install(err_mod.name, break_system_packages=allow_break)
                        importlib.invalidate_caches()
                        if module_name in sys.modules:
                            importlib.reload(sys.modules[module_name])
                        else:
                            importlib.import_module(module_name)
                        upgraded.append(f"{mod_name} (`{alias}`) *({source_used})*")
                    else:
                        errors_list.append(f"{mod_name} ({alias}): {err_mod}")

            except Exception as e:
                logger.error(f"Ошибка обновления {alias}: {e}")
                errors_list.append(f"{mod_name} ({alias}): {type(e).__name__}")

            await asyncio.sleep(0.3)

        if upgraded:
            snoozed = get_config("gh_installer", "snoozed_hashes", {})
            if isinstance(snoozed, dict):
                for alias in targets:
                    snoozed.pop(alias, None)
                    _last_notified_module_hashes.pop(alias, None)
                set_config("gh_installer", "snoozed_hashes", snoozed)

            await update_status("⏳ `Подготовка к перезапуску юзербота...`")
            msg = (
                f"🎉 **Обновление модулей успешно завершено!**\n\n"
                f"🔝 **Обновлено ({len(upgraded)}/{total_targets}):**\n"
                + "\n".join([f"• 🟢 {item}" for item in upgraded])
            )
            if errors_list:
                msg += f"\n\n⚠️ **Ошибки ({len(errors_list)}):**\n" + "\n".join([f"• 🔴 {e}" for e in errors_list])
            msg += "\n\n🔄 *Юзербот перезапущен для применения изменений.*"

            target_client = client or get_main_client()
            await restart_userbot(target_client, chat_id, message_id, custom_text=msg, event=event)
        else:
            msg = "🤷‍♂️ **Не удалось обновить выбранные модули.**\n"
            if errors_list:
                msg += f"\n⚠️ **Ошибки:**\n" + "\n".join([f"• 🔴 {e}" for e in errors_list])
            await update_status(msg)

    except Exception as e:
        logger.error(f"Сбой в perform_bulk_upgrade: {e}\n{traceback.format_exc()}")
        await update_status(f"❌ **Сбой при обновлении модулей:** `{e}`")


# ==========================================
# КОМАНДЫ ЮЗЕРБОТА
# ==========================================

@register_cmd("ghsearch", desc="Интерактивный поиск модуля в репозитории (с фото и кнопками). Юзай: .ghsearch <запрос>")
@register_cmd("ghs", desc="Алиас для .ghsearch")
@register_cmd("repo", desc="Алиас для .ghsearch all")
async def search_module_cmd(client, event, args):
    """
    Интерактивный поиск модулей в репозитории с показом баннеров, описания,
    кнопкой установки/обновления, пагинацией карточек и списком.
    """
    _clean_old_sessions()
    query = args.strip().lower() if args else "all"

    await event.edit(f"🔍 `Ищу '{query}' в репозитории...`")
    repo_index, err = await fetch_repo_index()

    if repo_index is None:
        return await event.edit(f"❌ **Ошибка доступа к репозиторию!**\n`{err}`")

    if query in ("all", "*", ""):
        results = list(repo_index.keys())
    else:
        results = []
        for alias, raw_val in repo_index.items():
            mod = normalize_module_info(alias, raw_val)
            alias_match = query in alias.lower()
            name_match = query in mod["name"].lower()
            desc_match = query in mod["desc"].lower() if mod.get("desc") else False
            cmd_match = any(query in c.lower() for c in mod.get("commands", []))
            if alias_match or name_match or desc_match or cmd_match:
                results.append(alias)

    if not results:
        return await event.edit(
            f"🤷‍♂️ По запросу `{query}` ничего не найдено в репозитории.\n"
            f"💡 Введи `.ghsearch all` чтобы посмотреть все доступные модули."
        )

    # Проверяем статусы обновлений для найденных установленных модулей
    updates_map = {}
    for alias in results:
        raw_val = repo_index.get(alias)
        if raw_val:
            mod = normalize_module_info(alias, raw_val)
            if is_module_installed(mod["file_name"]):
                _, has_upd = await check_single_module_update(mod)
                updates_map[alias] = has_upd

    session_id = f"ghs_{int(time.time() * 1000)}"
    _search_sessions[session_id] = {
        "query": query,
        "items": results,
        "repo_index": repo_index,
        "updates_map": updates_map,
        "current_idx": 0,
        "chat_id": event.chat_id,
        "created_at": time.time()
    }

    text, buttons = build_card_view(session_id, 0)

    try:
        await send_inline(
            client,
            event.chat_id,
            text,
            buttons=buttons,
            reply_to=event.reply_to_msg_id
        )
        await event.delete()
    except Exception as inline_err:
        logger.warning(f"send_inline fallback: {inline_err}")
        # Fallback на прямое редактирование текста, если инлайн-бот временно недоступен
        await event.edit(text, link_preview=True)


@register_cmd("ghinstall", desc="Установить модуль из репозитория. Флаги: [-b] [--proxy / --no-proxy]")
@register_cmd("ghi", desc="Алиас для .ghinstall")
async def install_module_cmd(client, event, args):
    """Прямая установка модуля по алиасу."""
    if not args:
        return await event.edit("❌ Укажи имя пакета из репозитория: `.ghinstall spam`\n💡 Список пакетов: `.ghsearch all`")

    parts = args.split()
    allow_break = False
    proxy_choice = None
    clean_parts = []
    for p in parts:
        pl = p.lower()
        if pl in ("--break-system-packages", "--break", "-b"):
            allow_break = True
        elif pl in ("--proxy", "-p"):
            proxy_choice = True
        elif pl in ("--no-proxy", "--noproxy", "-np"):
            proxy_choice = False
        else:
            clean_parts.append(p)

    if not clean_parts:
        return await event.edit("❌ Укажи имя пакета из репозитория: `.ghinstall spam`")

    package_alias = clean_parts[0].strip().lower()
    await perform_module_install(
        client,
        event.chat_id,
        event.id,
        package_alias,
        event=event,
        allow_break_system_packages=allow_break,
        proxy_choice=proxy_choice
    )


@register_cmd("confirm", desc="Подтвердить установку модуля с флагом --break-system-packages")
@register_cmd("yes", desc="Алиас для .confirm")
async def confirm_install_cmd(client, event, args):
    """Подтверждение установки модуля при ошибке PEP 668."""
    chat_key = str(event.chat_id)
    item = _pending_bsp_confirmations.pop(chat_key, None)
    if not item:
        now = time.time()
        for ck, val in list(_pending_bsp_confirmations.items()):
            if now - val["time"] < 300:
                item = val
                _pending_bsp_confirmations.pop(ck, None)
                break

    if not item or (time.time() - item["time"] > 300):
        return await event.edit("⚠️ Нет ожидающих подтверждения запросов на установку.")

    alias = item["alias"]
    await event.edit(f"🚀 `Подтверждено! Устанавливаю '{alias}' с --break-system-packages...`")
    await perform_module_install(
        client,
        event.chat_id,
        event.id,
        alias,
        event=event,
        allow_break_system_packages=True
    )


@register_cmd("cancel", desc="Отменить ожидающую установку модуля")
async def cancel_install_cmd(client, event, args):
    """Отмена ожидающей установки модуля."""
    chat_key = str(event.chat_id)
    item = _pending_bsp_confirmations.pop(chat_key, None) or _pending_proxy_confirmations.pop(chat_key, None)
    if not item:
        now = time.time()
        for pending_dict in (_pending_bsp_confirmations, _pending_proxy_confirmations):
            for ck, val in list(pending_dict.items()):
                if now - val["time"] < 300:
                    item = val
                    pending_dict.pop(ck, None)
                    break
            if item:
                break

    if item:
        alias = item["alias"]
        try:
            modules_dir = get_modules_dir()
            for ext in [".py"]:
                fpath = os.path.join(modules_dir, f"{alias}{ext}")
                if os.path.exists(fpath) and alias not in sys.modules:
                    os.remove(fpath)
        except Exception:
            pass
        return await event.edit(f"❌ **Установка модуля `{alias}` отменена.**")

    await event.edit("⚠️ Нет ожидающих действий для отмены.")


@register_cmd("ghcheck", desc="Проверить наличие обновлений для установленных модулей")
@register_cmd("checkmods", desc="Алиас для .ghcheck")
@register_cmd("ghupdates", desc="Алиас для .ghcheck")
async def check_modules_cmd(client, event, args):
    """
    Проверяет наличие свежих версий установленных модулей в репозитории без их перезаписи.
    """
    await event.edit("🔍 `Проверяю наличие обновлений для установленных модулей...`")
    repo_index, err = await fetch_repo_index(force=True)
    if repo_index is None:
        return await event.edit(f"❌ **Ошибка доступа к репозиторию:**\n`{err}`")

    installed_modules = []
    for alias, raw in repo_index.items():
        mod = normalize_module_info(alias, raw)
        if is_module_installed(mod["file_name"]):
            installed_modules.append((alias, mod))

    if not installed_modules:
        return await event.edit(
            "🤷‍♂️ **Ни один модуль из репозитория не установлен.**\n"
            "💡 Используйте `.ghsearch all` для просмотра доступных модулей."
        )

    outdated = []
    up_to_date = []

    for alias, mod in installed_modules:
        _, has_upd = await check_single_module_update(mod)
        if has_upd:
            outdated.append((alias, mod))
        else:
            up_to_date.append((alias, mod))

    total = len(installed_modules)
    buttons = []

    if outdated:
        msg = f"🔔 **Доступны обновления модулей (`{len(outdated)}/{total}`):**\n\n"
        for alias, mod in outdated:
            msg += f"🆙 **{mod['name']}** (`{alias}`) — *Доступна новая версия*\n"
        
        if up_to_date:
            msg += f"\n🟢 **Актуальные модули (`{len(up_to_date)}`):**\n"
            for alias, mod in up_to_date:
                msg += f"• **{mod['name']}** (`{alias}`)\n"

        msg += "\n💡 *Нажмите кнопку ниже для обновления или используйте `.upgrade`*"

        btn_rows = []
        btn_rows.append([Button.inline(f"🚀 Обновить все доступные ({len(outdated)})", b"gh_upd_all")])

        # Индивидуальные кнопки для обновления (по 2 в ряд)
        cur_row = []
        for alias, mod in outdated[:6]:
            cur_row.append(Button.inline(f"🆙 {mod['name'][:12]}", f"gh_upd:{alias}".encode()))
            if len(cur_row) == 2:
                btn_rows.append(cur_row)
                cur_row = []
        if cur_row:
            btn_rows.append(cur_row)

        btn_rows.append([
            Button.inline("🔄 Проверить снова", b"gh_check_recheck"),
            Button.inline("❌ Закрыть", f"gh_close:check".encode())
        ])
        buttons = btn_rows
    else:
        msg = f"✅ **Все установленные модули актуальны! (`{total}`):**\n\n"
        for alias, mod in up_to_date:
            msg += f"• 🟢 **{mod['name']}** (`{alias}`)\n"
        msg += "\n💡 *Все версии совпадают с репозиторием. Обновления не требуются.*"

        buttons = [
            [
                Button.inline("🔄 Проверить снова", b"gh_check_recheck"),
                Button.inline("❌ Закрыть", f"gh_close:check".encode())
            ]
        ]

    try:
        await send_inline(
            client,
            event.chat_id,
            msg,
            buttons=buttons,
            reply_to=getattr(event, "reply_to_msg_id", None)
        )
        await event.delete()
    except Exception as inline_err:
        logger.warning(f"check_modules send_inline fallback: {inline_err}")
        await event.edit(msg)


@register_cmd("upgrade", desc="Обновить установленные модули (.upgrade / .upgrade <имя> / .upgrade force)")
@register_cmd("ghupgrade", desc="Алиас для .upgrade")
@register_cmd("upgrademods", desc="Алиас для .upgrade")
async def upgrade_cmd(client, event, args):
    """
    Обновляет установленные модули с живым счётчиком прогресса:
    - Без аргументов: обновляет только те модули, у которых есть новая версия
    - .upgrade <имя>: обновляет конкретный модуль
    - .upgrade force / all: принудительно переустанавливает все установленные модули
    """
    raw_arg = args.strip() if args else ""
    force = False
    target = None

    if raw_arg in ("force", "-f", "all", "*"):
        force = True
    elif raw_arg:
        target = raw_arg.lower()
        if target.endswith(".py"):
            target = target[:-3]

    await perform_bulk_upgrade(client, event.chat_id, event.id, force_all=force, target_alias=target, event=event)


@register_cmd("updaterepo", desc="Принудительно обновить список модулей из репозитория Gitea")
async def update_repo_cmd(client, event, args):
    """Принудительно обновляет локальный кэш index.json."""
    await event.edit("🔄 `Скачиваю свежий индекс репозитория...`")
    repo_index, err = await fetch_repo_index(force=True)
    
    if repo_index is None:
        return await event.edit(f"❌ **Не удалось обновить репозиторий!**\n`{err}`")
        
    count = len(repo_index)
    msg = f"✅ **Репозиторий успешно обновлен!**\n📦 Найдено пакетов: `{count}`"
    if err:
        msg += f"\n⚠️ *Заметка:* `{err}`"
        
    await event.edit(msg)


@register_cmd("fixreq", desc="Проверить и доустановить зависимости pip для всех локальных модулей")
async def fixreq_cmd(client, event, args):
    """Сканирует все .py файлы в modules/ и доустанавливает pip-зависимости."""
    await event.edit("🔍 `Сканирую локальные модули на наличие зависимостей...`")
    modules_dir = get_modules_dir()
    
    if not os.path.exists(modules_dir):
        return await event.edit("❌ Папка с модулями не найдена.")
        
    all_deps = set()
    for file_name in os.listdir(modules_dir):
        if not file_name.endswith(".py"):
            continue
            
        file_path = os.path.join(modules_dir, file_name)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                code = f.read()
                
            requires_match = re.search(r"^\s*#\s*requires:\s*(.+)$", code, re.MULTILINE | re.IGNORECASE)
            if requires_match:
                deps = [d.strip() for d in re.split(r"[\s,]+", requires_match.group(1)) if d.strip()]
                all_deps.update(deps)
        except Exception:
            pass
            
    if not all_deps:
        return await event.edit("✅ Зависимости в модулях не найдены. Все чисто!")
        
    await event.edit(f"📦 **Найдено уникальных зависимостей ({len(all_deps)}):**\n`{', '.join(all_deps)}`\n\n⏳ `Проверяю и устанавливаю...`")
    
    success_deps = []
    error_deps = []
    
    for dep in all_deps:
        success, pip_err = await pip_install(dep)
        if success:
            success_deps.append(dep)
        else:
            error_deps.append(dep)
            logger.warning(f"Ошибка установки {dep}: {pip_err}")
            
    msg = "✅ **Проверка и установка зависимостей завершена!**\n\n"
    if success_deps:
        msg += f"🟢 **Установлено/Готово ({len(success_deps)}):** `{', '.join(success_deps)}`\n"
    if error_deps:
        msg += f"🔴 **С ошибками ({len(error_deps)}):** `{', '.join(error_deps)}`\n"
        
    await event.edit(msg)


@register_cmd("ghuninstall", desc="Удалить модуль. Юзай: .ghuninstall <имя_модуля>")
@register_cmd("ghrm", desc="Алиас для .ghuninstall")
async def uninstall_module_cmd(client, event, args):
    """Удаляет указанный модуль из папки modules/."""
    if not args:
        return await event.edit("❌ Укажи имя модуля для удаления: `.ghuninstall имя_модуля`")
        
    module_name = args.strip()
    if module_name.endswith(".py"):
        module_name = module_name[:-3]

    mod_info = modules_repo["modules"].get(module_name)
    if mod_info and mod_info.get("system", False):
        return await event.edit(f"🔒 Модуль `{mod_info['name']}` (`{module_name}`) системный и не может быть удален!")

    modules_dir = get_modules_dir()
    file_path = os.path.join(modules_dir, f"{module_name}.py")
    
    if not os.path.exists(file_path):
        return await event.edit(f"❌ Модуль `{module_name}` не найден в папке модулей.")
        
    try:
        os.remove(file_path)
        if module_name in sys.modules:
            del sys.modules[module_name]
        await event.edit(f"🗑 Удаляю модуль `{module_name}` и перезагружаю юзербота...")
        success_text = f"🗑 **Модуль `{module_name}` успешно удален!**"
        await restart_userbot(client, event.chat_id, event.id, custom_text=success_text)
    except Exception as e:
        await event.edit(f"❌ Ошибка при удалении: {e}")


@register_cmd("ghsendmod", desc="Отправить исходный код модуля в чат. Юзай: .ghsendmod <имя_модуля>")
async def send_module_cmd(client, event, args):
    """Отправляет файл модуля в чат как документ."""
    if not args:
        return await event.edit("❌ Укажи имя модуля для отправки: `.ghsendmod имя_модуля`")
        
    module_name = args.strip()
    if module_name.endswith(".py"):
        module_name = module_name[:-3]
        
    modules_dir = get_modules_dir()
    file_path = os.path.join(modules_dir, f"{module_name}.py")
    
    if not os.path.exists(file_path):
        return await event.edit(f"❌ Модуль `{module_name}` не найден.")
        
    try:
        await event.edit(f"📤 Отправляю модуль `{module_name}`...")
        await client.send_file(event.chat_id, file_path, caption=f"📦 Исходный код модуля: **{module_name}**")
        await event.delete()
    except Exception as e:
        await event.edit(f"❌ Ошибка при отправке: {e}")