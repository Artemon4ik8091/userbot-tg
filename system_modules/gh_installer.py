import os
import sys
import re
import asyncio
import importlib
import traceback
import json
import aiohttp
import time
from telethon import Button, errors
from registry import (
    register_cmd,
    register_callback,
    set_module_meta,
    modules_repo,
    restart_userbot,
    get_logger,
    send_inline,
    get_owner_id,
    get_bot,
    get_main_client
)

logger = get_logger("GHInstaller")

# Метаданные системного модуля
set_module_meta(
    name="Package Manager",
    desc="Установка модулей из репозитория GitHub, поиск с фото и кнопками, обновление и удаление.",
    system=True
)

# 🔗 Ссылка на индекс репозитория
INDEX_URL = "https://raw.githubusercontent.com/Artemon4ik8091/ubtg-repo/refs/heads/main/index.json"

# --- НАСТРОЙКИ КЭША И СЕССИЙ ---
CACHE_TIMEOUT = 3600  # 1 час кэша
_repo_cache = None
_last_repo_update = 0
_search_sessions = {}


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


async def pip_install(package_name):
    """Асинхронная установка пакетов через pip."""
    logger.info(f"Запуск установки пакета pip: {package_name}")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pip", "install", package_name,
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
    Скачивает и парсит index.json строго из репозитория с кэшированием.
    """
    global _repo_cache, _last_repo_update
    
    current_time = time.time()
    if not force and _repo_cache is not None:
        if current_time - _last_repo_update < CACHE_TIMEOUT:
            return _repo_cache, ""
            
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(INDEX_URL, timeout=10) as resp:
                if resp.status == 200:
                    text_data = await resp.text()
                    _repo_cache = json.loads(text_data)
                    _last_repo_update = current_time
                    return _repo_cache, ""
                else:
                    if _repo_cache is not None:
                        return _repo_cache, f"GitHub вернул статус {resp.status} (использован кэш)"
    except Exception as e:
        logger.warning(f"Ошибка загрузки индекса из сети: {e}")

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

    if _repo_cache is not None:
        return _repo_cache, "Использован сохраненный кэш репозитория"

    return None, "Не удалось получить index.json из репозитория"


def normalize_module_info(alias, raw_val, base_url=None):
    """
    Нормализует данные о модуле строго из полученного index.json.
    Если каких-то параметров нет в индексе, они остаются пустыми / дефолтными.
    """
    base_url = base_url or (INDEX_URL.rsplit('/', 1)[0] + '/')
    
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

    file_name = file_url.split('/')[-1]
    module_name = file_name[:-3] if file_name.endswith(".py") else file_name

    return {
        "alias": alias,
        "name": name,
        "desc": desc,
        "image": image,
        "path": path,
        "file_url": file_url,
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
    Формирует карточку модуля. Если картинка отсутствует в индексе, она не отправляется.
    Если описание отсутствует, выводится 'Описания не найдено'.
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
    status_str = "🟢 **Установлен**" if installed else "⚪️ **Не установлен**"

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

    # Ряд 2: Кнопка установки и переход к общему списку
    action_btn_text = "🔄 Переустановить" if installed else "📥 Установить"
    action_btn = Button.inline(action_btn_text, f"gh_inst:{mod['alias']}".encode())
    
    if total > 1:
        buttons.append([
            action_btn,
            Button.inline(f"📋 Списком ({total})", f"gh_list:{session_id}".encode())
        ])
    else:
        buttons.append([action_btn])

    # Ряд 3: Исходный код на GitHub и закрытие
    row3 = []
    if mod.get("file_url"):
        row3.append(Button.url("🔗 Исходник", mod["file_url"]))
    row3.append(Button.inline("❌ Закрыть", f"gh_close:{session_id}".encode()))
    buttons.append(row3)

    return text, buttons


def build_list_view(session_id):
    """
    Формирует текстовый список всех найденных модулей с быстрыми кнопками.
    """
    session = _search_sessions.get(session_id)
    if not session:
        return "⚠️ Сессия поиска устарела. Введите `.ghsearch`", None

    items = session.get("items", [])
    total = len(items)
    repo_index = session.get("repo_index", {})
    query_str = session.get("query", "")
    header = f"📋 **Найдено модулей ({total}) по запросу '{query_str}':**\n\n" if query_str != "all" else f"📋 **Все модули репозитория ({total}):**\n\n"

    text = header
    for idx, alias in enumerate(items, 1):
        mod = normalize_module_info(alias, repo_index.get(alias, {}))
        installed = is_module_installed(mod["file_name"])
        badge = "🟢" if installed else "⚪️"
        raw_desc = mod.get("desc") or "Описания не найдено"
        desc_cut = raw_desc[:55] + "..." if len(raw_desc) > 55 else raw_desc
        text += f"**{idx}.** {badge} **{mod['name']}** (`{alias}`)\n"
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


async def perform_module_install(client, chat_id, message_id, package_alias, event=None):
    """
    Единая логика скачивания, установки зависимостей, импорта и перезапуска модуля.
    """
    logger.info(f"Начало установки модуля: {package_alias}")

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
            avail = ", ".join([f"`{k}`" for k in repo_index.keys()])
            return await update_status(f"❌ Пакет `{package_alias}` не найден в репозитории.\n📦 **Доступные модули:**\n{avail}")

        mod = normalize_module_info(package_alias, repo_index[package_alias])
        file_url = mod["file_url"]
        file_name = mod["file_name"]
        module_name = mod["module_name"]

        await update_status(f"⏳ `Скачиваю {file_name}...`")

        async with aiohttp.ClientSession() as session:
            async with session.get(file_url, timeout=20) as resp:
                if resp.status != 200:
                    return await update_status(f"❌ Ошибка скачивания `{file_name}`! HTTP статус: {resp.status}")
                code = await resp.text()

        modules_dir = get_modules_dir()
        if modules_dir not in sys.path:
            sys.path.insert(0, modules_dir)

        file_path = os.path.join(modules_dir, file_name)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code)

        importlib.invalidate_caches()

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
                success, pip_err = await pip_install(dep)
                if not success:
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
                success, pip_err = await pip_install(missing_pkg)
                if not success:
                    tr_err = pip_err[-300:] if len(pip_err) > 300 else pip_err
                    raise Exception(f"Не удалось установить `{missing_pkg}`.\nОшибка pip:\n`...{tr_err}`")

                await update_status(f"✅ Пакет `{missing_pkg}` установлен! Пробую запустить модуль...")
                await asyncio.sleep(1)

        if imported_successfully:
            success_text = f"✅ **Пакет `{mod['name']}` (`{package_alias}`) успешно установлен!**\n🔄 *Перезапускаю юзербота для применения изменений...*"
            await update_status(success_text)
            await asyncio.sleep(0.3)
            logger.info(f"Пакет {package_alias} ({module_name}) успешно установлен. Перезапуск...")
            final_text = f"✅ **Пакет `{mod['name']}` (`{package_alias}`) успешно установлен и готов к работе!**"
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
    """Закрытие поискового меню."""
    session_id = data[len("gh_close:"):]
    _search_sessions.pop(session_id, None)
    await event.answer("Закрыто")
    try:
        await event.delete()
    except Exception:
        try:
            await event.edit("❌ **Поиск закрыт.**", buttons=None)
        except Exception:
            pass


@register_callback("gh_inst:")
async def cb_gh_install(event, data):
    """Кнопка 'Установить' / 'Переустановить' из карточки."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Установка модулей доступна только владельцу!", alert=True)

    alias = data[len("gh_inst:"):]
    await event.answer(f"🚀 Запуск установки '{alias}'...")
    msg_id = getattr(event, "message_id", None) or getattr(event, "id", 0)
    await perform_module_install(get_main_client(), event.chat_id, msg_id, alias, event=event)


# ==========================================
# КОМАНДЫ ЮЗЕРБОТА
# ==========================================

@register_cmd("ghsearch", desc="Интерактивный поиск модуля в репозитории (с фото и кнопками). Юзай: .ghsearch <запрос>")
@register_cmd("ghs", desc="Алиас для .ghsearch")
@register_cmd("repo", desc="Алиас для .ghsearch all")
async def search_module_cmd(client, event, args):
    """
    Интерактивный поиск модулей в репозитории с показом баннеров, описания,
    кнопкой установки, пагинацией карточек и списком.
    """
    _clean_old_sessions()
    query = args.strip().lower() if args else "all"

    await event.edit(f"🔍 `Ищу '{query}' в репозитории GitHub...`")
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

    session_id = f"ghs_{int(time.time() * 1000)}"
    _search_sessions[session_id] = {
        "query": query,
        "items": results,
        "repo_index": repo_index,
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


@register_cmd("ghinstall", desc="Установить модуль из репозитория. Юзай: .ghinstall <имя>")
@register_cmd("ghi", desc="Алиас для .ghinstall")
async def install_module_cmd(client, event, args):
    """Прямая установка модуля по алиасу."""
    if not args:
        return await event.edit("❌ Укажи имя пакета из репозитория: `.ghinstall spam`\n💡 Список пакетов: `.ghsearch all`")

    package_alias = args.strip().lower()
    await perform_module_install(client, event.chat_id, event.id, package_alias, event=event)


@register_cmd("updaterepo", desc="Принудительно обновить список модулей из репозитория GitHub")
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


@register_cmd("upgrade", desc="Обновить индекс и все установленные модули из репозитория")
async def upgrade_cmd(client, event, args):
    """Обновляет все локально установленные модули до последних версий из репозитория."""
    await event.edit("🔄 `Обновляю индекс репозитория...`")
    repo_index, err = await fetch_repo_index(force=True)
    
    if repo_index is None:
        return await event.edit(f"❌ **Ошибка обновления репозитория:**\n`{err}`")
        
    modules_dir = get_modules_dir()
    if modules_dir not in sys.path:
        sys.path.insert(0, modules_dir)
    upgraded = []
    errors_list = []
    
    await event.edit("⏳ `Проверяю обновления для локальных модулей...`")
    
    for alias, raw_val in repo_index.items():
        mod = normalize_module_info(alias, raw_val)
        file_name = mod["file_name"]
        file_url = mod["file_url"]
        file_path = os.path.join(modules_dir, file_name)
        
        if os.path.exists(file_path):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(file_url, timeout=20) as resp:
                        if resp.status == 200:
                            code = await resp.text()
                            with open(file_path, "w", encoding="utf-8") as f:
                                f.write(code)
                            
                            importlib.invalidate_caches()
                            module_name = mod["module_name"]
                            try:
                                if module_name in sys.modules:
                                    importlib.reload(sys.modules[module_name])
                                else:
                                    importlib.import_module(module_name)
                                upgraded.append(alias)
                            except ModuleNotFoundError as err_mod:
                                if err_mod.name and err_mod.name != module_name and err_mod.name != alias:
                                    await pip_install(err_mod.name)
                                    importlib.invalidate_caches()
                                    if module_name in sys.modules:
                                        importlib.reload(sys.modules[module_name])
                                    else:
                                        importlib.import_module(module_name)
                                    upgraded.append(alias)
                                else:
                                    errors_list.append(f"{alias} ({err_mod})")
                        else:
                            errors_list.append(f"{alias} (HTTP {resp.status})")
            except Exception as e:
                errors_list.append(f"{alias} ({type(e).__name__})")
    
    if upgraded:
        await event.edit("⏳ `Подготовка и применение обновленных модулей...`")
        msg = f"✅ **Обновление модулей завершено!**\n\n"
        msg += f"🔝 **Обновлено ({len(upgraded)}):** `{', '.join(upgraded)}`\n"
        if errors_list:
            msg += f"⚠️ **Ошибки ({len(errors_list)}):** `{', '.join(errors_list)}`\n"
        msg += "\n*(Совет: используй `.fixreq`, если после обновления модули требуют новых библиотек)*"
        
        await restart_userbot(client, event.chat_id, event.id, custom_text=msg)
        return

    msg = "🤷‍♂️ **Нет модулей для обновления.** (Ни один модуль из репозитория не установлен)\n"
    if errors_list:
        msg += f"⚠️ **Ошибки ({len(errors_list)}):** `{', '.join(errors_list)}`\n"
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