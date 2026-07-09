import os
import platform
import psutil
from telethon import events
from registry import init_config, get_config, set_module_meta, register_cmd

# Задаем мета-инфу
set_module_meta("Info", "Показывает красивую инфу о юзерботе и системе", system=True)

# Инициализируем дефолтный конфиг
init_config("module_info", {
    "custom_text": "Привет! Я юзербот, написанный на чистом питоне. 😎{br}Всё стабильно, полет нормальный.{br}{br}💻 Системная сводка:{br}**ЦП:** `{cpu_usage}%` | **ОЗУ:** `{ram} МБ`{br}**ОС:** `{os} ({arch})`{br}**Проц:** `{cpu_name}`",
    "media_path": ""  # Сюда можно вставить ссылку на картинку/гифку (http...) или путь к файлу
})

@register_cmd("info", "Выводит инфу о юзерботе")
async def cmd_info(client, event, args):
    # Пока собираем стату, дадим понять, что процесс пошел
    await event.edit("🔄 Собираю инфу, сек...")

    # --- СБОР СИСТЕМНОЙ ИНФЫ ---
    process = psutil.Process(os.getpid())
    ram_usage = f"{process.memory_info().rss / (1024 * 1024):.2f}"
    cpu_usage = f"{psutil.cpu_percent(interval=0.5):.1f}"
    
    os_info = f"{platform.system()} {platform.release()}"
    arch = platform.machine()
    cpu_name = platform.processor() or "Неизвестный камень"

    # --- РАБОТА С ТЕКСТОМ ---
    # Достаем кастомный текст из конфига
    custom = get_config("module_info", "custom_text", "Текст не найден.")
    
    # Заменяем маркеры на реальные значения
    custom = custom.replace("{br}", "\n")
    custom = custom.replace("{os}", os_info)
    custom = custom.replace("{arch}", arch)
    custom = custom.replace("{ram}", ram_usage)
    custom = custom.replace("{cpu_usage}", cpu_usage)
    custom = custom.replace("{cpu_name}", cpu_name)

    # Хардкодный текст, который не меняется через конфиг (твой копирайт)
    hardcoded_text = (
        f"\n\n**🤖 UBTG Userbot | by aswer**\n"
    )

    # Склеиваем
    final_text = custom + hardcoded_text

    # --- ОТПРАВКА ---
    media = get_config("module_info", "media_path", "")

    if media:
        try:
            await client.send_file(
                event.chat_id,
                file=media,
                caption=final_text,
                reply_to=event.message.reply_to_msg_id,
                force_document=False 
            )
            await event.delete()
        except Exception as e:
            await event.edit(f"{final_text}\n\n*(Медиа не подгрузилось: {e})*")
    else:
        await event.edit(final_text)


@register_cmd("infohelp", "Справка по настройке текста и картинки для .info")
async def cmd_info_help(client, event, args):
    """Выводит инструкцию по настройке модуля"""
    help_text = (
        "**🛠 Справка по настройке модуля Info**\n\n"
        "Чтобы поменять текст или добавить картинку, открой файл `Global_config.json` в папке с ботом "
        "и найди там блок `\"module_info\"`.\n\n"
        "**Доступные переменные для кастомного текста:**\n"
        "🔹 `{br}` — перенос на новую строку\n"
        "🔹 `{os}` — твоя операционная система\n"
        "🔹 `{arch}` — архитектура (например, AMD64 или ARM)\n"
        "🔹 `{cpu_name}` — название процессора\n"
        "🔹 `{cpu_usage}` — текущая загрузка процессора (%)\n"
        "🔹 `{ram}` — сколько ОЗУ (в МБ) жрет юзербот\n\n"
        "**Как добавить пикчу или гифку:**\n"
        "Впиши прямую ссылку на картинку (начинается с `http...`) или локальный путь к файлу в параметр `\"media_path\"`. "
        "Если оставить там пустые кавычки `\"\"`, бот будет отправлять просто текст."
    )
    await event.edit(help_text)