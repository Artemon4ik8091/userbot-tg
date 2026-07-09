from registry import register_cmd, set_module_meta, global_config, set_config
import json

set_module_meta("Settings", "Управление глобальным файлом конфигурации")

def parse_value(val):
    """Превращает строку в нужный тип данных (число, булево значение)"""
    if val.lower() in ['true', 'on']: return True
    if val.lower() in ['false', 'off']: return False
    if val.isdigit(): return int(val)
    try:
        return float(val)
    except ValueError:
        return val # Оставляем как строку

@register_cmd("cfg", desc="Управление конфигом. Юзай: .cfg help")
async def config_manager(client, event, args):
    if not args or args.lower() == "help":
        help_text = (
            "⚙️ **Менеджер Конфигураций**\n\n"
            "Доступные команды:\n"
            "`.cfg list` — показать все текущие настройки\n"
            "`.cfg get <модуль>` — показать настройки конкретного модуля\n"
            "`.cfg set <модуль> <ключ> <значение>` — изменить параметр\n\n"
            "📌 *Пример:* `.cfg set module_ping reply_text Привет мир!`"
        )
        return await event.edit(help_text)

    parts = args.split()
    action = parts[0].lower()

    if action == "list":
        if not global_config:
            return await event.edit("⚙️ Конфиг пока пуст.")
        
        text = "⚙️ **Глобальная Конфигурация:**\n\n"
        for mod, params in global_config.items():
            text += f"📦 **{mod}**\n"
            for k, v in params.items():
                text += f"  ├ `{k}` = {repr(v)}\n"
        await event.edit(text)

    elif action == "get":
        if len(parts) < 2:
            return await event.edit("❌ Укажи имя модуля. Пример: `.cfg get module_ping`")
        
        mod_name = parts[1]
        if mod_name not in global_config or not global_config[mod_name]:
            return await event.edit(f"❌ В конфиге нет данных для модуля `{mod_name}`.")
        
        text = f"📦 **Конфиг для {mod_name}:**\n\n"
        for k, v in global_config[mod_name].items():
            text += f"• `{k}` = {repr(v)}\n"
        await event.edit(text)

    elif action == "set":
        # .cfg set <module> <key> <value>
        if len(parts) < 4:
            return await event.edit("❌ Мало аргументов. Пример: `.cfg set module_ping delay 10`")
        
        mod_name = parts[1]
        key = parts[2]
        
        # Собираем всё остальное в одну строку (если значение состоит из нескольких слов)
        raw_value = " ".join(parts[3:])
        
        # Автоматически определяем тип (число, строка, True/False)
        parsed_value = parse_value(raw_value)
        
        # Сохраняем через функцию реестра
        set_config(mod_name, key, parsed_value)
        
        await event.edit(f"✅ Успешно!\nПараметр `{key}` в модуле `{mod_name}` изменен на `{repr(parsed_value)}`.")
        
    else:
        await event.edit("❌ Неизвестное действие. Напиши `.cfg help`")