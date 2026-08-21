import os
import sys
import shutil
import asyncio
from registry import register_cmd, set_module_meta, save_restart_info

# Системный модуль обновления юзербота (удалять нельзя)
set_module_meta(
    name="Обновление",
    desc="Системный модуль обновления юзербота из официального GitHub репозитория.",
    system=True
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFICIAL_REPO_URL = "https://github.com/Artemon4ik8091/userbot-tg"


async def run_git_cmd(*args, timeout=45):
    """
    Асинхронно выполняет команду git в директории юзербота.
    Возвращает (returncode: int, stdout: str, stderr: str).
    """
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
    except asyncio.TimeoutError:
        try:
            process.kill()
        except Exception:
            pass
        return -1, "", "Превышено время ожидания (таймаут 45с)"
    except Exception as e:
        return -1, "", str(e)


async def run_pip_requirements(timeout=120):
    """
    Асинхронно устанавливает / обновляет зависимости из requirements.txt.
    """
    req_file = os.path.join(BASE_DIR, "requirements.txt")
    if not os.path.exists(req_file):
        return True, "requirements.txt не найден"

    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            req_file,
            cwd=BASE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        if process.returncode == 0:
            return True, stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
        return False, err
    except asyncio.TimeoutError:
        try:
            process.kill()
        except Exception:
            pass
        return False, "Превышен таймаут установки зависимостей (120с)"
    except Exception as e:
        return False, str(e)


def is_git_installed():
    """Проверяет наличие git в операционной системе."""
    return shutil.which("git") is not None


def is_git_repo():
    """Проверяет, инициализирован ли репозиторий git в папке бота."""
    return os.path.isdir(os.path.join(BASE_DIR, ".git"))


async def ensure_git_setup():
    """
    Проверяет готовность git и гарантирует привязку origin к официальному репозиторию.
    Возвращает (success: bool, error_message: str).
    """
    if not is_git_installed():
        return False, "В системе не установлен `git`. Установите его (`sudo apt install git` / `pkg install git`)."

    if not is_git_repo():
        # Попытка инициализации git, если папка была скачана архивом
        code, _, err = await run_git_cmd("init")
        if code != 0:
            return False, f"Не удалось инициализировать git: `{err}`"

        await run_git_cmd("remote", "add", "origin", OFFICIAL_REPO_URL)
    else:
        # Проверяем remotes и принудительно фиксируем официальный origin
        code, remotes, _ = await run_git_cmd("remote")
        if "origin" not in remotes.split():
            await run_git_cmd("remote", "add", "origin", OFFICIAL_REPO_URL)
        else:
            await run_git_cmd("remote", "set-url", "origin", OFFICIAL_REPO_URL)

    return True, ""


async def get_current_branch():
    """Получает имя текущей активной ветки."""
    code, out, _ = await run_git_cmd("rev-parse", "--abbrev-ref", "HEAD")
    if code == 0 and out and out != "HEAD":
        return out
    return "main"


async def get_commit_info(revision="HEAD"):
    """
    Получает информацию о коммите.
    Возвращает словарь {hash, short_hash, author, date, message}.
    """
    code, out, _ = await run_git_cmd("log", "-1", "--format=%H%n%h%n%an%n%cr%n%s", revision)
    if code == 0 and out:
        lines = out.split("\n", 4)
        if len(lines) >= 5:
            return {
                "hash": lines[0],
                "short_hash": lines[1],
                "author": lines[2],
                "date": lines[3],
                "message": lines[4]
            }
    return None


@register_cmd("update", desc="Проверить или установить обновления юзербота (.update / .update now / .update force)")
async def update_cmd(client, event, args):
    """
    Команда управления обновлениями юзербота из GitHub.
    Использование:
      .update — проверить наличие обновлений
      .update now — скачать обновления и перезапустить бота
      .update force — принудительно обновить с перезаписью локальных изменений
      .update log [N] — история последних N коммитов
      .update help — справка
    """
    raw_args = args.strip()
    subcmd = raw_args.split()[0].lower() if raw_args else "check"

    # Справка по модулю
    if subcmd in ("help", "h", "?"):
        help_text = (
            "🔄 **Модуль обновления UBTG**\n\n"
            "**Команды:**\n"
            "• `.update` или `.update check` — проверить наличие новых коммитов\n"
            "• `.update now` (или `.update pull`) — скачать обновление, докачать библиотеки и перезапустить бота\n"
            "• `.update force` (или `.update -f`) — принудительно обновить (сбросить локальные конфликты)\n"
            "• `.update log [число]` — показать историю последних коммитов (по умолчанию 10)\n"
            "• `.version` — текущая версия и информация о коммите\n\n"
            f"🔗 **Официальный репозиторий:**\n{OFFICIAL_REPO_URL}"
        )
        return await event.edit(help_text)

    # Проверка готовности окружения Git
    ready, err = await ensure_git_setup()
    if not ready:
        return await event.edit(f"❌ **Ошибка Git:**\n{err}")

    branch = await get_current_branch()

    # --- РЕЖИМ 1: ПРОСМОТР ИСТОРИИ КОММИТОВ (.update log [N]) ---
    if subcmd in ("log", "logs", "changelog", "history"):
        parts = raw_args.split()
        limit = 10
        if len(parts) > 1 and parts[1].isdigit():
            limit = min(max(int(parts[1]), 1), 30)

        await event.edit(f"⏳ Получаю последние {limit} коммитов...")
        code, logs_out, err_log = await run_git_cmd("log", f"-{limit}", "--pretty=format:• `[%h]` **%s** *(%an, %cr)*")
        if code != 0 or not logs_out:
            return await event.edit(f"❌ Не удалось прочитать историю коммитов:\n`{err_log}`")

        msg = (
            f"📜 **История последних коммитов ({branch}):**\n\n"
            f"{logs_out}\n\n"
            f"🔗 [GitHub Репозиторий]({OFFICIAL_REPO_URL})"
        )
        return await event.edit(msg)

    # --- РЕЖИМ 2: ПРИНУДИТЕЛЬНОЕ ОБНОВЛЕНИЕ (.update force / -f) ---
    if subcmd in ("force", "-f", "hard"):
        await event.edit("⚠️ **Запуск принудительного обновления...**\n`Сбрасываю локальные изменения и стягиваю свежий код...`")

        # 1. Fetch
        code, _, err = await run_git_cmd("fetch", "origin", branch)
        if code != 0:
            return await event.edit(f"❌ Ошибка `git fetch`:\n`{err}`")

        # 2. Reset hard
        code, reset_out, err = await run_git_cmd("reset", "--hard", f"origin/{branch}")
        if code != 0:
            return await event.edit(f"❌ Ошибка `git reset`:\n`{err}`")

        # 3. Clean untracked pycache / artifacts (без удаления конфигов и сессий)
        await run_git_cmd("clean", "-fd", "-e", "core_conf.json", "-e", "Global_config.json", "-e", "*.session", "-e", "*.session-journal")

        # 4. Обновление зависимостей
        await event.edit("📦 `Проверяю и обновляю зависимости (pip requirements)...`")
        pip_ok, pip_msg = await run_pip_requirements()
        if not pip_ok:
            await event.edit(f"⚠️ Зависимости установились с предупреждением:\n`{pip_msg[:300]}`\n\nПерезапускаю...")
            await asyncio.sleep(2)

        # 5. Получаем инфу о новом коммите
        new_commit = await get_commit_info("HEAD")
        commit_badge = f"`[{new_commit['short_hash']}]` {new_commit['message']}" if new_commit else "Актуальная версия"

        restart_text = (
            f"🎉 **Юзербот успешно обновлен (force) и перезапущен!**\n\n"
            f"🌿 **Ветка:** `{branch}`\n"
            f"📌 **Коммит:** {commit_badge}\n"
            f"👤 **Автор:** `{new_commit['author'] if new_commit else 'GitHub'}`"
        )

        await event.edit("🔄 Перезапускаю юзербота для применения изменений...")
        save_restart_info(event.chat_id, event.id, restart_text)

        try:
            await client.disconnect()
        except Exception:
            pass

        python = sys.executable
        script = os.path.abspath(sys.argv[0])
        os.execv(python, [python, script] + sys.argv[1:])
        return

    # --- РЕЖИМ 3: СТАНДАРТНОЕ ОБНОВЛЕНИЕ (.update now / pull / apply) ---
    if subcmd in ("now", "pull", "apply", "upgrade"):
        await event.edit("⏳ **Проверяю и скачиваю обновление из GitHub...**")

        # 1. Fetch
        code, _, fetch_err = await run_git_cmd("fetch", "origin", branch)
        if code != 0:
            return await event.edit(f"❌ Ошибка подключения к GitHub (`git fetch`):\n`{fetch_err}`")

        # 2. Выполняем pull
        code, pull_out, pull_err = await run_git_cmd("pull", "origin", branch)
        if code != 0:
            conflict_hint = ""
            if "conflict" in (pull_err + pull_out).lower() or "local changes" in (pull_err + pull_out).lower():
                conflict_hint = (
                    "\n\n💡 **Обнаружен конфликт с локальными файлами!**\n"
                    "Используйте `.update force`, чтобы перезаписать локальные изменения версией из GitHub."
                )
            return await event.edit(f"❌ **Ошибка при выполнении git pull:**\n`{pull_err or pull_out}`{conflict_hint}")

        # 3. Проверяем, были ли вообще изменения
        if "Already up to date" in pull_out or "Уже обновлено" in pull_out:
            cur = await get_commit_info("HEAD")
            cur_info = f"`[{cur['short_hash']}]` {cur['message']}" if cur else ""
            return await event.edit(f"✅ **Юзербот уже обновлен до последней версии!**\n🌿 Ветка: `{branch}`\n📌 {cur_info}")

        # 4. Установка новых зависимостей при необходимости
        await event.edit("📦 `Обновляю зависимости из requirements.txt...`")
        pip_ok, pip_msg = await run_pip_requirements()
        if not pip_ok:
            await event.edit(f"⚠️ Предупреждение pip при установке библиотек:\n`{pip_msg[:300]}`\n\nПродолжаю перезапуск...")
            await asyncio.sleep(2)

        # 5. Получаем новый коммит
        new_commit = await get_commit_info("HEAD")
        commit_badge = f"`[{new_commit['short_hash']}]` {new_commit['message']}" if new_commit else "Актуальная версия"

        restart_text = (
            f"🎉 **Юзербот успешно обновлен и перезапущен!**\n\n"
            f"🌿 **Ветка:** `{branch}`\n"
            f"📌 **Коммит:** {commit_badge}\n"
            f"👤 **Автор:** `{new_commit['author'] if new_commit else 'GitHub'}`"
        )

        await event.edit("🔄 Перезапускаю юзербота для применения всех изменений...")
        save_restart_info(event.chat_id, event.id, restart_text)

        try:
            await client.disconnect()
        except Exception:
            pass

        python = sys.executable
        script = os.path.abspath(sys.argv[0])
        os.execv(python, [python, script] + sys.argv[1:])
        return

    # --- РЕЖИМ 4: ПРОВЕРКА НАЛИЧИЯ ОБНОВЛЕНИЙ (.update / .update check) ---
    await event.edit("🔄 `Проверяю наличие обновлений на GitHub...`")

    # Fetch
    code, _, fetch_err = await run_git_cmd("fetch", "origin", branch)
    if code != 0:
        return await event.edit(f"❌ **Не удалось связаться с GitHub:**\n`{fetch_err}`")

    # Считаем коммиты между локальным HEAD и origin/branch
    code, behind_count_str, _ = await run_git_cmd("rev-list", "--count", f"HEAD..origin/{branch}")
    behind_count = int(behind_count_str) if (code == 0 and behind_count_str.isdigit()) else 0

    code, ahead_count_str, _ = await run_git_cmd("rev-list", "--count", f"origin/{branch}..HEAD")
    ahead_count = int(ahead_count_str) if (code == 0 and ahead_count_str.isdigit()) else 0

    current_commit = await get_commit_info("HEAD")
    cur_hash_str = f"`{current_commit['short_hash']}`" if current_commit else "Н/Д"
    cur_msg_str = current_commit['message'] if current_commit else ""
    cur_author_str = current_commit['author'] if current_commit else "Неизвестен"
    cur_date_str = current_commit['date'] if current_commit else ""

    if behind_count > 0:
        # Есть доступные обновления
        code, new_commits_log, _ = await run_git_cmd(
            "log",
            f"HEAD..origin/{branch}",
            "--pretty=format:• `[%h]` **%s** *(%an)*",
            "-n",
            "10"
        )

        commits_display = new_commits_log if (code == 0 and new_commits_log) else "• Новые изменения в репозитории"
        if behind_count > 10:
            commits_display += f"\n*...и еще {behind_count - 10} коммитов*"

        msg = (
            f"📦 **Доступно обновление UBTG!**\n\n"
            f"🌿 **Ветка:** `{branch}`\n"
            f"🔢 **Новых коммитов:** `{behind_count}`\n\n"
            f"📋 **Список изменений:**\n"
            f"{commits_display}\n\n"
            f"💡 **Чтобы установить обновление, выполните:**\n"
            f"`.update now` — скачать и перезапустить бота\n"
            f"`.update force` — принудительно (если есть локальные конфликты)"
        )
        await event.edit(msg)

    elif ahead_count > 0:
        # Локальная ветка опережает удаленную
        msg = (
            f"ℹ️ **Локальная версия опережает репозиторий**\n\n"
            f"🌿 **Ветка:** `{branch}`\n"
            f"🔢 **Локальных коммитов:** `{ahead_count}`\n"
            f"📌 **Текущий коммит:** {cur_hash_str} — {cur_msg_str}\n"
            f"👤 **Автор:** `{cur_author_str}` ({cur_date_str})\n\n"
            f"💡 Для синхронизации с GitHub можно использовать `.update force`."
        )
        await event.edit(msg)

    else:
        # Полностью актуальная версия
        msg = (
            f"✅ **Юзербот обновлен до последней версии!**\n\n"
            f"🌿 **Ветка:** `{branch}`\n"
            f"📌 **Текущий коммит:** {cur_hash_str}\n"
            f"💬 `{cur_msg_str}`\n"
            f"👤 **Автор:** `{cur_author_str}` ({cur_date_str})\n\n"
            f"🔗 [GitHub Репозиторий]({OFFICIAL_REPO_URL})"
        )
        await event.edit(msg)


@register_cmd("version", desc="Показывает текущую версию, коммит и ветку юзербота")
async def version_cmd(client, event, args):
    """Выводит детальную информацию о текущей установленной версии юзербота."""
    branch = await get_current_branch() if is_git_repo() else "main"
    commit = await get_commit_info("HEAD") if is_git_repo() else None

    if commit:
        commit_line = f"`{commit['short_hash']}` — {commit['message']}"
        author_line = f"`{commit['author']}` ({commit['date']})"
    else:
        commit_line = "Не удалось определить (git не инициализирован)"
        author_line = "Н/Д"

    text = (
        "🤖 **UBTG Userbot | Версия**\n\n"
        f"🌿 **Ветка:** `{branch}`\n"
        f"📌 **Коммит:** {commit_line}\n"
        f"👤 **Автор коммита:** {author_line}\n"
        f"🐍 **Python:** `{sys.version.split()[0]}`\n"
        f"🔗 **Репозиторий:** [GitHub]({OFFICIAL_REPO_URL})\n\n"
        "💡 *Для проверки обновлений используйте `.update`*"
    )
    await event.edit(text)


@register_cmd("checkupdate", desc="Быстрая проверка наличия обновлений на GitHub")
async def check_update_alias(client, event, args):
    """Алиас для быстрой проверки обновлений."""
    await update_cmd(client, event, "check")
