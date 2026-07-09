# Здесь хранится вся инфа о командах и фоновых задачах
import inspect
import json
import os
import time

# --- СИСТЕМА КОНФИГУРАЦИЙ ---
CONFIG_FILE = "Global_config.json"
global_config = {}

def load_config():
    """Загружает конфигурацию из JSON файла"""
    global global_config
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            try:
                global_config = json.load(f)
            except json.JSONDecodeError:
                global_config = {}
    else:
        global_config = {}
        save_config()

def save_config():
    """Сохраняет текущую конфигурацию в JSON файл"""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(global_config, f, indent=4, ensure_ascii=False)

def get_config(module_name, key, default=None):
    """
    Получает значение из конфига.
    Пример: get_config("module_ping", "reply_text", "Понг!")
    """
    return global_config.get(module_name, {}).get(key, default)

def set_config(module_name, key, value):
    """
    Записывает значение в конфиг и сохраняет файл.
    Пример: set_config("module_ping", "reply_text", "Дарова!")
    """
    if module_name not in global_config:
        global_config[module_name] = {}
    
    global_config[module_name][key] = value
    save_config()

def init_config(module_name, default_dict):
    """
    Инициализирует дефолтные настройки модуля. 
    Записывает их в конфиг, только если их там еще нет.
    Вызывать в начале файла модуля.
    """
    if module_name not in global_config:
        global_config[module_name] = {}
        
    changed = False
    for key, default_value in default_dict.items():
        if key not in global_config[module_name]:
            global_config[module_name][key] = default_value
            changed = True
            
    if changed:
        save_config()

# Загружаем конфиг при старте
load_config()


# --- РЕЕСТР МОДУЛЕЙ И КОМАНД ---
modules_repo = {
    "modules": {},       # Инфа о модулях и их командах
    "commands": {},      # Плоский список для быстрого запуска
    "background_tasks": []
}

def set_module_meta(name, desc="Описания не найдено", system=False):
    """
    Задает имя и описание для модуля (вызывать в начале файла).
    system=True — модуль системный. Такие модули нельзя удалить через .uninstall,
    и в .help они помечаются специальной пометкой "Системное".
    """
    frame = inspect.currentframe().f_back
    module_id = inspect.getmodule(frame).__name__
    
    if module_id not in modules_repo["modules"]:
        modules_repo["modules"][module_id] = {
            "name": name,
            "desc": desc,
            "commands": {},
            "system": system
        }
    else:
        modules_repo["modules"][module_id]["name"] = name
        modules_repo["modules"][module_id]["desc"] = desc
        modules_repo["modules"][module_id]["system"] = system

def register_cmd(command_name, desc="Описания не найдено"):
    def decorator(func):
        module_id = inspect.getmodule(func).__name__
        
        # Если модуль не задал о себе инфу через set_module_meta, даем ему дефолтное имя
        if module_id not in modules_repo["modules"]:
            fallback_name = module_id.split('.')[-1].capitalize()
            modules_repo["modules"][module_id] = {
                "name": fallback_name,
                "desc": "Описания не найдено",
                "commands": {},
                "system": False
            }
        
        # Сохраняем описание команды в модуль
        modules_repo["modules"][module_id]["commands"][command_name] = desc
        # Сохраняем саму функцию в плоский словарь для ядра
        modules_repo["commands"][command_name] = func
        
        return func
    return decorator

def register_bg():
    def decorator(func):
        modules_repo["background_tasks"].append(func)
        return func
    return decorator

def is_system_module(module_id):
    """Проверяет, является ли модуль системным (защищенным от удаления)."""
    mod_info = modules_repo["modules"].get(module_id)
    if not mod_info:
        return False
    return mod_info.get("system", False)


# --- СИСТЕМА ПОЛНОЙ ПЕРЕЗАГРУЗКИ ---
# Используется для того, чтобы после os.execv() ядро могло найти чат и
# сообщение, куда нужно отправить подтверждение об успешном рестарте.
RESTART_FILE = "restart_info.json"

def save_restart_info(chat_id, message_id):
    """
    Сохраняет данные о том, где было вызвано .restart, чтобы после
    перезапуска процесса ядро могло отредактировать это же сообщение.
    """
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "time": time.time()
    }
    try:
        with open(RESTART_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[Registry] Не удалось сохранить restart_info: {e}")

def pop_restart_info():
    """
    Читает данные о рестарте (если они есть) и сразу удаляет файл,
    чтобы при следующем обычном запуске бот не пытался снова кому-то отвечать.
    Возвращает dict {chat_id, message_id, time} либо None.
    """
    if not os.path.exists(RESTART_FILE):
        return None
    try:
        with open(RESTART_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = None
    finally:
        try:
            os.remove(RESTART_FILE)
        except OSError:
            pass
    return data