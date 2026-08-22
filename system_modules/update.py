import os
import sys
import shutil
import asyncio
from telethon import Button, errors
from registry import (
    register_cmd,
    register_bg,
    register_callback,
    set_module_meta,
    restart_userbot,
    send_inline,
    get_bot,
    get_owner_id,
    get_bot_username,
    get_config,
    set_config,
    init_config,
    get_logger,
    get_main_client
)

logger = get_logger("Update")

# Системный модуль обновления юзербота (удалять нельзя)
set_module_meta(
    name="Обновление",
    desc="Системный модуль обновления юзербота из официального GitHub репозитория с инлайн-кнопками и фоновым чекером.",
    system=True
)

init_config("module_update", {
    "snoozed_hash": ""
})

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFICIAL_REPO_URL = "https://github.com/Artemon4ik8091/userbot-tg"


async def run_git_cmd(*args, timeout=45):
    """
    Асинхронно выполняет команду git в директории юзербота.
    Возвращает (returncode: int, stdout: str, stderr: str).
    """
    cmd_str = f"git {' '.join(args)}"
    logger.debug(f"Выполнение команды: {cmd_str}")
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
        logger.debug(f"Результат [{cmd_str}]: code={process.returncode}, out={out_str[:150]}, err={err_str[:150]}")
        return process.returncode, out_str, err_str
    except asyncio.TimeoutError:
        try:
            process.kill()
        except Exception:
            pass
        logger.error(f"Превышено время ожидания ({timeout}с) для команды: {cmd_str}")
        return -1, "", f"Превышено время ожидания (таймаут {timeout}с)"
    except Exception as e:
        logger.error(f"Исключение при выполнении {cmd_str}: {e}")
        return -1, "", str(e)


async def run_pip_requirements(timeout=120):
    """
    Асинхронно устанавливает / обновляет зависимости из requirements.txt.
    """
    req_file = os.path.join(BASE_DIR, "requirements.txt")
    if not os.path.exists(req_file):
        logger.debug("requirements.txt не найден, пропускаю pip")
        return True, "requirements.txt не найден"

    logger.debug("Запуск установки / обновления зависимостей из requirements.txt...")
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
            logger.debug("Зависимости pip успешно обновлены")
            return True, stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
        logger.warning(f"Предупреждение/ошибка pip: {err[:200]}")
        return False, err
    except asyncio.TimeoutError:
        try:
            process.kill()
        except Exception:
            pass
        logger.error(f"Превышен таймаут установки зависимостей ({timeout}с)")
        return False, f"Превышен таймаут установки зависимостей ({timeout}с)"
    except Exception as e:
        logger.error(f"Исключение при запуске pip: {e}")
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
        logger.error("Git не установлен в операционной системе!")
        return False, "В системе не установлен `git`. Установите его (`sudo apt install git` / `pkg install git`)."

    if not is_git_repo():
        logger.info("Git репозиторий не инициализирован. Выполняю git init...")
        code, _, err = await run_git_cmd("init")
        if code != 0:
            logger.error(f"Не удалось инициализировать git: {err}")
            return False, f"Не удалось инициализировать git: `{err}`"

        await run_git_cmd("remote", "add", "origin", OFFICIAL_REPO_URL)
    else:
        code, remotes, _ = await run_git_cmd("remote")
        if "origin" not in remotes.split():
            logger.debug(f"Добавляю origin: {OFFICIAL_REPO_URL}")
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


async def check_updates_state():
    """
    Выполняет проверку обновлений через fetch и сравнение HEAD с origin/branch.
    Возвращает словарь с результатами проверки.
    """
    logger.debug("Запуск проверки наличия обновлений...")
    ready, err = await ensure_git_setup()
    if not ready:
        return {"ok": False, "error": err}

    branch = await get_current_branch()
    logger.debug(f"Текущая ветка: {branch}. Выполняю git fetch...")
    code, _, fetch_err = await run_git_cmd("fetch", "origin", branch, timeout=30)
    if code != 0:
        logger.error(f"Ошибка при git fetch: {fetch_err}")
        return {"ok": False, "error": f"Не удалось связаться с GitHub (`git fetch`):\n`{fetch_err}`"}

    code_behind, behind_count_str, _ = await run_git_cmd("rev-list", "--count", f"HEAD..origin/{branch}")
    behind_count = int(behind_count_str) if (code_behind == 0 and behind_count_str.isdigit()) else 0

    code_ahead, ahead_count_str, _ = await run_git_cmd("rev-list", "--count", f"origin/{branch}..HEAD")
    ahead_count = int(ahead_count_str) if (code_ahead == 0 and ahead_count_str.isdigit()) else 0

    current_commit = await get_commit_info("HEAD")
    logger.debug(f"Состояние: behind={behind_count}, ahead={ahead_count}, commit={current_commit.get('short_hash') if current_commit else 'None'}")

    new_commits_log = ""
    if behind_count > 0:
        code_log, logs_out, _ = await run_git_cmd(
            "log",
            f"HEAD..origin/{branch}",
            "--pretty=format:• `[%h]` **%s** *(%an)*",
            "-n",
            "10"
        )
        if code_log == 0 and logs_out:
            new_commits_log = logs_out

    return {
        "ok": True,
        "branch": branch,
        "behind_count": behind_count,
        "ahead_count": ahead_count,
        "current_commit": current_commit,
        "new_commits_log": new_commits_log
    }


def build_update_ui(state):
    """
    Формирует текст сообщения и инлайн-кнопки на основе текущего состояния обновлений.
    """
    branch = state.get("branch", "main")
    behind_count = state.get("behind_count", 0)
    ahead_count = state.get("ahead_count", 0)
    current_commit = state.get("current_commit")
    new_commits_log = state.get("new_commits_log", "")

    cur_hash_str = f"`{current_commit['short_hash']}`" if current_commit else "Н/Д"
    cur_msg_str = current_commit['message'] if current_commit else ""
    cur_author_str = current_commit['author'] if current_commit else "Неизвестен"
    cur_date_str = current_commit['date'] if current_commit else ""

    if behind_count > 0:
        commits_display = new_commits_log or "• Новые изменения в репозитории"
        if behind_count > 10:
            commits_display += f"\n*...и еще {behind_count - 10} коммитов*"

        text = (
            f"📦 **Доступно обновление UBTG!**\n\n"
            f"🌿 **Ветка:** `{branch}`\n"
            f"🔢 **Новых коммитов:** `{behind_count}`\n\n"
            f"📋 **Список изменений:**\n"
            f"{commits_display}\n\n"
            f"💡 **Выберите действие:**"
        )
        buttons = [
            [
                Button.inline("🚀 Обновить", b"upd_apply"),
                Button.inline("❌ Отмена", b"upd_cancel")
            ]
        ]
        return text, buttons

    elif ahead_count > 0:
        text = (
            f"ℹ️ **Локальная версия опережает репозиторий**\n\n"
            f"🌿 **Ветка:** `{branch}`\n"
            f"🔢 **Локальных коммитов:** `{ahead_count}`\n"
            f"📌 **Текущий коммит:** {cur_hash_str} — {cur_msg_str}\n"
            f"👤 **Автор:** `{cur_author_str}` ({cur_date_str})\n\n"
            f"💡 Для синхронизации с GitHub можно использовать `.update force`."
        )
        buttons = [
            [
                Button.inline("🔄 Попробовать снова", b"upd_recheck"),
                Button.inline("❌ Отмена", b"upd_cancel")
            ]
        ]
        return text, buttons

    else:
        text = (
            f"✅ **Юзербот обновлен до последней версии!**\n\n"
            f"🌿 **Ветка:** `{branch}`\n"
            f"📌 **Текущий коммит:** {cur_hash_str}\n"
            f"💬 `{cur_msg_str}`\n"
            f"👤 **Автор:** `{cur_author_str}` ({cur_date_str})\n\n"
            f"🔗 [GitHub Репозиторий]({OFFICIAL_REPO_URL})"
        )
        buttons = [
            [Button.inline("🔄 Попробовать снова", b"upd_recheck")]
        ]
        return text, buttons


async def edit_any_message(client, chat_id, message_id, text, buttons=None):
    """
    Универсальное редактирование сообщения через Telegram бота или юзербота.
    """
    bot = get_bot()
    if bot:
        try:
            await bot.edit_message(chat_id, message_id, text, buttons=buttons)
            return True
        except Exception:
            pass

    if client:
        try:
            await client.edit_message(chat_id, message_id, text, buttons=buttons)
            return True
        except Exception:
            pass

    return False


async def run_update_sequence(client, chat_id, message_id, branch, force=False, event=None):
    """
    Выполняет последовательность скачивания обновления, установки зависимостей и перезапуска.
    """
    target_client = client or get_main_client()
    logger.info(f"🚀 Запуск процесса обновления (ветка: {branch}, force={force})...")

    async def update_status(msg_text):
        if event:
            try:
                await event.edit(msg_text, buttons=None)
                return
            except Exception:
                pass
        await edit_any_message(target_client, chat_id, message_id, msg_text, buttons=None)

    try:
        await update_status("⏳ **Проверяю и скачиваю обновление из GitHub...**")

        # 1. Fetch
        logger.debug(f"Выполняю git fetch origin {branch}...")
        code, _, fetch_err = await run_git_cmd("fetch", "origin", branch)
        if code != 0:
            logger.error(f"git fetch origin {branch} завершился с ошибкой: {fetch_err}")
            return await update_status(f"❌ Ошибка подключения к GitHub (`git fetch`):\n`{fetch_err}`")

        # 2. Pull или Reset (в зависимости от режима force)
        if force:
            logger.info("Режим force: сброс локальных изменений (git reset --hard)...")
            code, _, err = await run_git_cmd("reset", "--hard", f"origin/{branch}")
            if code != 0:
                logger.error(f"git reset --hard завершился с ошибкой: {err}")
                return await update_status(f"❌ Ошибка `git reset`:\n`{err}`")
            await run_git_cmd("clean", "-fd", "-e", "core_conf.json", "-e", "Global_config.json", "-e", "*.session", "-e", "*.session-journal")
        else:
            logger.info(f"Выполняю git pull origin {branch}...")
            code, pull_out, pull_err = await run_git_cmd("pull", "origin", branch)
            if code != 0:
                logger.error(f"git pull завершился с ошибкой (code={code}): out='{pull_out}', err='{pull_err}'")
                conflict_hint = ""
                if "conflict" in (pull_err + pull_out).lower() or "local changes" in (pull_err + pull_out).lower():
                    conflict_hint = (
                        "\n\n💡 **Обнаружен конфликт с локальными файлами!**\n"
                        "Используйте `.update force`, чтобы перезаписать локальные изменения версией из GitHub."
                    )
                return await update_status(f"❌ **Ошибка при выполнении git pull:**\n`{pull_err or pull_out}`{conflict_hint}")

        # 3. Обновление зависимостей
        logger.info("Проверка и установка зависимостей из requirements.txt...")
        await update_status("📦 `Обновляю зависимости из requirements.txt...`")
        pip_ok, pip_msg = await run_pip_requirements()
        if not pip_ok:
            logger.warning(f"Предупреждение pip: {pip_msg[:200]}")
            await update_status(f"⚠️ Предупреждение pip при установке библиотек:\n`{pip_msg[:300]}`\n\nПродолжаю перезапуск...")
            await asyncio.sleep(2)

        # 4. Получаем данные о новом коммите
        new_commit = await get_commit_info("HEAD")
        commit_badge = f"`[{new_commit['short_hash']}]` {new_commit['message']}" if new_commit else "Актуальная версия"
        logger.info(f"Актуальный коммит: {commit_badge}")

        restart_text = (
            f"🎉 **Юзербот успешно обновлен{' (force)' if force else ''} и перезапущен!**\n\n"
            f"🌿 **Ветка:** `{branch}`\n"
            f"📌 **Коммит:** {commit_badge}\n"
            f"👤 **Автор:** `{new_commit['author'] if new_commit else 'GitHub'}`"
        )

        logger.info("Подготовка к перезагрузке юзербота...")
        await update_status("🔄 Перезапускаю юзербота для применения всех изменений...")
        set_config("module_update", "snoozed_hash", "")
        await restart_userbot(target_client, chat_id, message_id, custom_text=restart_text)

    except Exception as e:
        logger.error(f"Непредвиденное исключение при обновлении: {e}")
        await update_status(f"❌ **Ошибка обновления:**\n`{e}`")


# ==========================================
# ОБРАБОТЧИКИ НАЖАТИЙ НА ИНЛАЙН-КНОПКИ
# ==========================================

def is_authorized_user(sender_id):
    """Проверяет, является ли пользователь владельцем бота."""
    owner_id = get_owner_id()
    if not owner_id:
        return True
    return sender_id == owner_id


@register_callback("upd_apply")
async def cb_upd_apply(event, data):
    """Кнопка 'Обновить' в инлайн-сообщении чата."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        logger.warning(f"Неавторизованная попытка обновления от {sender.id}")
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    logger.info(f"Нажата кнопка 'Обновить' пользователем {sender.id}")
    await event.answer("🚀 Запуск обновления...")
    msg_id = getattr(event, "message_id", None) or getattr(event, "id", 0)
    branch = await get_current_branch()
    await run_update_sequence(get_main_client(), event.chat_id, msg_id, branch, force=False, event=event)


@register_callback("upd_cancel")
async def cb_upd_cancel(event, data):
    """Кнопка 'Отмена' в инлайн-сообщении."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    logger.info(f"Обновление отменено пользователем {sender.id}")
    await event.answer("Отменено")
    text = (
        "❌ **Проверка обновлений завершена.**\n\n"
        "💡 Для новой проверки используйте команду `.update` или нажмите кнопку ниже."
    )
    buttons = [[Button.inline("🔄 Попробовать снова", b"upd_recheck")]]
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        pass


@register_callback("upd_recheck")
async def cb_upd_recheck(event, data):
    """Кнопка 'Попробовать снова' в инлайн-сообщении."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    logger.info(f"Повторная проверка обновлений пользователем {sender.id}")
    await event.answer("🔄 Проверяю обновления...")
    try:
        await event.edit("🔄 `Проверяю наличие обновлений на GitHub...`", buttons=None)
    except errors.MessageNotModifiedError:
        pass

    state = await check_updates_state()
    if not state.get("ok"):
        err_msg = f"❌ **Ошибка проверки обновлений:**\n`{state.get('error')}`"
        buttons = [[Button.inline("🔄 Попробовать снова", b"upd_recheck")]]
        try:
            await event.edit(err_msg, buttons=buttons)
        except errors.MessageNotModifiedError:
            pass
        return

    text, buttons = build_update_ui(state)
    try:
        await event.edit(text, buttons=buttons)
    except errors.MessageNotModifiedError:
        await event.answer("✅ Информация актуальна!")


@register_callback("bot_upd_apply")
async def cb_bot_upd_apply(event, data):
    """Кнопка 'Обновить' в сообщении от Telegram-бота."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        logger.warning(f"Неавторизованная попытка обновления от {sender.id}")
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    logger.info(f"Нажата кнопка 'Обновить' через бота пользователем {sender.id}")
    await event.answer("🚀 Запуск обновления...")
    msg_id = getattr(event, "message_id", None) or getattr(event, "id", 0)
    branch = await get_current_branch()
    await run_update_sequence(get_main_client(), event.chat_id, msg_id, branch, force=False, event=event)


@register_callback("bot_upd_snooze")
async def cb_bot_upd_snooze(event, data):
    """Кнопка 'Отложить' в сообщении от Telegram-бота."""
    sender = await event.get_sender()
    if not is_authorized_user(sender.id):
        return await event.answer("⚠️ Действие доступно только владельцу!", alert=True)

    # Извлекаем хэш версии из callback_data или запрашиваем актуальный
    snoozed_hash = data.replace("bot_upd_snooze_", "").replace("bot_upd_snooze", "").strip("_")
    if not snoozed_hash:
        branch = await get_current_branch()
        _, remote_hash, _ = await run_git_cmd("rev-parse", f"origin/{branch}")
        snoozed_hash = remote_hash

    if snoozed_hash:
        set_config("module_update", "snoozed_hash", snoozed_hash)

    await event.answer("⏳ Обновление отложено")
    hint_text = (
        "⏳ **Обновление отложено.**\n\n"
        "Бот больше не будет напоминать об этой версии (до выхода следующего нового обновления).\n\n"
        "💡 **Подсказка:** Чтобы обновиться в любое время, "
        "используйте команду `.update` в любом чате или нажмите кнопку ниже."
    )
    buttons = [[Button.inline("🚀 Обновить сейчас", b"bot_upd_apply")]]
    try:
        await event.edit(hint_text, buttons=buttons)
    except errors.MessageNotModifiedError:
        pass


# ==========================================
# ФОНОВАЯ ЗАДАЧА: АВТО-ПРОВЕРКА РАЗ В 15 МИНУТ
# ==========================================

@register_bg()
async def auto_update_checker(client):
    """
    Фоновый процесс: каждые 15 минут проверяет наличие обновлений в репозитории.
    При обнаружении отправляет сообщение владельцу от имени встроенного бота с кнопками.
    """
    logger.debug("Запуск фонового чекера авто-обновлений...")
    # Ждем 60 секунд после запуска, чтобы ядро успело полностью инициализироваться
    await asyncio.sleep(60)
    last_notified_hash = None

    while True:
        try:
            ready, _ = await ensure_git_setup()
            if ready:
                branch = await get_current_branch()
                code, _, _ = await run_git_cmd("fetch", "origin", branch, timeout=30)
                if code == 0:
                    code_remote, remote_hash, _ = await run_git_cmd("rev-parse", f"origin/{branch}")
                    code_local, local_hash, _ = await run_git_cmd("rev-parse", "HEAD")

                    code_cnt, behind_str, _ = await run_git_cmd("rev-list", "--count", f"HEAD..origin/{branch}")
                    behind_count = int(behind_str) if (code_cnt == 0 and behind_str.isdigit()) else 0

                    snoozed_hash = get_config("module_update", "snoozed_hash", "")
                    logger.debug(f"Фоновый чек: behind={behind_count}, remote={remote_hash[:7] if remote_hash else 'None'}, snoozed={snoozed_hash[:7] if snoozed_hash else 'None'}")

                    # Проверяем:
                    # 1. Есть коммиты позади (behind_count > 0)
                    # 2. remote_hash валидный и не равен локальному
                    # 3. remote_hash не совпадает с отложенной версией (snoozed_hash)
                    # 4. remote_hash еще не был отправлен в текущей сессии (last_notified_hash)
                    if (
                        behind_count > 0
                        and remote_hash
                        and remote_hash != local_hash
                        and remote_hash != snoozed_hash
                        and remote_hash != last_notified_hash
                    ):
                        logger.info(f"Обнаружено {behind_count} новых коммитов в GitHub! Отправка уведомления владельцу...")
                        code_log, new_commits_log, _ = await run_git_cmd(
                            "log",
                            f"HEAD..origin/{branch}",
                            "--pretty=format:• `[%h]` **%s** *(%an)*",
                            "-n",
                            "5"
                        )
                        commits_display = new_commits_log if (code_log == 0 and new_commits_log) else "• Новые изменения в репозитории"
                        if behind_count > 5:
                            commits_display += f"\n*...и еще {behind_count - 5} коммитов*"

                        msg = (
                            f"🔔 **Доступно обновление UBTG!**\n\n"
                            f"🌿 **Ветка:** `{branch}`\n"
                            f"🔢 **Новых коммитов:** `{behind_count}`\n\n"
                            f"📋 **Список изменений:**\n"
                            f"{commits_display}\n\n"
                            f"💡 **Выберите действие:**"
                        )

                        buttons = [
                            [
                                Button.inline("🚀 Обновить", b"bot_upd_apply"),
                                Button.inline("⏳ Отложить", f"bot_upd_snooze_{remote_hash}".encode())
                            ]
                        ]

                        bot = get_bot()
                        owner_id = get_owner_id()
                        if bot and owner_id:
                            try:
                                await bot.send_message(owner_id, msg, buttons=buttons)
                                last_notified_hash = remote_hash
                                logger.info("Уведомление об обновлении отправлено в ЛС боту владельца.")
                            except Exception as ex:
                                logger.warning(f"Ошибка отправки уведомления ботом: {ex}")
        except Exception as e:
            logger.error(f"Ошибка в фоновом чеке обновлений: {e}")

        # Проверка каждые 15 минут
        await asyncio.sleep(15 * 60)


# ==========================================
# ОСНОВНАЯ КОМАНДА .UPDATE
# ==========================================

@register_cmd("update", desc="Проверить или установить обновления юзербота (.update / .update now / .update force)")
async def update_cmd(client, event, args):
    """
    Команда управления обновлениями юзербота из GitHub.
    Использование:
      .update — проверить наличие обновлений (интерактивный инлайн режим)
      .update now — мгновенно скачать обновления и перезапустить бота
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
            "• `.update` — интерактивная проверка обновлений с инлайн-кнопками\n"
            "• `.update now` (или `.update pull`) — мгновенно скачать обновление и перезапустить бота\n"
            "• `.update force` (или `.update -f`) — принудительно обновить (сбросить локальные конфликты)\n"
            "• `.update log [число]` — показать историю последних коммитов (по умолчанию 10)\n"
            "• `.version` — текущая версия и информация о коммите\n\n"
            "⏰ **Авто-проверка:** Каждые 15 минут в фоне бот проверяет обновления и присылает уведомление в ЛС.\n\n"
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
        await run_update_sequence(client, event.chat_id, event.id, branch, force=True, event=event)
        return

    # --- РЕЖИМ 3: ПРЯМОЕ ОБНОВЛЕНИЕ БЕЗ КНОПОК (.update now / pull / apply) ---
    if subcmd in ("now", "pull", "apply", "upgrade"):
        await run_update_sequence(client, event.chat_id, event.id, branch, force=False, event=event)
        return

    # --- РЕЖИМ 4: ИНТЕРАКТИВНАЯ ПРОВЕРКА ОБНОВЛЕНИЙ (.update / .update check) ---
    await event.edit("🔄 `Проверяю наличие обновлений на GitHub...`")

    state = await check_updates_state()
    if not state.get("ok"):
        return await event.edit(f"❌ **Ошибка проверки обновлений:**\n`{state.get('error')}`")

    text, buttons = build_update_ui(state)

    # Отправляем через инлайн с кнопками
    try:
        await send_inline(
            client,
            event.chat_id,
            text,
            buttons=buttons,
            reply_to=event.message.reply_to_msg_id
        )
        await event.delete()
    except Exception as inline_ex:
        # Резервный вариант, если бот или инлайн недоступен
        print(f"[Update] send_inline fallback: {inline_ex}")
        await event.edit(text)


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

