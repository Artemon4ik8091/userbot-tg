# requires: 
# ---------------------------------------------------------------------------------
# Name: Backup & Restore
# Description: Системный модуль для создания бекапов пользовательских модулей
#              и базы данных (Global_config.json / core_conf.json) в ZIP-архив
#              и быстрого восстановления из файла с автоустановкой зависимостей.
# ---------------------------------------------------------------------------------

import os
import sys
import json
import time
import zipfile
import asyncio
import shutil
import traceback
import re
from datetime import datetime

from telethon import errors, events

from registry import (
    register_cmd,
    set_module_meta,
    init_config,
    get_config,
    set_config,
    get_logger,
    get_prefix,
    restart_userbot,
    load_config,
    save_config,
    get_cache_dir,
    get_backups_dir,
    get_cache_size,
    clear_cache,
    get_bot_username,
    modules_repo,
    BASE_DIR,
    MODULES_DIR,
    CONFIG_FILE,
    CORE_CONFIG_FILE
)

logger = get_logger("Backup")

set_module_meta(
    name="Backup",
    desc="Резервное копирование и восстановление модулей и базы данных в ZIP-архив.",
    system=True
)

init_config("backup", {
    "auto_backup_before_restore": True,
    "max_backups_keep": 10
})


def format_size(bytes_val: int) -> str:
    """Форматирует байты в читаемый формат (Б, КБ, МБ, ГБ)."""
    if bytes_val < 1024:
        return f"{bytes_val} Б"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} КБ"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.2f} МБ"
    else:
        return f"{bytes_val / (1024 * 1024 * 1024):.2f} ГБ"


def get_module_requires(filepath: str) -> list:
    """Извлекает список pip-зависимостей из шапки модуля (# requires: ...)."""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for _ in range(30):
                line = f.readline()
                if not line:
                    break
                match = re.search(r"^\s*#\s*requires:\s*(.+)$", line, re.IGNORECASE)
                if match:
                    parts = [d.strip() for d in re.split(r"[\s,]+", match.group(1)) if d.strip()]
                    return parts
    except Exception:
        pass
    return []


async def pip_install(package_name: str) -> bool:
    """Асинхронная установка пакета через pip."""
    logger.info(f"Установка зависимости {package_name} через pip...")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", package_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        return proc.returncode == 0
    except Exception as e:
        logger.error(f"Ошибка установки {package_name}: {e}")
        return False


def prune_old_backups(keep_count: int = 10):
    """Удаляет старые локальные бэкапы, превышающие лимит."""
    backups_dir = get_backups_dir()
    if not os.path.exists(backups_dir):
        return
    zip_files = []
    for f in os.listdir(backups_dir):
        if f.endswith(".zip"):
            fp = os.path.join(backups_dir, f)
            try:
                zip_files.append((fp, os.path.getmtime(fp)))
            except OSError:
                pass
    if len(zip_files) > keep_count:
        zip_files.sort(key=lambda x: x[1])
        to_delete = zip_files[:len(zip_files) - keep_count]
        for fp, _ in to_delete:
            try:
                os.remove(fp)
                logger.info(f"Удален старый бэкап: {os.path.basename(fp)}")
            except OSError:
                pass


def create_backup_archive(custom_label: str = None) -> tuple:
    """
    Создает ZIP-архив с резервной копией пользовательских модулей и базы данных.
    Возвращает (путь_к_архиву: str, manifest: dict).
    """
    backups_dir = get_backups_dir()
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d_%H-%M-%S")
    date_human = now.strftime("%d.%m.%Y %H:%M:%S")

    label_part = f"_{custom_label}" if custom_label else ""
    filename = f"ubtg_backup_{date_str}{label_part}.zip"
    archive_path = os.path.join(backups_dir, filename)

    modules_to_pack = []
    if os.path.exists(MODULES_DIR):
        for root, dirs, files in os.walk(MODULES_DIR):
            if "__pycache__" in root:
                continue
            for f in files:
                if f.endswith(".py") and not f.startswith("."):
                    full_path = os.path.join(root, f)
                    rel_path = os.path.relpath(full_path, MODULES_DIR)
                    sz = os.path.getsize(full_path)
                    reqs = get_module_requires(full_path)
                    modules_to_pack.append({
                        "file": f,
                        "rel_path": rel_path,
                        "full_path": full_path,
                        "size": sz,
                        "requires": reqs
                    })

    has_global_cfg = os.path.exists(CONFIG_FILE)
    has_core_cfg = os.path.exists(CORE_CONFIG_FILE)

    other_db_files = []
    for f in os.listdir(BASE_DIR):
        if f.endswith((".db", ".sqlite", ".sqlite3")) and os.path.isfile(os.path.join(BASE_DIR, f)):
            other_db_files.append(f)

    manifest = {
        "format": "ubtg_backup",
        "version": "1.0",
        "created_at": now.isoformat(),
        "created_at_human": date_human,
        "bot_username": get_bot_username() or "unknown",
        "modules_count": len(modules_to_pack),
        "modules": [
            {
                "file": m["rel_path"],
                "size": m["size"],
                "requires": m["requires"]
            }
            for m in modules_to_pack
        ],
        "database": {
            "has_global_config": has_global_cfg,
            "has_core_conf": has_core_cfg,
            "other_db_files": other_db_files
        }
    }

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=4, ensure_ascii=False))

        for m in modules_to_pack:
            arcname = f"modules/{m['rel_path']}"
            zf.write(m["full_path"], arcname=arcname)

        if has_global_cfg:
            zf.write(CONFIG_FILE, arcname="database/Global_config.json")

        if has_core_cfg:
            try:
                with open(CORE_CONFIG_FILE, "r", encoding="utf-8") as f:
                    core_data = json.load(f)
                safe_core = {
                    "cmd_prefix": core_data.get("cmd_prefix", "."),
                    "log_chat_id": core_data.get("log_chat_id"),
                    "proxy": core_data.get("proxy")
                }
                zf.writestr("database/core_conf.json", json.dumps(safe_core, indent=4, ensure_ascii=False))
            except Exception:
                zf.write(CORE_CONFIG_FILE, arcname="database/core_conf.json")

        for odb in other_db_files:
            odb_path = os.path.join(BASE_DIR, odb)
            if os.path.exists(odb_path):
                zf.write(odb_path, arcname=f"database/{odb}")

    max_keep = get_config("backup", "max_backups_keep", 10)
    prune_old_backups(max_keep)

    return archive_path, manifest


def is_safe_zip_path(target_dir: str, path: str) -> bool:
    """Защита от Zip Slip уязвимости (path traversal)."""
    resolved_target = os.path.abspath(target_dir)
    resolved_path = os.path.abspath(os.path.join(target_dir, path))
    return resolved_path.startswith(resolved_target + os.sep) or resolved_path == resolved_target


def read_manifest_from_zip(zip_path: str) -> dict:
    """Считывает manifest.json из ZIP-архива, если он есть."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if "manifest.json" in zf.namelist():
                data = zf.read("manifest.json").decode("utf-8", errors="ignore")
                return json.loads(data)
    except Exception:
        pass
    return {}


async def perform_restore(client, event, zip_path: str, only_modules: bool = False, only_db: bool = False):
    """
    Выполняет восстановление модулей и/или базы данных из указанного ZIP-архива.
    """
    p = get_prefix()
    logger.info(f"Начало восстановления из {zip_path} (only_modules={only_modules}, only_db={only_db})")

    if not os.path.exists(zip_path) or not zipfile.is_zipfile(zip_path):
        return await event.edit("❌ **Ошибка:** Файл поврежден или не является валидным ZIP-архивом!")

    restore_temp = get_cache_dir("restore_temp")
    if os.path.exists(restore_temp):
        shutil.rmtree(restore_temp, ignore_errors=True)
    os.makedirs(restore_temp, exist_ok=True)

    try:
        await event.edit("⏳ **Проверка содержимого архива бэкапа...**")
        manifest = read_manifest_from_zip(zip_path)

        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.namelist():
                if not is_safe_zip_path(restore_temp, member):
                    return await event.edit("❌ **Ошибка безопасности:** Обнаружен опасный путь (Zip Slip) внутри архива!")

            zf.extractall(restore_temp)

        auto_backup = get_config("backup", "auto_backup_before_restore", True)
        if auto_backup:
            try:
                create_backup_archive(custom_label="pre_restore_safety")
            except Exception as e_pre:
                logger.warning(f"Не удалось создать точку отката pre_restore: {e_pre}")

        restored_modules_count = 0
        installed_deps = []
        db_restored = False
        core_restored = False

        # --- 1. ВОССТАНОВЛЕНИЕ МОДУЛЕЙ ---
        if not only_db:
            await event.edit("📦 **Восстановление пользовательских модулей...**")
            os.makedirs(MODULES_DIR, exist_ok=True)

            # Ищем папку modules/ внутри распакованного архива
            extracted_modules_dir = os.path.join(restore_temp, "modules")
            candidate_files = []

            if os.path.exists(extracted_modules_dir):
                for root, dirs, files in os.walk(extracted_modules_dir):
                    for f in files:
                        if f.endswith(".py") and not f.startswith("."):
                            full_src = os.path.join(root, f)
                            rel_p = os.path.relpath(full_src, extracted_modules_dir)
                            candidate_files.append((full_src, rel_p))
            else:
                # Если бэкап был без папки modules/, смотрим корень распаковки
                for f in os.listdir(restore_temp):
                    if f.endswith(".py") and f not in ("userbot.py", "registry.py"):
                        candidate_files.append((os.path.join(restore_temp, f), f))

            for src_path, rel_p in candidate_files:
                dest_path = os.path.join(MODULES_DIR, rel_p)
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                shutil.copy2(src_path, dest_path)
                restored_modules_count += 1

                reqs = get_module_requires(dest_path)
                for req in reqs:
                    if req not in installed_deps:
                        installed_deps.append(req)

            # Установка зависимостей, если модули их требуют
            if installed_deps:
                await event.edit(f"⚙️ **Проверка зависимостей ({len(installed_deps)} шт.)...**")
                for pkg in installed_deps:
                    try:
                        __import__(pkg.replace("-", "_"))
                    except ImportError:
                        await event.edit(f"📦 Установка необходимой библиотеки: `{pkg}`...")
                        await pip_install(pkg)

        # --- 2. ВОССТАНОВЛЕНИЕ БАЗЫ ДАННЫХ (CONFIG) ---
        if not only_modules:
            await event.edit("💾 **Восстановление базы данных и настроек...**")

            # 2.1 Global_config.json
            gcfg_candidates = [
                os.path.join(restore_temp, "database", "Global_config.json"),
                os.path.join(restore_temp, "Global_config.json")
            ]
            for gpath in gcfg_candidates:
                if os.path.exists(gpath):
                    try:
                        with open(gpath, "r", encoding="utf-8") as f:
                            restored_data = json.load(f)
                        if isinstance(restored_data, dict):
                            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                                json.dump(restored_data, f, indent=4, ensure_ascii=False)
                            load_config()
                            db_restored = True
                            break
                    except Exception as e_cfg:
                        logger.error(f"Ошибка при импорте Global_config.json: {e_cfg}")

            # 2.2 core_conf.json (безопасное обновление префикса и кастомных настроек)
            core_candidates = [
                os.path.join(restore_temp, "database", "core_conf.json"),
                os.path.join(restore_temp, "core_conf.json")
            ]
            for cpath in core_candidates:
                if os.path.exists(cpath):
                    try:
                        with open(cpath, "r", encoding="utf-8") as f:
                            new_core = json.load(f)
                        if isinstance(new_core, dict) and os.path.exists(CORE_CONFIG_FILE):
                            with open(CORE_CONFIG_FILE, "r", encoding="utf-8") as f:
                                cur_core = json.load(f)
                            if "cmd_prefix" in new_core and new_core["cmd_prefix"]:
                                cur_core["cmd_prefix"] = new_core["cmd_prefix"]
                            if "proxy" in new_core and new_core["proxy"]:
                                cur_core["proxy"] = new_core["proxy"]
                            with open(CORE_CONFIG_FILE, "w", encoding="utf-8") as f:
                                json.dump(cur_core, f, indent=4, ensure_ascii=False)
                            core_restored = True
                            break
                    except Exception as e_core:
                        logger.error(f"Ошибка при обновлении core_conf: {e_core}")

            # 2.3 Дополнительные файлы баз данных (*.db, *.sqlite)
            db_dir = os.path.join(restore_temp, "database")
            if os.path.exists(db_dir):
                for f in os.listdir(db_dir):
                    if f.endswith((".db", ".sqlite", ".sqlite3")):
                        src_db = os.path.join(db_dir, f)
                        dst_db = os.path.join(BASE_DIR, f)
                        try:
                            shutil.copy2(src_db, dst_db)
                        except Exception as e_db:
                            logger.error(f"Ошибка восстановления {f}: {e_db}")

        # Формируем итоговое сообщение об успехе
        details = []
        if not only_db:
            details.append(f"• Восстановлено модулей: `{restored_modules_count}`")
        if db_restored:
            details.append("• База данных модулей: `Обновлена`")
        if core_restored:
            details.append("• Конфигурация ядра: `Применена`")
        if installed_deps:
            details.append(f"• Установлено зависимостей: `{len(installed_deps)}`")

        details_str = "\n".join(details) if details else "• Изменения применены"
        success_msg = (
            f"🎉 **Резервная копия успешно восстановлена!**\n\n"
            f"{details_str}\n\n"
            f"🔄 **Юзербот перезапущен и готов к работе.**"
        )

        await event.edit("🔄 **Применение изменений и перезапуск юзербота...**")
        await restart_userbot(client, event.chat_id, event.id, custom_text=success_msg)

    except Exception as e:
        tb = traceback.format_exc()
        truncated_tb = tb[-600:] if len(tb) > 600 else tb
        logger.error(f"Критическая ошибка восстановления:\n{tb}")
        await event.edit(
            f"❌ **Критическая ошибка при восстановлении!**\n\n"
            f"ℹ️ **Причина:** `{e}`\n\n"
            f"📋 **Traceback:**\n`...{truncated_tb}`"
        )
    finally:
        if os.path.exists(restore_temp):
            shutil.rmtree(restore_temp, ignore_errors=True)


@register_cmd("backup", desc="Создать резервную копию модулей и БД (.backup [me/chat] [название])")
async def backup_cmd(client, event, args):
    """
    Создает ZIP-архив с модулями и базой данных, сохраняет локально
    и отправляет файл в Telegram.
    """
    p = get_prefix()
    raw_args = (args or "").strip()
    arg_tokens = raw_args.split()

    # Обработка подкоманд
    if arg_tokens and arg_tokens[0].lower() in ("list", "all"):
        return await list_backups_cmd(client, event, " ".join(arg_tokens[1:]))

    if arg_tokens and arg_tokens[0].lower() in ("clean", "clear"):
        return await clean_backups_cmd(client, event, " ".join(arg_tokens[1:]))

    if arg_tokens and arg_tokens[0].lower() in ("delete", "rm", "del"):
        return await delete_backup_cmd(client, event, " ".join(arg_tokens[1:]))

    if arg_tokens and arg_tokens[0].lower() == "send":
        target_name = arg_tokens[1] if len(arg_tokens) > 1 else ""
        return await send_backup_file_cmd(client, event, target_name)

    # Определяем цель отправки (текущий чат или 'me' - Избранное)
    send_to_me = False
    custom_label = None

    filtered_tokens = []
    for token in arg_tokens:
        if token.lower() in ("me", "saved", "fav"):
            send_to_me = True
        else:
            filtered_tokens.append(token)

    if filtered_tokens:
        custom_label = re.sub(r'[^a-zA-Z0-9_\-]', '_', "_".join(filtered_tokens))[:30]

    await event.edit("⏳ **Создание резервной копии модулей и базы данных...**")

    try:
        archive_path, manifest = create_backup_archive(custom_label=custom_label)
        archive_size = os.path.getsize(archive_path)
        formatted_size = format_size(archive_size)

        mod_names = [os.path.splitext(m["file"])[0] for m in manifest.get("modules", [])]
        if len(mod_names) > 5:
            mod_preview = ", ".join(f"`{m}`" for m in mod_names[:5]) + f" ...и еще `{len(mod_names) - 5}`"
        elif mod_names:
            mod_preview = ", ".join(f"`{m}`" for m in mod_names)
        else:
            mod_preview = "Пользовательские модули не установлены"

        caption = (
            f"📦 **Резервная копия UBTG готова!**\n\n"
            f"📅 **Дата:** `{manifest.get('created_at_human')}`\n"
            f"📁 **Модули ({manifest.get('modules_count')} шт.):**\n{mod_preview}\n\n"
            f"💾 **База данных:** `Global_config.json`\n"
            f"⚙️ **Настройки ядра:** `core_conf.json`\n"
            f"📊 **Размер архива:** `{formatted_size}`\n\n"
            f"💡 **Восстановление:**\n"
            f"Сделайте реплай на этот файл с командой `{p}restore`"
        )

        target_chat = "me" if send_to_me else event.chat_id

        await client.send_file(
            target_chat,
            archive_path,
            caption=caption
        )

        if send_to_me and event.chat_id != "me":
            await event.edit(
                f"✅ **Резервная копия успешно создана и отправлена в Избранное!**\n\n"
                f"📊 **Размер:** `{formatted_size}`\n"
                f"📁 **Файл:** `{os.path.basename(archive_path)}`"
            )
        else:
            await event.delete()

    except Exception as e:
        tb = traceback.format_exc()
        truncated_tb = tb[-500:] if len(tb) > 500 else tb
        logger.error(f"Ошибка создания бэкапа:\n{tb}")
        await event.edit(f"❌ **Не удалось создать бэкап:** `{e}`\n\n`...{truncated_tb}`")


@register_cmd("dump", desc="Алиас для команды .backup")
async def dump_alias_cmd(client, event, args):
    """Алиас для .backup"""
    await backup_cmd(client, event, args)


@register_cmd("restore", desc="Восстановить из архива бэкапа (реплай на zip или .restore <файл>)")
async def restore_cmd(client, event, args):
    """
    Восстанавливает модули и конфигурацию из прикрепленного файла или локального архива.
    """
    p = get_prefix()
    raw_args = (args or "").strip()
    arg_tokens = raw_args.split()

    only_modules = any(t in ("--only-modules", "-m", "modules") for t in arg_tokens)
    only_db = any(t in ("--only-db", "-db", "db", "config") for t in arg_tokens)

    # Проверяем, сделан ли реплай на сообщение с файлом
    reply_msg = await event.get_reply_message()

    if reply_msg and reply_msg.file:
        file_name = reply_msg.file.name or ""
        if not file_name.lower().endswith((".zip", ".tar.gz", ".tar")):
            return await event.edit("❌ **Ошибка:** Прикрепленный файл должен быть архивом (`.zip`)!")

        await event.edit("⏳ **Скачиваю архив бэкапа из сообщения...**")
        download_dir = get_cache_dir("restore_download")
        zip_path = os.path.join(download_dir, "incoming_backup.zip")

        try:
            await reply_msg.download_media(file=zip_path)
            await perform_restore(client, event, zip_path, only_modules=only_modules, only_db=only_db)
        finally:
            if os.path.exists(download_dir):
                shutil.rmtree(download_dir, ignore_errors=True)
        return

    # Если реплая нет, проверяем аргументы (имя файла или 'latest')
    file_arg = next((t for t in arg_tokens if not t.startswith("-")), None)

    if file_arg:
        backups_dir = get_backups_dir()
        target_path = None

        if file_arg.lower() == "latest":
            zips = [os.path.join(backups_dir, f) for f in os.listdir(backups_dir) if f.endswith(".zip")]
            if not zips:
                return await event.edit("❌ **Локальные бэкапы не найдены!** Сначала создайте бэкап: `.backup`")
            zips.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            target_path = zips[0]
        else:
            cand = os.path.join(backups_dir, file_arg)
            if not cand.endswith(".zip") and os.path.exists(cand + ".zip"):
                cand += ".zip"
            if os.path.exists(cand):
                target_path = cand

        if target_path and os.path.exists(target_path):
            return await perform_restore(client, event, target_path, only_modules=only_modules, only_db=only_db)
        else:
            return await event.edit(
                f"❌ **Файл `{file_arg}` не найден в папке локальных бэкапов!**\n\n"
                f"💡 Список доступных бэкапов: `{p}backups`"
            )

    # Если ни реплая, ни аргументов нет — выводим красивую справку
    await event.edit(
        f"ℹ️ **Как восстановить юзербота из бэкапа:**\n\n"
        f"1️⃣ **Через реплай:**\n"
        f"   Отправьте реплай на файл архива (`.zip`) командой `{p}restore`\n\n"
        f"2️⃣ **Из локального списка:**\n"
        f"   `{p}restore latest` — восстановить последний локальный бэкап\n"
        f"   `{p}restore <имя_файла.zip>` — восстановить конкретный бэкап\n\n"
        f"📌 **Дополнительные параметры:**\n"
        f"• `{p}restore --only-modules` — восстановить только модули (без БД)\n"
        f"• `{p}restore --only-db` — восстановить только базу данных (без файлов модулей)\n\n"
        f"🗄 Список сохраненных бэкапов: `{p}backups`"
    )


@register_cmd("backups", desc="Список локальных резервных копий")
async def list_backups_cmd(client, event, args):
    """Отображает список всех локально сохраненных архивов бэкапа."""
    p = get_prefix()
    backups_dir = get_backups_dir()

    if not os.path.exists(backups_dir):
        return await event.edit("🗄 **Локальных резервных копий пока нет.**\nСоздайте первый бэкап: `.backup`")

    zip_files = []
    for f in os.listdir(backups_dir):
        if f.endswith(".zip"):
            fp = os.path.join(backups_dir, f)
            try:
                zip_files.append((f, os.path.getsize(fp), os.path.getmtime(fp), fp))
            except OSError:
                pass

    if not zip_files:
        return await event.edit("🗄 **Локальных резервных копий пока нет.**\nСоздайте первый бэкап: `.backup`")

    zip_files.sort(key=lambda x: x[2], reverse=True)

    text = f"🗄 **Локальные резервные копии ({len(zip_files)} шт.):**\n\n"

    for idx, (fname, sz, mtime, fp) in enumerate(zip_files[:15], start=1):
        dt_str = datetime.fromtimestamp(mtime).strftime("%d.%m.%Y %H:%M")
        size_str = format_size(sz)

        mf = read_manifest_from_zip(fp)
        mod_cnt = mf.get("modules_count")
        mod_info = f" • `{mod_cnt} мод.`" if mod_cnt is not None else ""

        text += f"**{idx}.** `{fname}`\n    └ ⏱ `{dt_str}` | 📊 `{size_str}`{mod_info}\n"

    text += (
        f"\n🛠 **Команды управления:**\n"
        f"• `{p}restore <имя_файла>` — восстановить выбранный бэкап\n"
        f"• `{p}restore latest` — восстановить самый свежий бэкап\n"
        f"• `{p}backup send <имя_файла>` — отправить файл в текущий чат\n"
        f"• `{p}backup delete <имя_файла>` — удалить конкретный бэкап\n"
        f"• `{p}backup clean` — очистить старые копии"
    )

    await event.edit(text)


async def send_backup_file_cmd(client, event, file_name):
    """Отправляет конкретный локальный файл бэкапа в чат."""
    if not file_name:
        return await event.edit("❌ **Укажите имя файла бэкапа:** `.backup send <имя_файла>`")

    backups_dir = get_backups_dir()
    target_path = os.path.join(backups_dir, file_name)
    if not target_path.endswith(".zip") and os.path.exists(target_path + ".zip"):
        target_path += ".zip"

    if not os.path.exists(target_path):
        return await event.edit(f"❌ **Файл `{file_name}` не найден!** Посмотрите список: `.backups`")

    await event.edit("📤 **Отправляю файл резервной копии...**")
    try:
        await client.send_file(
            event.chat_id,
            target_path,
            caption=f"📦 **Резервная копия UBTG:** `{os.path.basename(target_path)}`"
        )
        await event.delete()
    except Exception as e:
        await event.edit(f"❌ **Ошибка при отправке файла:** `{e}`")


async def delete_backup_cmd(client, event, file_name):
    """Удаляет конкретный бэкап из папки backups."""
    if not file_name:
        return await event.edit("❌ **Укажите имя файла для удаления:** `.backup delete <имя_файла>`")

    backups_dir = get_backups_dir()
    target_path = os.path.join(backups_dir, file_name)
    if not target_path.endswith(".zip") and os.path.exists(target_path + ".zip"):
        target_path += ".zip"

    if not os.path.exists(target_path):
        return await event.edit(f"❌ **Файл `{file_name}` не найден!**")

    try:
        os.remove(target_path)
        await event.edit(f"🗑 **Бэкап `{os.path.basename(target_path)}` успешно удален.**")
    except Exception as e:
        await event.edit(f"❌ **Не удалось удалить файл:** `{e}`")


async def clean_backups_cmd(client, event, args):
    """Удаляет все локальные бэкапы кроме последнего."""
    backups_dir = get_backups_dir()
    if not os.path.exists(backups_dir):
        return await event.edit("🗄 **Папка бэкапов пуста.**")

    zip_files = []
    for f in os.listdir(backups_dir):
        if f.endswith(".zip"):
            fp = os.path.join(backups_dir, f)
            try:
                zip_files.append((fp, os.path.getmtime(fp)))
            except OSError:
                pass

    if len(zip_files) <= 1:
        return await event.edit("ℹ️ **Очистка не требуется:** в хранилище 1 или менее бэкапов.")

    zip_files.sort(key=lambda x: x[1])
    to_delete = zip_files[:-1]
    deleted_count = 0

    for fp, _ in to_delete:
        try:
            os.remove(fp)
            deleted_count += 1
        except OSError:
            pass

    await event.edit(f"🧹 **Очистка завершена!** Удалено старых бэкапов: `{deleted_count}`. Последний сохранен.")


@register_cmd("cache", desc="Управление общим кэшем юзербота (.cache / .cache clear)")
async def cache_cmd(client, event, args):
    """
    Показывает размер общего кэша юзербота и подпапок модулей,
    а также позволяет очистить кэш.
    """
    p = get_prefix()
    arg = (args or "").strip().lower()

    if arg in ("clear", "clean", "flush", "delete", "rm"):
        return await clear_cache_cmd(client, event, "")

    if arg.startswith("clear ") or arg.startswith("clean "):
        target_module = arg.split(maxsplit=1)[1]
        return await clear_cache_cmd(client, event, target_module)

    cache_dir = get_cache_dir()
    total_size = get_cache_size()

    lines = [
        "🗂 **Общий кэш модулей (папка `cache/`):**\n",
        f"📊 **Суммарный размер:** `{format_size(total_size)}`\n",
        "📁 **Разбивка по модулям:**"
    ]

    has_subdirs = False
    if os.path.exists(cache_dir):
        for item in sorted(os.listdir(cache_dir)):
            item_path = os.path.join(cache_dir, item)
            if os.path.isdir(item_path):
                sz = get_cache_size(item)
                lines.append(f"• `{item}` — `{format_size(sz)}`")
                has_subdirs = True
            elif os.path.isfile(item_path):
                sz = os.path.getsize(item_path)
                lines.append(f"• `{item}` (файл) — `{format_size(sz)}`")
                has_subdirs = True

    if not has_subdirs:
        lines.append("*(Кэш в данный момент пуст)*")

    lines.append(
        f"\n💡 **Команды очистки:**\n"
        f"• `{p}clearcache` или `{p}cache clear` — очистить весь кэш\n"
        f"• `{p}cache clear <модуль>` — очистить кэш конкретного модуля"
    )

    await event.edit("\n".join(lines))


@register_cmd("clearcache", desc="Быстрая очистка кэша всех модулей")
async def clear_cache_cmd(client, event, args):
    """Очищает весь кэш или кэш конкретного модуля."""
    target_mod = (args or "").strip()
    if target_mod:
        before_sz = get_cache_size(target_mod)
        deleted_count = clear_cache(target_mod)
        await event.edit(
            f"🧹 **Кэш модуля `{target_mod}` успешно очищен!**\n"
            f"🗑 Удалено файлов: `{deleted_count}`\n"
            f"💾 Освобождено памяти: `{format_size(before_sz)}`"
        )
    else:
        before_sz = get_cache_size()
        deleted_count = clear_cache()
        await event.edit(
            f"🧹 **Общий кэш юзербота успешно очищен!**\n"
            f"🗑 Удалено файлов: `{deleted_count}`\n"
            f"💾 Освобождено памяти: `{format_size(before_sz)}`"
        )
