import os
import sys
import time
import asyncio
import importlib
from telethon import TelegramClient, events, errors
import qrcode

# Подключаем наше общее хранилище из нового файла API
from registry import modules_repo, pop_restart_info

# --- НАСТРОЙКИ ---
API_ID = 26907307
API_HASH = '93d332b9cc58759b6bc4265f2a71f0c9'

# Прокси (если нужен, оставь. Если дома все работает без него — можно закомментить)
proxy_config = {
    'proxy_type': 'http',
    'addr': '127.0.0.1',
    'port': 2080
}

# Инициализируем клиента
client = TelegramClient(
    'my_account',
    API_ID,
    API_HASH,
    device_model="MacBook Pro",
    system_version="macOS 14.5",
    app_version="10.11.1"
    # proxy=proxy_config  <--- ПРОСТО УБРАЛИ ЭТУ СТРОЧКУ НАХУЙ
)

def load_modules():
    """Динамически подгружает все .py файлы из папок system_modules и modules"""
    base_dir = os.path.dirname(__file__)
    
    # Словарь папок: { 'Имя_папки': является_ли_она_системной }
    folders_to_load = {
        'system_modules': True,
        'modules': False
    }

    for folder_name, is_system in folders_to_load.items():
        folder_path = os.path.join(base_dir, folder_name)
        
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
            print(f"[Core] 📁 Создана папка для модулей: {folder_name}/")
            continue

        # Добавляем папку в sys.path, чтобы импорты из неё работали ровно
        if folder_path not in sys.path:
            sys.path.insert(0, folder_path)

        for file in os.listdir(folder_path):
            if file.endswith('.py') and not file.startswith('__'):
                module_name = file[:-3]
                try:
                    # Загружаем модуль
                    importlib.import_module(module_name)
                    
                    # Пиздатая фича: принудительно ставим статус "системный", 
                    # если модуль лежит в папке system_modules, даже если
                    # ты забыл вписать system=True в самом коде модуля.
                    if is_system:
                        if module_name in modules_repo["modules"]:
                            modules_repo["modules"][module_name]["system"] = True
                        else:
                            modules_repo["modules"][module_name] = {
                                "name": module_name.capitalize(),
                                "desc": "Системный модуль (без описания)",
                                "commands": {},
                                "system": True
                            }
                    
                    icon = "⚙️" if is_system else "📦"
                    print(f"[Core] {icon} Модуль '{module_name}' успешно загружен!")
                except Exception as e:
                    print(f"[Core] ❌ Пиздец, не удалось загрузить модуль '{module_name}': {e}")

async def notify_after_restart():
    """
    Проверяет, не был ли этот запуск вызван командой .restart.
    Если да — редактирует то самое сообщение, откуда рестарт был вызван,
    подтверждая, что юзербот успешно поднялся, и показывает время простоя.
    """
    restart_info = pop_restart_info()
    if not restart_info:
        return

    try:
        elapsed = time.time() - restart_info.get("time", time.time())
        await client.edit_message(
            restart_info["chat_id"],
            restart_info["message_id"],
            f"✅ Успешно перезагружен!\n⏱ Заняло: `{elapsed:.2f} сек.`"
        )
        print("[Core] Уведомление о перезагрузке отправлено.")
    except Exception as e:
        print(f"[Core] ⚠️ Не удалось отредактировать сообщение после рестарта: {e}")

async def handle_incoming_messages(event):
    """Слушает сообщения и триггерит команды модулей"""
    # Ловим только наши исходящие сообщения
    if not event.out:
        return

    text = event.raw_text
    print(f"[Debug] Отправлено сообщение: '{text}'")

    # Проверяем, команда ли это
    if text.startswith('.'):
        parts = text.split(maxsplit=1)
        cmd = parts[0][1:]  # Убираем точку
        args = parts[1] if len(parts) > 1 else ""

        print(f"[Debug] Обнаружена команда: .{cmd} с аргументами: '{args}'")

        if cmd in modules_repo["commands"]:
            try:
                # Запускаем функцию из привязанного модуля
                await modules_repo["commands"][cmd](client, event, args)
                print(f"[Debug] Команда .{cmd} успешно выполнена!")
            except Exception as e:
                print(f"[Debug] ❌ Ошибка при выполнении .{cmd}: {e}")
                await event.edit(f"**Ошибка в модуле [.{cmd}]:**\n`{e}`")

async def main():
    await client.connect()

    if not await client.is_user_authorized():
        print("=== Запуск генерации QR-кода ===")
        qr_login = await client.qr_login()
        qr = qrcode.QRCode()
        qr.add_data(qr_login.url)
        print("\n" + "="*60)
        qr.print_tty()
        print("="*60 + "\n")
        try:
            await qr_login.wait(timeout=60)
            print("Ура, бля! Успешно залогинились!")
        except errors.SessionPasswordNeededError:
            password = input("У тебя включен облачный пароль (2FA). Введи его сюда: ")
            await client.sign_in(password=password)
        except Exception as e:
            print(f"Ошибка при входе: {e}")
            await client.disconnect()
            return

    print("\n[Core] Загружаем модули...")
    load_modules()

    # Вешаем слушатель на отправляемые сообщения
    client.add_event_handler(handle_incoming_messages, events.NewMessage(outgoing=True))

    print(f"\n[Core] Запущено команд: {len(modules_repo['commands'])}")
    print(f"[Core] Запущено фоновых задач: {len(modules_repo['background_tasks'])}")
    print("\n=== Юзербот полностью готов к работе! ===")

    # Запускаем все фоновые воркеры
    for task in modules_repo["background_tasks"]:
        asyncio.create_task(task(client))

    # Если этот запуск — следствие команды .restart, сообщаем об успехе
    await notify_after_restart()

    # Пусть крутится вечно
    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\n[Core] Юзербот остановлен вручную.")
    finally:
        # Небольшой фикс, чтобы не кидало ошибку при закрытии отключенного клиента
        disconnect_task = client.disconnect()
        if disconnect_task:
            client.loop.run_until_complete(disconnect_task)