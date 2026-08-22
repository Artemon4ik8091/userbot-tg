import os
import sys
import re
import asyncio
import importlib
import traceback
import json
import aiohttp
import time
from registry import register_cmd, set_module_meta, modules_repo, restart_userbot, get_logger

logger = get_logger("GHInstaller")

# Обновляем метаданные модуля (он системный, удалять нельзя)
set_module_meta(
    name="Package Manager",
    desc="Установка модулей из репозитория GitHub, удаление и отправка.",
    system=True
)

# 🔗 ССЫЛКА НА ТВОЙ ИНДЕКС
INDEX_URL = "https://raw.githubusercontent.com/Artemon4ik8091/ubtg-repo/refs/heads/main/index.json"

# --- НАСТРОЙКИ КЭША ---
CACHE_TIMEOUT = 3600  # Время жизни кэша в секундах (1 час = 3600 сек)
_repo_cache = None
_last_repo_update = 0

def get_modules_dir():
    # Ищем папку modules на уровень выше от системных модулей
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), 'modules')

async def pip_install(package_name):
    """Асинхронная установка пакетов через pip"""
    logger.info(f"Запуск установки пакета: {package_name}")
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
    """Скачивает и парсит index.json из репозитория с кэшированием"""
    global _repo_cache, _last_repo_update
    
    current_time = time.time()
    
    # Возвращаем кэш, если не требуется принудительное обновление и время жизни кэша не истекло
    if not force and _repo_cache is not None:
        if current_time - _last_repo_update < CACHE_TIMEOUT:
            return _repo_cache, ""
            
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(INDEX_URL) as resp:
                if resp.status != 200:
                    # Если GitHub недоступен, но есть старый кэш - отдаем его с предупреждением
                    if _repo_cache is not None:
                        return _repo_cache, f"GitHub вернул статус {resp.status} (использован старый кэш)"
                    return None, f"GitHub вернул статус {resp.status}"
                
                # Читаем как текст, затем парсим
                text_data = await resp.text()
                _repo_cache = json.loads(text_data)
                _last_repo_update = current_time
                return _repo_cache, ""
    except Exception as e:
        if _repo_cache is not None:
            return _repo_cache, f"Ошибка сети: {str(e)} (использован старый кэш)"
        return None, str(e)

@register_cmd("updaterepo", desc="Принудительно обновить список модулей из репозитория GitHub")
async def update_repo_cmd(client, event, args):
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
    await event.edit("🔄 `Обновляю индекс репозитория...`")
    repo_index, err = await fetch_repo_index(force=True)
    
    if repo_index is None:
        return await event.edit(f"❌ **Ошибка обновления репозитория:**\n`{err}`")
        
    modules_dir = get_modules_dir()
    upgraded = []
    errors = []
    
    await event.edit("⏳ `Проверяю обновления для локальных модулей...`")
    
    for alias, path_or_url in repo_index.items():
        if path_or_url.startswith("http"):
            file_url = path_or_url
        else:
            base_url = INDEX_URL.rsplit('/', 1)[0] + '/'
            file_url = base_url + path_or_url
            
        file_name = file_url.split('/')[-1]
        file_path = os.path.join(modules_dir, file_name)
        
        # Обновляем только те модули, которые уже установлены локально
        if os.path.exists(file_path):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(file_url) as resp:
                        if resp.status == 200:
                            code = await resp.text()
                            with open(file_path, "w", encoding="utf-8") as f:
                                f.write(code)
                            
                            module_name = file_name[:-3]
                            try:
                                # Пытаемся перезагрузить обновленный модуль
                                if module_name in sys.modules:
                                    importlib.reload(sys.modules[module_name])
                                else:
                                    importlib.import_module(module_name)
                                upgraded.append(alias)
                            except ModuleNotFoundError as err:
                                # На случай, если в обнове добавили новую библиотеку
                                if err.name:
                                    await pip_install(err.name)
                                    if module_name in sys.modules:
                                        importlib.reload(sys.modules[module_name])
                                    else:
                                        importlib.import_module(module_name)
                                    upgraded.append(alias)
                        else:
                            errors.append(f"{alias} (HTTP {resp.status})")
            except Exception as e:
                errors.append(f"{alias} ({type(e).__name__})")
    
    if upgraded:
        await event.edit("⏳ `Подготовка и применение обновленных модулей...`")
        msg = f"✅ **Обновление модулей завершено!**\n\n"
        msg += f"🔝 **Обновлено ({len(upgraded)}):** `{', '.join(upgraded)}`\n"
        if errors:
            msg += f"⚠️ **Ошибки ({len(errors)}):** `{', '.join(errors)}`\n"
        msg += "\n*(Совет: используй `.fixreq`, если после обновления модули выдают ошибки)*"
        
        await restart_userbot(client, event.chat_id, event.id, custom_text=msg)
        return

    msg = "🤷‍♂️ **Нет модулей для обновления.** (Ни один модуль из репозитория не установлен)\n"
    if errors:
        msg += f"⚠️ **Ошибки ({len(errors)}):** `{', '.join(errors)}`\n"
    await event.edit(msg)

@register_cmd("fixreq", desc="Проверить и доустановить зависимости pip для всех локальных модулей")
async def fixreq_cmd(client, event, args):
    await event.edit("🔍 `Сканирую локальные модули на наличие зависимостей...`")
    modules_dir = get_modules_dir()
    
    if not os.path.exists(modules_dir):
        return await event.edit("❌ Папка с модулями не найдена.")
        
    all_deps = set()
    
    # Считываем все зависимости (requires: ...) из всех .py файлов
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
    
    # Прогоняем зависимости через pip (уже установленные pip быстро пропустит)
    for dep in all_deps:
        success, pip_err = await pip_install(dep)
        if success:
            success_deps.append(dep)
        else:
            error_deps.append(dep)
            print(f"[FixReq] Ошибка установки {dep}: {pip_err}")
            
    msg = "✅ **Проверка и установка зависимостей завершена!**\n\n"
    if success_deps:
        msg += f"🟢 **Установлено/Готово ({len(success_deps)}):** `{', '.join(success_deps)}`\n"
    if error_deps:
        msg += f"🔴 **С ошибками ({len(error_deps)}):** `{', '.join(error_deps)}`\n"
        
    await event.edit(msg)

@register_cmd("ghsearch", desc="Поиск модуля в репозитории по названию")
async def search_module_cmd(client, event, args):
    if not args:
        return await event.edit("❌ Укажи запрос для поиска: `.ghsearch <название>`\n*(Или введи `.ghsearch all`, чтобы увидеть весь список)*")
    
    query = args.strip().lower()
    await event.edit(f"🔍 `Ищу '{query}' в репозитории...`")
    
    repo_index, err = await fetch_repo_index()
    if repo_index is None:
        return await event.edit(f"❌ **Ошибка доступа к репозиторию!**\n`{err}`")
        
    if query == "all":
        results = list(repo_index.keys())
    else:
        results = [name for name in repo_index.keys() if query in name.lower()]
        
    if not results:
        return await event.edit(f"🤷‍♂️ По запросу `{query}` ничего не найдено в репозитории.")
        
    msg = f"🔎 **Результаты поиска ({len(results)}):**\n\n"
    msg += ", ".join([f"`{name}`" for name in results])
    msg += "\n\n*(Установить: `.ghinstall <имя>`)*"
    
    await event.edit(msg)

@register_cmd("ghinstall", desc="Установить модуль из репозитория. Использование: .ghinstall <имя_пакета>")
async def install_module(client, event, args):
    if not args:
        return await event.edit("❌ Укажи имя пакета из репозитория: `.ghinstall spam`")

    package_alias = args.strip().lower()
    await event.edit(f"🔍 `Ищу пакет '{package_alias}' в репозитории...`")

    # 1. Получаем индекс
    repo_index, err = await fetch_repo_index()
    if repo_index is None:
        return await event.edit(f"❌ **Ошибка доступа к репозиторию!**\nПроверь ссылку `INDEX_URL` в `installer.py`.\n`{err}`")

    # 2. Ищем алиас в индексе
    if package_alias not in repo_index:
        available_packages = ", ".join(repo_index.keys())
        return await event.edit(
            f"❌ Пакет `{package_alias}` не найден в репозитории.\n"
            f"📦 **Доступные модули:**\n`{available_packages}`"
        )

    path_or_url = repo_index[package_alias]
    
    # 3. Формируем итоговую ссылку на скачивание файла
    if path_or_url.startswith("http"):
        file_url = path_or_url
    else:
        # Если в JSON указан относительный путь, склеиваем его с базовым URL репозитория
        base_url = INDEX_URL.rsplit('/', 1)[0] + '/'
        file_url = base_url + path_or_url

    # Вытаскиваем имя файла из ссылки (например, spam_module.py)
    file_name = file_url.split('/')[-1]
    module_name = file_name[:-3] # убираем .py для импорта

    await event.edit(f"⏳ `Скачиваю {file_name}...`")

    try:
        # 4. Скачиваем сам код модуля
        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as resp:
                if resp.status != 200:
                    return await event.edit(f"❌ Ошибка скачивания файла модуля! Код: {resp.status}")
                code = await resp.text()

        # 5. Сохраняем на диск
        modules_dir = get_modules_dir()
        file_path = os.path.join(modules_dir, file_name)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code)

        # 6. Проверка и установка зависимостей
        requires_match = re.search(r"^\s*#\s*requires:\s*(.+)$", code, re.MULTILINE | re.IGNORECASE)
        if requires_match:
            deps = [d.strip() for d in re.split(r"[\s,]+", requires_match.group(1)) if d.strip()]
            if deps:
                await event.edit(f"📦 Найдена разметка зависимостей! Устанавливаю: `{', '.join(deps)}`...")
                for dep in deps:
                    success, pip_err = await pip_install(dep)
                    if not success:
                        truncated_err = pip_err[-200:] if len(pip_err) > 200 else pip_err
                        await event.edit(
                            f"⚠️ Ошибка pip при установке `{dep}`!\n"
                            f"`...{truncated_err}`\nПродолжаю..."
                        )
                        await asyncio.sleep(3)

        # 7. Динамический импорт/перезагрузка
        max_install_attempts = 5
        imported_successfully = False

        for attempt in range(max_install_attempts):
            try:
                if module_name in sys.modules:
                    importlib.reload(sys.modules[module_name])
                else:
                    importlib.import_module(module_name)
                
                imported_successfully = True
                break
                
            except ModuleNotFoundError as err:
                missing_package = err.name
                if not missing_package:
                    raise err

                await event.edit(f"🔍 Модуль требует либу `{missing_package}`. Устанавливаю её через pip...")
                
                success, pip_err = await pip_install(missing_package)
                if not success:
                    truncated_err = pip_err[-300:] if len(pip_err) > 300 else pip_err
                    raise Exception(f"Не удалось установить пакет `{missing_package}`.\nОшибка pip:\n`...{truncated_err}`")
                
                await event.edit(f"✅ Пакет `{missing_package}` успешно установлен! Пробую запустить...")
                await asyncio.sleep(1)

        if imported_successfully:
            await event.edit(f"⏳ `Подготовка и настройка пакета {package_alias}...`")
            print(f"[GH-Installer] Пакет {package_alias} ({module_name}) установлен. Применение настроек...")
            
            success_text = f"✅ **Пакет `{package_alias}` (`{module_name}`) успешно установлен и готов к работе!**"
            await restart_userbot(client, event.chat_id, event.id, custom_text=success_text)
        else:
            await event.edit(f"❌ Ошибка: Не удалось запустить `{module_name}`.")
            
    except Exception as e:
        tb_str = traceback.format_exc()
        truncated_tb = tb_str[-600:] if len(tb_str) > 600 else tb_str
        await event.edit(f"❌ **Сбой установки!**\n\nℹ️ **Причина:** `{e}`\n\n📋 **Traceback:**\n`...{truncated_tb}`")


@register_cmd("ghuninstall", desc="Удалить модуль. Использование: .uninstall <имя_модуля>")
async def uninstall_module(client, event, args):
    # Логика удаления оставлена без изменений
    if not args:
        return await event.edit("❌ Укажи имя модуля для удаления: `.uninstall имя_модуля`")
        
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

@register_cmd("ghsendmod", desc="Отправить файл модуля в чат. Использование: .sendmod <имя_модуля>")
async def send_module(client, event, args):
    # Логика отправки оставлена без изменений
    if not args:
        return await event.edit("❌ Укажи имя модуля для отправки: `.sendmod имя_модуля`")
        
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