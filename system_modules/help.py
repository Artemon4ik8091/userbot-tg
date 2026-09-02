from registry import register_cmd, modules_repo, set_module_meta, get_prefix

# Задаем метаданные нашего модуля (системный, нельзя удалить)
set_module_meta(
    name="Help",
    desc="Системный модуль для вывода справочной информации по командам юзербота.",
    system=True
)

@register_cmd("help", desc="Выводит список всех модулей или инфу по конкретному (.help Название)")
async def help_cmd(client, event, args):
    p = get_prefix()
    query = args.strip().lower()

    # Если просто написали .help без аргументов
    if not query:
        text = "**🤖 Список установленных модулей:**\n\n"
        for mod_id, mod_info in modules_repo["modules"].items():
            mod_name = mod_info["name"]
            is_system = mod_info.get("system", False)
            badge = " 🔒" if is_system else ""
            cmds = [f"{p}{c}" for c in mod_info["commands"].keys()]
            cmds_str = ", ".join(cmds) if cmds else "Нет команд"

            text += f"• {mod_name}{badge} 📁 `{mod_id}` ({cmds_str})\n"

        text += f"\n💡 `Введи {p}help <Название> для подробностей`"
        await event.edit(text)
        return

    # Если ищем конкретный модуль (например, .help Ping)
    found_mod = None
    found_mod_id = None  # Создаем переменную для сохранения имени файла

    for mod_id, mod_info in modules_repo["modules"].items():
        if mod_info["name"].lower() == query or mod_id.lower() == query:
            found_mod = mod_info
            found_mod_id = mod_id  # Сохраняем ID/имя файла при совпадении
            break

    if found_mod:
        is_system = found_mod.get("system", False)
        badge = " 🔒 *Системное*" if is_system else ""

        text = f"**📦 Модуль:** `{found_mod['name']}`{badge}\n"
        # Выводим имя файла
        text += f"**📁 Файл:** `{found_mod_id}`\n"
        text += f"**📖 Описание:** {found_mod['desc']}\n\n"
        text += "**🛠 Команды:**\n"

        if found_mod["commands"]:
            for cmd_name, cmd_desc in found_mod["commands"].items():
                text += f"  • `{p}{cmd_name}` — {cmd_desc}\n"
        else:
            text += "  (Нет доступных команд)\n"

        if is_system:
            text += f"\n⚠️ Этот модуль системный и защищен от удаления через `{p}uninstall`."

        await event.edit(text)
    else:
        await event.edit(f"❌ Модуль **{query}** не найден!\nНапиши `{p}help` чтобы посмотреть список.")