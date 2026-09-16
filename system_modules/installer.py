import os
import sys
import re
import asyncio
import importlib
import traceback
from telethon import Button
from registry import (
    register_cmd,
    register_callback,
    set_module_meta,
    modules_repo,
    restart_userbot,
    get_logger,
    get_owner_id,
    get_prefix,
    get_config,
    set_config,
    init_config,
    module_requests_proxy,
    get_module_proxy_permission,
    set_module_proxy_permission,
    is_module_proxy_enabled,
    get_core_proxy_url
)

logger = get_logger("Installer")

# Обновляем метаданные модуля (он системный, удалять нельзя)
set_module_meta(
    name="modules manager",
    desc="Установка, удаление и отправка модулей с детальным дебагом ошибок.",
    system=True
)

init_config("installer", {
    "allow_break_system_packages": False
})


def is_authorized_user(sender_id):
    """Проверяет права владельца."""
    owner_id = get_owner_id()
    return not owner_id or sender_id == owner_id


def get_modules_dir():
    # Если installer.py лежит в system_modules, то выходим на уровень выше и ищем папочку modules
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mod_dir = os.path.join(base_dir, 'modules')
    if not os.path.exists(mod_dir):
        try:
            os.makedirs(mod_dir, exist_ok=True)
        except Exception:
            pass
    return mod_dir


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
    """
    Асинхронно устанавливает пакет через pip.
    Возвращает кортеж (success: bool, error_msg: str)
    """
    logger.info(f"Запуск установки пакета: {package_name} (break_system_packages={break_system_packages})")
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
    
    # Декодируем логи ошибок от pip
    err_msg = stderr.decode('utf-8', errors='ignore').strip()
    if not err_msg:
        err_msg = stdout.decode('utf-8', errors='ignore').strip()
    return False, err_msg


@register_cmd("install", desc="Установить модуль из файла (с автоустановкой зависимостей и дебагом). Флаги: [-b] [--proxy / --no-proxy]")
async def install_module(client, event, args):
    reply_msg = await event.get_reply_message()
    
    if not reply_msg or not reply_msg.file:
        await event.edit("⚠️ Бро, сделай реплай на сообщение с .py файлом!")
        return

    if not reply_msg.file.name.endswith(".py"):
        await event.edit("❌ Это не .py файл. Я могу устанавливать только Python модули.")
        return

    args_lower = (args or "").lower()
    allow_break = any(flag in args_lower.split() for flag in ("--break-system-packages", "--break", "-b"))
    if not allow_break:
        allow_break = bool(get_config("installer", "allow_break_system_packages", False)) or bool(get_config("gh_installer", "allow_break_system_packages", False))

    proxy_flag = None
    if any(flag in args_lower.split() for flag in ("--proxy", "-p")):
        proxy_flag = True
    elif any(flag in args_lower.split() for flag in ("--no-proxy", "--noproxy", "-np")):
        proxy_flag = False

    module_name = reply_msg.file.name[:-3]
    await event.edit(f"⏳ `Скачиваю {reply_msg.file.name}...`")
    
    try:
        modules_dir = get_modules_dir()
        if modules_dir not in sys.path:
            sys.path.insert(0, modules_dir)

        file_path = os.path.join(modules_dir, reply_msg.file.name)
        
        # Скачиваем файл модуля на диск
        await reply_msg.download_media(file=file_path)
        importlib.invalidate_caches()
        
        # Читаем код файла, чтобы найти явные зависимости в комментариях
        with open(file_path, "r", encoding="utf-8") as f:
            code = f.read()

        # Анализ запроса на использование прокси ядра
        if proxy_flag is not None:
            set_module_proxy_permission(module_name, proxy_flag)
        else:
            existing_perm = get_module_proxy_permission(module_name)
            if existing_perm is None and module_requests_proxy(code):
                prefix = get_prefix() or "."
                core_proxy_url = get_core_proxy_url()
                proxy_status = f"`{core_proxy_url}`" if core_proxy_url else "*(прокси ядра пока не настроен, можно задать через `.proxy set`)*"
                if os.path.exists(file_path):
                    try: os.remove(file_path)
                    except Exception: pass
                warn_text = (
                    f"🌐 **Запрос сетевого прокси для модуля `{module_name}`!**\n\n"
                    f"📦 Модуль `{module_name}` запрашивает маршрутизацию сетевых запросов через прокси ядра.\n\n"
                    f"🔌 **Текущий прокси ядра:**\n{proxy_status}\n\n"
                    f"❓ **Выберите режим подключения (ответьте на файл модуля):**\n"
                    f"• С прокси: `{prefix}install --proxy` (или `{prefix}install -p`)\n"
                    f"• Без прокси (напрямую): `{prefix}install --no-proxy` (или `{prefix}install -np`)\n\n"
                    f"💡 *Вы также можете управлять разрешениями в меню `{prefix}proxy`.*"
                )
                return await event.edit(warn_text)

        # Ищем строку типа "# requires: requests pillow psutil"
        requires_match = re.search(r"^\s*#\s*requires:\s*(.+)$", code, re.MULTILINE | re.IGNORECASE)
        if requires_match:
            deps = [d.strip() for d in re.split(r"[\s,]+", requires_match.group(1)) if d.strip()]
            deps = [d for d in deps if d and d != module_name]
            if deps:
                await event.edit(f"📦 Найдена разметка зависимостей! Устанавливаю: `{', '.join(deps)}`...")
                for dep in deps:
                    success, pip_err = await pip_install(dep, break_system_packages=allow_break)
                    if not success:
                        if not allow_break and is_pep668_error(pip_err):
                            prefix = get_prefix() or "."
                            if os.path.exists(file_path):
                                try: os.remove(file_path)
                                except Exception: pass
                            warn_text = (
                                f"⚠️ **Внимание: Защита системного окружения (PEP 668)!**\n\n"
                                f"📦 Модулю `{module_name}` требуется библиотека `{dep}`.\n"
                                f"🔒 В вашей операционной системе активен режим `externally-managed-environment`.\n\n"
                                f"ℹ️ Для установки этой библиотеки в текущую систему требуется флаг:\n"
                                f"`--break-system-packages`\n\n"
                                f"⚠️ **Предупреждение:** Этот флаг принудительно устанавливает пакет в системное окружение ОС. "
                                f"В большинстве случаев это безопасно для модулей юзербота, но может нести "
                                f"потенциальный риск конфликта с системными пакетами дистрибутива Linux.\n\n"
                                f"❓ **Вы точно хотите продолжить установку с этим флагом?**\n\n"
                                f"💡 **Для продолжения ответьте на файл модуля командой:**\n"
                                f"`{prefix}install --break-system-packages` (или `{prefix}install -b`)\n\n"
                                f"💡 **Чтобы разрешить навсегда:**\n"
                                f"`{prefix}cfg set installer allow_break_system_packages true`"
                            )
                            return await event.edit(warn_text)
                        # Обрезаем лог pip, чтобы влезло в лимит сообщения телеги
                        truncated_err = pip_err[-200:] if len(pip_err) > 200 else pip_err
                        await event.edit(
                            f"⚠️ Не удалось установить зависимость `{dep}` через pip!\n"
                            f"**Лог ошибки:**\n`...{truncated_err}`\n\nПытаюсь продолжить установку модуля..."
                        )
                        await asyncio.sleep(3)

        # Пытаемся динамически импортировать/перезагрузить модуль
        max_install_attempts = 5
        imported_successfully = False

        for attempt in range(max_install_attempts):
            try:
                importlib.invalidate_caches()
                if module_name in sys.modules:
                    importlib.reload(sys.modules[module_name])
                else:
                    importlib.import_module(module_name)
                
                imported_successfully = True
                break
                
            except ModuleNotFoundError as err:
                # Нам нужен именно отсутствующий модуль, а не сам импортируемый пакет
                missing_package = err.name
                if not missing_package or missing_package == module_name:
                    raise err

                await event.edit(f"🔍 Модуль требует либу `{missing_package}`. Устанавливаю её через pip...")
                
                success, pip_err = await pip_install(missing_package, break_system_packages=allow_break)
                if not success:
                    if not allow_break and is_pep668_error(pip_err):
                        prefix = get_prefix() or "."
                        if os.path.exists(file_path):
                            try: os.remove(file_path)
                            except Exception: pass
                        warn_text = (
                            f"⚠️ **Внимание: Защита системного окружения (PEP 668)!**\n\n"
                            f"📦 Модулю `{module_name}` требуется библиотека `{missing_package}`.\n"
                            f"🔒 В вашей операционной системе активен режим `externally-managed-environment`.\n\n"
                            f"ℹ️ Для установки этой библиотеки в текущую систему требуется флаг:\n"
                            f"`--break-system-packages`\n\n"
                            f"⚠️ **Предупреждение:** Этот флаг принудительно устанавливает пакет в системное окружение ОС. "
                            f"В большинстве случаев это безопасно для модулей юзербота, но может нести "
                            f"потенциальный риск конфликта с системными пакетами дистрибутива Linux.\n\n"
                            f"❓ **Вы точно хотите продолжить установку с этим флагом?**\n\n"
                            f"💡 **Для продолжения ответьте на файл модуля командой:**\n"
                            f"`{prefix}install --break-system-packages` (или `{prefix}install -b`)\n\n"
                            f"💡 **Чтобы разрешить навсегда:**\n"
                            f"`{prefix}cfg set installer allow_break_system_packages true`"
                        )
                        return await event.edit(warn_text)
                    truncated_err = pip_err[-300:] if len(pip_err) > 300 else pip_err
                    raise Exception(
                        f"Не удалось автоматически установить пакет `{missing_package}`.\n"
                        f"**Ошибка pip:**\n`...{truncated_err}`"
                    )
                
                await event.edit(f"✅ Пакет `{missing_package}` успешно установлен! Пробую запустить модуль...")
                await asyncio.sleep(1)

        if imported_successfully:
            await event.edit(f"⏳ `Подготовка и настройка модуля {module_name}...`")
            print(f"[Installer] Модуль {module_name} успешно установлен. Применение настроек...")
            
            proxy_note = ""
            if is_module_proxy_enabled(module_name):
                core_p = get_core_proxy_url()
                proxy_note = f"\n🌐 *Трафик модуля направлен через прокси ядра (`{core_p or 'пока не задан'}`).*"
            elif get_module_proxy_permission(module_name) is False:
                proxy_note = f"\n⚪️ *Модуль выходит в сеть напрямую без прокси.*"

            success_text = f"✅ **Модуль `{module_name}` успешно установлен и готов к работе!**{proxy_note}"
            await restart_userbot(client, event.chat_id, event.id, custom_text=success_text)
        else:
            await event.edit(f"❌ Ошибка: Превышено число попыток автоустановки зависимостей для `{module_name}`.")
        
    except Exception as e:
        # Получаем полный трейсбэк ошибки, чтобы понять, где именно упал Python
        tb_str = traceback.format_exc()
        # Обрезаем трейсбэк снизу (самое важное в конце), чтобы сообщение не превысило 4096 символов
        truncated_tb = tb_str[-600:] if len(tb_str) > 600 else tb_str
        
        await event.edit(
            f"❌ **Ошибка при установке модуля!**\n\n"
            f"ℹ️ **Причина:** `{e}`\n\n"
            f"📋 **Кусок лога ошибки (Traceback):**\n`...{truncated_tb}`"
        )

@register_cmd("uninstall", desc="Удалить модуль. Использование: .uninstall <имя_модуля>")
async def uninstall_module(client, event, args):
    if not args:
        return await event.edit("❌ Укажи имя модуля для удаления: `.uninstall имя_модуля`")
        
    module_name = args.strip()
    if module_name.endswith(".py"):
        module_name = module_name[:-3]

    # Проверка на системность модуля
    mod_info = modules_repo["modules"].get(module_name)
    if mod_info and mod_info.get("system", False):
        return await event.edit(
            f"🔒 Модуль `{mod_info['name']}` (`{module_name}`) системный и не может быть удален!"
        )

    modules_dir = get_modules_dir()
    file_path = os.path.join(modules_dir, f"{module_name}.py")
    
    if not os.path.exists(file_path):
        return await event.edit(f"❌ Модуль `{module_name}` не найден в папке модулей.")
        
    try:
        os.remove(file_path)
        
        if module_name in sys.modules:
            del sys.modules[module_name]
            
        print(f"[Installer] Модуль {module_name} удален. Перезапуск...")
        await event.edit(f"🗑 Удаляю модуль `{module_name}` и перезагружаю юзербота...")
        success_text = f"🗑 **Модуль `{module_name}` успешно удален!**"
        await restart_userbot(client, event.chat_id, event.id, custom_text=success_text)
    except Exception as e:
        await event.edit(f"❌ Ошибка при удалении: {e}")

@register_cmd("sendmod", desc="Отправить файл модуля в чат. Использование: .sendmod <имя_модуля>")
async def send_module(client, event, args):
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
        await client.send_file(
            event.chat_id, 
            file_path, 
            caption=f"📦 Исходный код модуля: **{module_name}**"
        )
        await event.delete()
    except Exception as e:
        await event.edit(f"❌ Ошибка при отправке: {e}")