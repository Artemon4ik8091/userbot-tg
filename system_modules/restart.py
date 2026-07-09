import os
import sys
from registry import register_cmd, set_module_meta, save_restart_info

# Системный модуль — отвечает за полную перезагрузку ядра, удалять нельзя
set_module_meta(
    name="Рестарт",
    desc="Полная перезагрузка юзербота (полный перезапуск Python-процесса).",
    system=True
)

@register_cmd("restart", desc="Полностью перезапускает юзербота (ядро + все модули)")
async def restart_cmd(client, event, args):
    await event.edit("🔄 Перезагружаюсь...")

    # Сохраняем чат и id сообщения, чтобы после рестарта ядро
    # смогло отредактировать это же сообщение и подтвердить успех
    save_restart_info(event.chat_id, event.id)

    # Аккуратно отключаемся от Telegram перед перезапуском процесса
    try:
        await client.disconnect()
    except Exception:
        pass

    # Полный перезапуск процесса Python.
    # os.execv() заменяет текущий процесс новым — перечитывается АБСОЛЮТНО всё:
    # и ядро (userbot.py), и все модули, со всеми их изменениями в коде.
    python = sys.executable
    script = os.path.abspath(sys.argv[0])
    os.execv(python, [python, script] + sys.argv[1:])