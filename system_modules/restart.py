from registry import register_cmd, set_module_meta, restart_userbot

# Системный модуль — отвечает за полную перезагрузку ядра, удалять нельзя
set_module_meta(
    name="Рестарт",
    desc="Полная перезагрузка юзербота (полный перезапуск Python-процесса).",
    system=True
)

@register_cmd("restart", desc="Полностью перезапускает юзербота (ядро + все модули)")
async def restart_cmd(client, event, args):
    await event.edit("🔄 Перезагружаюсь...")
    await restart_userbot(client, event.chat_id, event.id)