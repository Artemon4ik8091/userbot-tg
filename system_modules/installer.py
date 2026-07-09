import os
import importlib
from registry import register_cmd, set_module_meta, modules_repo
import sys

# Обновляем метаданные модуля, так как функционал расширился (системный, нельзя удалить)
set_module_meta(
    name="modules manager",
    desc="Установка, удаление и отправка модулей прямо из чата.",
    system=True
)

# Вспомогательная функция для получения пути к папке с модулями
def get_modules_dir():
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), 'modules')

@register_cmd("install", desc="Установить модуль из файла, на который ты сделал реплай")
async def install_module(client, event, args):
    reply_msg = await event.get_reply_message()
    
    if not reply_msg or not reply_msg.file:
        await event.edit("⚠️ Бро, сделай реплай на сообщение с .py файлом!")
        return

    if not reply_msg.file.name.endswith(".py"):
        await event.edit("❌ Это не .py файл. Я могу устанавливать только Python модули.")
        return

    await event.edit(f"`Устанавливаю {reply_msg.file.name}...`")
    
    try:
        modules_dir = get_modules_dir()
        file_path = os.path.join(modules_dir, reply_msg.file.name)
        
        # Скачиваем файл
        await reply_msg.download_media(file=file_path)
        
        # Динамически импортируем/перезагружаем модуль
        module_name = reply_msg.file.name[:-3]
        
        # Если модуль уже был — перезагружаем, если нет — грузим
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])
        else:
            importlib.import_module(module_name)
            
        await event.edit(f"✅ Модуль `{module_name}` успешно установлен и подгружен!")
        print(f"[Installer] Модуль {module_name} установлен.")
        
    except Exception as e:
        await event.edit(f"❌ Ошибка установки: {e}")

@register_cmd("uninstall", desc="Удалить модуль. Использование: .uninstall <имя_модуля>")
async def uninstall_module(client, event, args):
    if not args:
        return await event.edit("❌ Укажи имя модуля для удаления: `.uninstall имя_модуля`")
        
    module_name = args.strip()
    # Убираем .py, если пользователь случайно ввел его
    if module_name.endswith(".py"):
        module_name = module_name[:-3]

    # --- ПРОВЕРКА НА СИСТЕМНЫЙ МОДУЛЬ ---
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
        # Удаляем файл
        os.remove(file_path)
        
        # Пытаемся выгрузить из кэша системы (если он там есть)
        if module_name in sys.modules:
            del sys.modules[module_name]
            
        await event.edit(f"🗑 Модуль `{module_name}` успешно удален!\n*(Возможно, потребуется перезагрузка бота для полного удаления команд из памяти)*")
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
        
        # Отправляем файл в текущий чат
        await client.send_file(
            event.chat_id, 
            file_path, 
            caption=f"📦 Исходный код модуля: **{module_name}**"
        )
        
        # Удаляем сообщение с командой, чтобы не мусорить в чате
        await event.delete()
    except Exception as e:
        await event.edit(f"❌ Ошибка при отправке: {e}")