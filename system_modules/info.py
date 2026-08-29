import os
import platform
import asyncio
import psutil
from telethon import events
from registry import init_config, get_config, set_module_meta, register_cmd

# Задаем мета-инфу
set_module_meta("Info", "Показывает красивую инфу о юзерботе и системе", system=True)

REPO_URL = "https://gitea.com/aswer/userbot-tg"
GITHUB_REPO_URL = "https://github.com/Artemon4ik8091/userbot-tg"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


async def run_git_cmd(*args, timeout=15):
    """Асинхронно выполняет команду git в директории юзербота."""
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=BASE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        out_str = stdout.decode("utf-8", errors="replace").strip()
        err_str = stderr.decode("utf-8", errors="replace").strip()
        return process.returncode, out_str, err_str
    except Exception as e:
        return -1, "", str(e)


async def get_git_branch():
    """Получает имя текущей активной ветки."""
    code, out, _ = await run_git_cmd("rev-parse", "--abbrev-ref", "HEAD")
    if code == 0 and out and out != "HEAD":
        return out
    return "main"


async def get_git_commit_info():
    """
    Получает детальную информацию о последнем коммите.
    Возвращает dict {hash, short_hash, author, date, message}.
    """
    code, out, _ = await run_git_cmd("log", "-1", "--format=%H%n%h%n%an%n%cr%n%B")
    if code == 0 and out:
        lines = out.split("\n", 4)
        if len(lines) >= 5:
            return {
                "hash": lines[0].strip(),
                "short_hash": lines[1].strip(),
                "author": lines[2].strip(),
                "date": lines[3].strip(),
                "message": lines[4].strip()
            }
        elif len(lines) >= 3:
            return {
                "hash": lines[0].strip(),
                "short_hash": lines[1].strip(),
                "author": lines[2].strip(),
                "date": lines[3].strip() if len(lines) > 3 else "",
                "message": ""
            }
    return {
        "hash": "Н/Д",
        "short_hash": "Н/Д",
        "author": "Artemon4ik8091",
        "date": "",
        "message": "Нет данных о коммите"
    }


def format_expandable_quote(text: str) -> str:
    """Форматирует текст в свёрнутую цитату для Telegram Markdown."""
    if not text:
        return "**>Нет комментария||**"
    lines = text.strip().split("\n")
    quoted_lines = [f">{line}" for line in lines]
    return f"**{chr(10).join(quoted_lines)}||**"


# Инициализируем дефолтный конфиг
init_config("module_info", {
    "custom_text": (
        "Привет! Я юзербот, написанный на чистом питоне. 😎{br}"
        "Всё стабильно, полет нормальный.{br}{br}"
        "💻 **Системная сводка:**{br}"
        "**ЦП:** `{cpu_usage}%` | **ОЗУ:** `{ram} МБ`{br}"
        "**ОС:** `{os} ({arch})`{br}"
        "**Проц:** `{cpu_name}`{br}{br}"
        "🌿 **Ветка:** `{branch}`{br}"
        "📌 **Коммит:** `{commit_short}`{br}"
        "👤 **Автор:** `{author}`{br}"
        "💬 **Комментарий:**{br}{commit_quote}"
    ),
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

    # --- СБОР ИНФОРМАЦИИ О РЕПОЗИТОРИИ / GIT ---
    branch = await get_git_branch()
    commit_info = await get_git_commit_info()
    commit_hash = commit_info["hash"]
    commit_short = commit_info["short_hash"]
    commit_author = commit_info["author"]
    commit_date = commit_info["date"]
    commit_msg = commit_info["message"]
    commit_quote = format_expandable_quote(commit_msg)

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

    # Переменные ветки, коммита и автора
    custom = custom.replace("{branch}", branch)
    custom = custom.replace("{commit_hash}", commit_hash)
    custom = custom.replace("{commit_short}", commit_short)
    custom = custom.replace("{commit_hash_short}", commit_short)
    custom = custom.replace("{commit}", commit_short)
    custom = custom.replace("{commit_msg}", commit_msg)
    custom = custom.replace("{commit_message}", commit_msg)
    custom = custom.replace("{commit_quote}", commit_quote)
    custom = custom.replace("{commit_msg_quote}", commit_quote)
    custom = custom.replace("{author}", commit_author)
    custom = custom.replace("{commit_author}", commit_author)
    custom = custom.replace("{commit_date}", commit_date)
    custom = custom.replace("{repo}", REPO_URL)
    custom = custom.replace("{repo_url}", REPO_URL)

    # Хардкодный текст, который не меняется через конфиг (твой копирайт)
    hardcoded_text = (
        f"\n\n**🤖 UBTG Userbot | by aswer**\n"
        f"🔗 [Gitea Репозиторий]({REPO_URL}) | [GitHub]({GITHUB_REPO_URL})"
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
        "или используй команду `.cfg` для настройки `\"module_info\"`.\n\n"
        "**Доступные переменные для кастомного текста:**\n"
        "🔹 `{br}` — перенос на новую строку\n"
        "🔹 `{os}` — твоя операционная система\n"
        "🔹 `{arch}` — архитектура (например, AMD64 или ARM)\n"
        "🔹 `{cpu_name}` — название процессора\n"
        "🔹 `{cpu_usage}` — текущая загрузка процессора (%)\n"
        "🔹 `{ram}` — сколько ОЗУ (в МБ) жрет юзербот\n"
        "🔹 `{branch}` — текущая ветка Git (например, main)\n"
        "🔹 `{commit}` / `{commit_short}` — короткий хэш коммита\n"
        "🔹 `{commit_hash}` — полный хэш коммита\n"
        "🔹 `{commit_msg}` — текст сообщения/комментария коммита\n"
        "🔹 `{commit_quote}` — комментарий коммита в свёрнутой цитате\n"
        "🔹 `{author}` / `{commit_author}` — автор последнего коммита\n"
        "🔹 `{commit_date}` — дата последнего коммита\n"
        "🔹 `{repo}` — ссылка на репозиторий\n\n"
        "**Как добавить пикчу или гифку:**\n"
        "Впиши прямую ссылку на картинку (начинается с `http...`) или локальный путь к файлу в параметр `\"media_path\"`. "
        "Если оставить там пустые кавычки `\"\"`, бот будет отправлять просто текст."
    )
    await event.edit(help_text)