import os
import sys
import re
import asyncio
import importlib
import traceback
from registry import register_cmd, set_module_meta, modules_repo

# Обновляем метаданные модуля (он системный, удалять нельзя)
set_module_meta(
    name="modules manager",
    desc="Установка, удаление и отправка модулей с детальным дебагом ошибок.",
    system=True
)

def get_modules_dir():
    # Если installer.py лежит в system_modules, то выходим на уровень выше и ищем папочку modules
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), 'modules')

async def pip_install(package_name):
    """
    Асинхронно устанавливает пакет через pip.
    Возвращает кортеж (success: bool, error_msg: str)
    """
    print(f"[Installer] Запуск установки пакета: {package_name}")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pip", "install", package_name,
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

@register_cmd("install", desc="Установить модуль из файла (с автоустановкой зависимостей и дебагом)")
async def install_module(client, event, args):
    reply_msg = await event.get_reply_message()
    
    if not reply_msg or not reply_msg.file:
        await event.edit("⚠️ Бро, сделай реплай на сообщение с .py файлом!")
        return

    if not reply_msg.file.name.endswith(".py"):
        await event.edit("❌ Это не .py файл. Я могу устанавливать только Python модули.")
        return

    module_name = reply_msg.file.name[:-3]
    await event.edit(f"⏳ `Скачиваю {reply_msg.file.name}...`")
    
    try:
        modules_dir = get_modules_dir()
        file_path = os.path.join(modules_dir, reply_msg.file.name)
        
        # Скачиваем файл модуля на диск
        await reply_msg.download_media(file=file_path)
        
        # Читаем код файла, чтобы найти явные зависимости в комментариях
        with open(file_path, "r", encoding="utf-8") as f:
            code = f.read()

        # Ищем строку типа "# requires: requests pillow psutil"
        requires_match = re.search(r"^\s*#\s*requires:\s*(.+)$", code, re.MULTILINE | re.IGNORECASE)
        if requires_match:
            deps = [d.strip() for d in re.split(r"[\s,]+", requires_match.group(1)) if d.strip()]
            if deps:
                await event.edit(f"📦 Найдена разметка зависимостей! Устанавливаю: `{', '.join(deps)}`...")
                for dep in deps:
                    success, pip_err = await pip_install(dep)
                    if not success:
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
                if module_name in sys.modules:
                    importlib.reload(sys.modules[module_name])
                else:
                    importlib.import_module(module_name)
                
                imported_successfully = True
                break
                
            except ModuleNotFoundError as err:
                # Нам нужен именно отсутствующий модуль, а не внутренние импорты самого импортируемого пакета
                missing_package = err.name
                if not missing_package:
                    raise err

                await event.edit(f"🔍 Модуль требует либу `{missing_package}`. Устанавливаю её через pip...")
                
                success, pip_err = await pip_install(missing_package)
                if not success:
                    truncated_err = pip_err[-300:] if len(pip_err) > 300 else pip_err
                    raise Exception(
                        f"Не удалось автоматически установить пакет `{missing_package}`.\n"
                        f"**Ошибка pip:**\n`...{truncated_err}`"
                    )
                
                await event.edit(f"✅ Пакет `{missing_package}` успешно установлен! Пробую запустить модуль...")
                await asyncio.sleep(1)

        if imported_successfully:
            await event.edit(f"✅ Модуль `{module_name}` успешно установлен, все зависимости на месте!")
            print(f"[Installer] Модуль {module_name} успешно установлен.")
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
            
        await event.edit(f"🗑 Модуль `{module_name}` успешно удален!\n*(Рекомендуется сделать `.restart` для очистки памяти)*")
        print(f"[Installer] Модуль {module_name} удален.")
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