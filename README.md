# **🛠 Полное руководство по созданию модулей для Юзербота**

Добро пожаловать в документацию для разработчиков! Архитектура нашего юзербота построена на плагинах. Вам **никогда не нужно редактировать ядро (userbot.py)**, чтобы добавить новую функцию.  
Достаточно создать новый файл .py в нужной папке, и бот автоматически подхватит его, а встроенный установщик сам скачает нужные библиотеки.

## **📑 Оглавление**

1. [Архитектура и расположение файлов](#1-архитектура-и-расположение-файлов)  
2. [Базовая структура и Метаданные](#2-базовая-структура-и-метаданные)  
3. [Создание команд (@register_cmd)](#3-создание-команд)  
4. [Зависимости и автоустановка библиотек](#4-зависимости-и-автоустановка-библиотек-важно)  
5. [Фоновые задачи (@register_bg)](#5-фоновые-задачи)  
6. [Система конфигураций (Global_config)](#6-система-конфигураций)  
7. [Шпаргалка по Telethon](#7-шпаргалка-по-telethon)  
8. [Правила и частые ошибки](#8-правила-и-частые-ошибки)  
9. [Встроенный Telegram Бот ядра, Уведомления и Inline API](#9-встроенный-telegram-бот-ядра-уведомления-и-inline-api)  
10. [Система перезагрузки и сохранения контекста (save_restart_info)](#10-система-перезагрузки-и-сохранения-контекста)  
11. [Защита от спамбана и Rate Limiter](#11-защита-от-спамбана-и-rate-limiter)

---

## **1. Архитектура и расположение файлов**

Модули в юзерботе разделены по папкам в зависимости от их назначения:

* 📂 `modules/` — Пользовательские модули. Сюда устанавливаются все сторонние плагины командой `.install` (из файла) или `.ghinstall` (из репозитория). Их можно свободно удалять через `.uninstall`.  
* 📂 `system_modules/` — Системные модули (ядро команд, справка, установщики, настройки, обновление). **Фича ядра:** любой файл, помещенный в эту папку, автоматически получает статус «системного». Такие модули защищены от случайного удаления через `.uninstall`, а в команде `.help` помечаются иконкой 🔒.
* 📂 `init_modules/` — Pre-Auth модули инициализации (мастер авторизации QR/Web, настройка прокси). Выполняются до авторизации клиента Telegram.

> 💡 **Режим хостинга (`--host`):** При запуске с флагом `--host` юзербот в целях безопасности отключает модули терминала (`terminal.py`) и локального установщика (`installer.py`).

---

## **2. Базовая структура и Метаданные**

Каждый модуль взаимодействует с ядром через `registry.py`. В начале файла рекомендуется задавать метаданные, чтобы в команде `.help` модуль выглядел красиво.  

**Минимальный шаблон модуля:**  
```python
from registry import register_cmd, set_module_meta

# 1. Задаем метаданные: Имя, Описание, Статус  
# Если system=True, модуль нельзя будет удалить через .uninstall (даже если он в папке modules)  
set_module_meta(  
    name="Мой Модуль",   
    desc="Описание того, что делает этот модуль",  
    system=False   
)

# 2. Далее идет код команд...
```

*Примечание: Если не использовать `set_module_meta`, ядро само создаст резервное имя, взяв название файла с заглавной буквы, но описание будет "Описания не найдено".*

---

## **3. Создание команд**

Команды вызываются префиксом `.` (точка). Для регистрации используется декоратор `@register_cmd`.

### **Сигнатура функции команды**

Функция **обязательно** должна быть асинхронной (`async def`) и принимать ровно 3 аргумента:

1. `client`: Экземпляр `TelegramClient` (доступ ко всем методам API Telegram).  
2. `event`: Объект события сообщения (ваше отправленное сообщение).  
3. `args`: Строка текста, написанная *после* команды. Если текста нет, возвращает пустую строку `""`.

### **Пример команды:**

```python
from registry import register_cmd, set_module_meta

set_module_meta("Утилиты", "Базовые утилиты")

@register_cmd("say", desc="Печатает текст от твоего имени. Юзай: .say <текст>")  
async def say_cmd(client, event, args):  
    # Проверка на наличие аргументов  
    if not args:  
        return await event.edit("❌ Укажи текст: `.say Привет`")  
      
    # event.edit() редактирует исходное сообщение (где вы написали .say)  
    await event.edit(f"🗣 {args}")
```

---

## **4. Зависимости и автоустановка библиотек (ВАЖНО!)**

Наш установщик (`installer.py` и `gh_installer.py`) невероятно умный. Если вашему модулю нужны сторонние библиотеки (например, `requests` или `Pillow`), вам **не нужно** просить пользователя устанавливать их вручную!  
Достаточно добавить в любом месте файла (желательно в начале) специальный комментарий:  
```python
# requires: requests pillow bs4
```

### **Как работает процесс установки:**
1. При установке модуля через `.install` (из файла) или `.ghinstall` (из репозитория) бот находит строку `# requires:`, автоматически запускает `pip install` для каждого пакета и валидирует импорт.
2. Во время завершающей инициализации бот выводит статус подготовки (`⏳ Подготовка и настройка модуля...`).
3. Ядро **автоматически бесшовно перезагружает процесс**, чтобы применить все изменения в чистом контексте памяти, и после рестарта обновляет исходное сообщение на:
   `✅ Модуль <имя> успешно установлен и готов к работе!`

*Также установщик умеет перехватывать `ModuleNotFoundError` во время импорта и автоматически докачивать недостающие зависимости на лету.*

---

## **5. Фоновые задачи**

Фоновые задачи (Background Tasks) загружаются при старте юзербота и крутятся параллельно основному процессу. Отлично подходят для авто-смены био, таймеров, чекеров.  
Используйте декоратор `@register_bg()`. Функция принимает только аргумент `client`.  

```python
import asyncio
from registry import set_module_meta, register_bg  

set_module_meta("Авто-Статус", "Фоновый процесс")

@register_bg()  
async def auto_bio_loop(client):  
    print("[Авто-Статус] Цикл запущен!")  
      
    while True:  
        try:  
            # Ваша фоновая логика  
            pass  
        except Exception as e:  
            print(f"Ошибка в фоне: {e}")  
              
        # ОБЯЗАТЕЛЬНО: асинхронный сон, чтобы не "повесить" бота!  
        await asyncio.sleep(60)
```

---

## **6. Система конфигураций**

Бот имеет единый файл `Global_config.json`, управляемый системной командой `.cfg`.  
Чтобы модуль работал с конфигом, используйте функции `init_config`, `get_config`, `set_config` из `registry`.

### **Инициализация (в начале файла)**

Используйте `__name__` как уникальный идентификатор модуля для конфига.  

```python
from registry import init_config, get_config, set_config

# Устанавливаем дефолтные значения.   
# Запишутся в JSON только если их там еще нет.  
init_config(__name__, {  
    "enabled": True,  
    "delay": 5,  
    "spam_text": "Привет!"  
})
```

### **Использование в командах**

```python
@register_cmd("check", desc="Проверить конфиг")  
async def check_cfg(client, event, args):  
    # Чтение  
    is_on = get_config(__name__, "enabled")  
    delay = get_config(__name__, "delay")  
      
    # Запись (если нужно изменить программно)  
    set_config(__name__, "delay", delay + 1)  
      
    await event.edit(f"Статус: {is_on}, Задержка: {delay}")
```

*Пользователь может изменять эти значения налету: `.cfg set <имя_модуля> delay 10`. Встроенный парсер сам поймет, что `10` это число, а `True` — булево значение.*

---

## **7. Шпаргалка по Telethon**

Внутри команд вам доступна полная документация Telethon. Самые частые операции:  

**Работа с текущим сообщением:**
* `await event.edit("Текст")` — Изменить отправленное сообщение (скрывает саму команду).  
* `await event.delete()` — Удалить сообщение с командой.  
* `await event.respond("Текст")` — Отправить новое сообщение следом.

**Реплай (Ответ на сообщение):**  
Часто команды (например `.install` или `.ban`) требуют реплая на чужое сообщение.  

```python
reply_msg = await event.get_reply_message()  
if not reply_msg:  
    return await event.edit("❌ Сделай реплай на сообщение!")

print(reply_msg.text) # Текст исходного сообщения  
print(reply_msg.sender_id) # ID отправителя
```

**Работа с медиа:**  
```python
# Скачивание файла из реплая  
if reply_msg.file:  
    path = await reply_msg.download_media(file="downloads/")  
      
# Отправка файла  
await client.send_file(event.chat_id, "photo.jpg", caption="Описание!")
```

---

## **8. Правила и частые ошибки**

1. 🚫 **НИКОГДА не используйте `time.sleep()`**. Это заблокирует Event Loop, и юзербот зависнет, перестав реагировать на любые команды. Используйте **только** `await asyncio.sleep()`.  
2. ⚠️ **Перехватывайте ошибки (`try...except`)**. В фоновых задачах или при массовых рассылках необработанная ошибка может крашнуть отдельную таску.  
3. 🎯 **Не фильтруйте входящие сообщения**. Ядро бота (`event.out` в `userbot.py`) уже настроено так, что модули реагируют **только на ваши исходящие** сообщения. Вам не нужно проверять, кто написал команду.  
4. 🔌 **Уникальные имена команд**. Команды регистрируются в плоский словарь `modules_repo["commands"]`. Если два разных модуля зарегистрируют команду `@register_cmd("ping")`, загрузится только последняя.  
5. 🧩 **Зависимости**. Всегда используйте метку `# requires: ...` для PIP пакетов. Это сделает ваш модуль plug-and-play для любого пользователя.

---

## **9. Встроенный Telegram Бот ядра и Inline API**

Telegram Бот является **неотъемлемой частью ядра** (`userbot.py` + `registry.py`), а не сторонним модулем. Он расширяет возможности юзербота, позволяя отправлять сообщения с интерактивными инлайн-кнопками в любые чаты и получать системные уведомления.

### **🤖 Архитектура и автонастройка бота**
При первом запуске юзербота ядро автоматически:
1. Обращается к `@BotFather` от лица вашего аккаунта.
2. Создает нового Telegram бота с именем `Имя's Assistant` и юзернеймом вида `<username>_ub_<suffix>_bot`.
3. Настраивает ему Inline-режим (`/setinline` -> `Search`).
4. Токен и имя бота сохраняются в `core_conf.json`.
5. **Инициализирующий рукопожатие-диалог:** Юзербот автоматически отправляет сообщение `/start` созданному боту. Это гарантирует, что бот кеширует объект владельца и может присылать личные уведомления без ошибки `PeerUser`.
6. При успешном запуске бот присылает красочное уведомление в ЛС владельца (с различием первого запуска и последующих).

---

### **🔘 Разработка модулей с Inline-кнопками**

Для отправки инлайн-кнопок от лица бота импортируйте функции `send_inline` и `@register_callback` из `registry.py`, а также `Button` и `errors` из `telethon`:

```python
from telethon import Button, errors
from registry import register_cmd, set_module_meta, send_inline, register_callback
```

#### **1. Отправка инлайн-сообщения (`send_inline`)**
```python
await send_inline(client, chat_id, text, buttons=None, reply_to=None)
```
- **`client`**: Экземпляр юзербота (`TelegramClient`).
- **`chat_id`**: ID чата для отправки (`event.chat_id`).
- **`text`**: Текст сообщения (с поддержкой Markdown).
- **`buttons`**: Список рядов кнопок (`[ [Button.inline(...), ...], [...] ]`).
- **`reply_to`**: ID сообщения для ответа (`event.reply_to_msg_id`).

*Как это работает:* `send_inline` кеширует текст и кнопки, отправляет инлайн-запрос боту (`client.inline_query`), получает результат и мгновенно кликает его прямо в чат от вашего лица. Исходное сообщение с `.командой` после этого обычно удаляют (`await event.delete()`).

#### **2. Обработка кликов по кнопкам (`@register_callback`)**
```python
@register_callback("prefix")
async def my_callback_handler(event, data):
    ...
```
- **`event`**: Объект `CallbackQuery.Event` от Telethon.
- **`data`**: Строка `callback_data` кнопки (например `"prefix_action_123"`).

**Полезные методы в обработчике:**
- `user = await event.get_sender()` — узнать, кто нажал на кнопку (`user.id`, `user.first_name`).
- `await event.answer("Текст", alert=True)` — показать всплывающее окно (alert) или подсказку.
- `await event.edit("Новый текст", buttons=new_buttons)` — обновить текст/кнопки инлайн-сообщения.

---

### **⚠️ Важные правила и Best Practices для Inline-модулей**

1. **Уникальность префиксов (`@register_callback`):**
   - Роутер ядра автоматически сортирует префиксы по длине (`key=len, reverse=True`), но старайтесь делать префиксы кнопок максимально уникальными, чтобы избежать перехвата другими кнопками.
   - ❌ **Опасно:** `@register_callback("ttt_menu")` и `@register_callback("ttt_menu_pvp")`
   - ✅ **Надежно:** `@register_callback("ttt_mainmenu")` и `@register_callback("ttt_pvpmode")`

2. **Передача параметров в `callback_data`:**
   - Если в `callback_data` передаются переменные (ID игры, номер страницы, индекс клетки), **не используйте символы `_` внутри значений ID** при последующем разбиении через `.split("_")`.
   - ✅ **Формирование кнопки:** `Button.inline("Клетка", f"ttt_cell_{game_id}_{idx}".encode())` (где `game_id = "g1700000000"` без внутренних подчёркиваний).
   - ✅ **Безопасный парсинг:**
     ```python
     payload = data.replace("ttt_cell_", "")
     game_id, idx_str = payload.rsplit("_", 1)
     idx = int(idx_str)
     ```

3. **Защита от ошибки `MessageNotModifiedError`:**
   - Если при клике по кнопке текст и кнопки сообщения **не изменились**, Telegram API вернет ошибку `MessageNotModifiedError`.
   - Всегда оборачивайте `event.edit` в блок `try...except`:
     ```python
     try:
         await event.edit(new_text, buttons=new_buttons)
     except errors.MessageNotModifiedError:
         await event.answer() # Закрывает анимацию загрузки кнопки без ошибки
     ```

---

### **💡 Полный пример модуля (Интерактивный Инлайн-Счетчик)**

```python
import time
from telethon import Button, errors
from registry import register_cmd, set_module_meta, send_inline, register_callback

set_module_meta("Счетчик", "Интерактивный инлайн-счетчик с кнопками")

# Хранилище счетчиков: counter_id -> int
counters = {}

@register_cmd("counter", desc="Запустить инлайн-счетчик")
async def counter_cmd(client, event, args):
    # Создаем уникальный ID счетчика без подчёркиваний
    cid = f"c{int(time.time() * 1000)}"
    counters[cid] = 0

    buttons = [
        [
            Button.inline("➖ Уменьшить", f"cnt_dec_{cid}".encode()),
            Button.inline("➕ Увеличить", f"cnt_inc_{cid}".encode())
        ],
        [Button.inline("🔄 Сбросить", f"cnt_reset_{cid}".encode())]
    ]

    await send_inline(
        client,
        event.chat_id,
        "🔢 **Инлайн Счетчик**\n\nТекущее значение: `0`",
        buttons=buttons,
        reply_to=event.reply_to_msg_id
    )
    await event.delete()

@register_callback("cnt_inc_")
async def on_inc(event, data):
    cid = data.replace("cnt_inc_", "")
    if cid not in counters:
        return await event.answer("⚠️ Счетчик не найден!", alert=True)
    
    counters[cid] += 1
    await _update_counter(event, cid)

@register_callback("cnt_dec_")
async def on_dec(event, data):
    cid = data.replace("cnt_dec_", "")
    if cid not in counters:
        return await event.answer("⚠️ Счетчик не найден!", alert=True)
    
    counters[cid] -= 1
    await _update_counter(event, cid)

@register_callback("cnt_reset_")
async def on_reset(event, data):
    cid = data.replace("cnt_reset_", "")
    if cid not in counters:
        return await event.answer("⚠️ Счетчик не найден!", alert=True)
    
    counters[cid] = 0
    await _update_counter(event, cid)

async def _update_counter(event, cid):
    val = counters[cid]
    buttons = [
        [
            Button.inline("➖ Уменьшить", f"cnt_dec_{cid}".encode()),
            Button.inline("➕ Увеличить", f"cnt_inc_{cid}".encode())
        ],
        [Button.inline("🔄 Сбросить", f"cnt_reset_{cid}".encode())]
    ]
    try:
        await event.edit(f"🔢 **Инлайн Счетчик**\n\nТекущее значение: `{val}`", buttons=buttons)
    except errors.MessageNotModifiedError:
        await event.answer()
```

---

## **10. Система перезагрузки и сохранения контекста**

Если вашему модулю требуется перезапустить юзербота (например, после обновления, изменения критических настроек или установки библиотек), используйте механизм сохранения контекста через `save_restart_info`:

```python
import os
import sys
from registry import save_restart_info

# 1. Сохраняем чат, ID сообщения и опциональный кастомный текст для отчёта
save_restart_info(
    chat_id=event.chat_id,
    message_id=event.id,
    custom_text="🎉 **Модуль успешно обновлен и готов к работе!**"
)

# 2. Корректно закрываем сессию Telegram
try:
    await client.disconnect()
except Exception:
    pass

# 3. Полный перезапуск Python-процесса
python = sys.executable
script = os.path.abspath(sys.argv[0])
os.execv(python, [python, script] + sys.argv[1:])
```

### **Параметры `save_restart_info`:**
- `chat_id` *(int)*: ID чата, где была вызвана команда.
- `message_id` *(int)*: ID сообщения, которое будет отредактировано после рестарта.
- `custom_text` *(str, optional)*: Текст, который ядро подставит после успешного запуска. Если `None`, выводится стандартное `✅ Успешно перезагружен! (Заняло: X.XX сек.)`.

---

## **11. Защита от спамбана и Rate Limiter**

В ядро и реестр встроена проактивная защита от флуда и блокировок Telegram API:

1. **Минимальный интервал между командами (0.3 сек):** Предотвращает слишком частые параллельные запросы к API.
2. **Перехват `FloodWaitError`:**
   - Если Telegram API возвращает задержку `FloodWait`, ядро временно блокирует выполнение новых команд на указанное время, предотвращая сброс сессии.
   - Владельцу автоматически приходит сервисное уведомление через бота с таймером окончания блокировки.
3. **Отправка сервисных уведомлений владельцу (`send_bot_notification`):**
   ```python
   from registry import send_bot_notification

   # Отправит сообщение в ЛС владельцу через встроенного Telegram-бота
   await send_bot_notification("⚠️ **Внимание:** В модуле произошло важное событие!")
   ```

