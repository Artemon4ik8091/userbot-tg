from registry import register_cmd, set_module_meta, get_config, set_config, init_config
import time

set_module_meta("Ping", "Показ задержки")

# Прописываем дефолтные значения сразу при загрузке модуля
init_config(__name__, {
    "custom_reply": "ПОНГ! 🏓",
    "delay": 0
})

@register_cmd("ping", desc="Показ задержки")
async def ping_cmd(client, event, args):
    start_time = time.perf_counter()
    
    # Сначала меняем текст на промежуточный, чтобы сервер Telegram обработал запрос
    await event.edit("🔄 Измеряю...")
    
    end_time = time.perf_counter()
    
    # Считаем пинг в миллисекундах
    ping_ms = round((end_time - start_time) * 1000)
    
    # Получаем кастомный текст из конфига
    reply_text = get_config(__name__, "custom_reply")
    
    # Выводим итоговый результат
    await event.edit(f"{reply_text}\n⏱ Задержка: `{ping_ms} мс`")