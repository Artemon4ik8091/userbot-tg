import asyncio
from registry import register_cmd, set_module_meta

# Устанавливаем метаданные для команды .help
set_module_meta(
    name="Терминал", 
    desc="Выполнение bash-команд прямо из Telegram",
    system=False
)

@register_cmd("terminal", desc="Выполняет команду Linux. Юзай: .terminal <команда>")
async def terminal_cmd(client, event, args):
    """
    Выполняет системную команду в Linux и выводит результат.
    Пример: .terminal neofetch
    """
    # Проверка на наличие аргументов
    if not args:
        return await event.edit("❌ Укажи команду! Пример: `.terminal neofetch`")
    
    # Изменяем исходное сообщение для отображения статуса обработки
    await event.edit(f"⏳ Выполняю команду: `{args}`...")
    
    try:
        # Асинхронно запускаем команду в системном шелле.
        # Использование asyncio гарантирует, что мы не заблокируем юзербота!
        process = await asyncio.create_subprocess_shell(
            args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Ожидаем завершения команды (с таймаутом в 60 секунд для безопасности)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60.0)
        
        # Декодируем вывод из байтов в строку, игнорируя битые символы
        output = stdout.decode('utf-8', errors='replace').strip()
        error = stderr.decode('utf-8', errors='replace').strip()
        
        # Формируем общий результат
        result_text = ""
        if output:
            result_text += output
        if error:
            # Если команда отработала, но выдала ошибки в stderr, выводим и их
            result_text += f"\n\n[STDERR (Ошибки)]:\n{error}"
            
        if not result_text:
            result_text = "[Команда выполнена успешно, но не вернула вывода]"
            
        # Защита от превышения лимита символов в одном сообщении Telegram (макс 4096 символов)
        if len(result_text) > 4000:
            result_text = result_text[:4000] + "\n... [Вывод обрезан из-за лимитов Telegram]"
        
        # Формируем итоговое сообщение. 
        # Используем блок кода (тройные обратные кавычки) - это работает как "цитата терминала" 
        # в Telegram. Это критически важно для сохранения ASCII-арта логотипа neofetch.
        final_message = f"**💻 Команда:** `{args}`\n**Результат:**\n```{result_text}```"
        
        # Путем изменения сообщения выводим конечный результат
        await event.edit(final_message)
        
    except asyncio.TimeoutError:
        # Если команда (например, ping без лимита) зависла
        await event.edit(f"❌ Команда `{args}` выполнялась слишком долго (таймаут 60с) и была прервана!")
    except Exception as e:
        # Перехват любых неожиданных сбоев
        await event.edit(f"❌ Произошла непредвиденная ошибка при выполнении: `{str(e)}`")