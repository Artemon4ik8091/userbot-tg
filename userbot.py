import os
import sys
import time
import json
import asyncio
import importlib
import random
import re
import psutil
import shutil
import subprocess
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from telethon import TelegramClient, events, errors, Button
from telethon.sessions import MemorySession
import qrcode

if "--help" in sys.argv or "-h" in sys.argv:
    print("""Использование: python3 userbot.py [ПАРАМЕТРЫ]

Доступные параметры запуска:
  -h, --help                  Показать это сообщение справки и выйти
  --no-web                    Использовать консольную настройку вместо веб-интерфейса
  --no-api                    Скрыть шаг ввода API Telegram (если уже настроено)
  --no-proxy                  Скрыть шаг настройки прокси в веб-интерфейсе
  --set-app-id <число>        Установить API ID (получить на my.telegram.org)
  --set-hash-id <строка>      Установить API Hash
  --set-proxy-ip <строка>     Установить IP-адрес прокси (например, 127.0.0.1)
  --set-proxy-port <число>    Установить порт прокси (например, 1080)
  --set-proxy-protocol <тип>  Установить протокол прокси (доступны: http, socks4, socks5)
""")
    sys.exit(0)

# Подключаем наше общее хранилище из реестра
from registry import (
    modules_repo,
    pop_restart_info,
    set_owner_id,
    get_owner_id,
    set_bot,
    get_bot,
    get_bot_username,
    get_userbot_start_time,
    send_bot_notification,
    callback_handlers,
    inline_payload_cache,
    save_restart_info,
    is_rate_limited,
    get_rate_limit_remaining,
    apply_flood_wait,
    check_cmd_rate_limit
)

# --- НАСТРОЙКИ КОНФИГА ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "core_conf.json")
WEB_SETUP_HOST = "127.0.0.1"
WEB_SETUP_TIMEOUT = 600
NO_API_SETUP = "--no-api" in sys.argv
NO_PROXY_SETUP = "--no-proxy" in sys.argv
SET_APP_ID = None
SET_HASH_ID = None
SET_PROXY_IP = None
SET_PROXY_PORT = None
SET_PROXY_PROTOCOL = None
WEB_SETUP_SERVER = None

for index, arg in enumerate(sys.argv):
    if arg == "--set-app-id" and index + 1 < len(sys.argv):
        SET_APP_ID = sys.argv[index + 1]
    if arg == "--set-hash-id" and index + 1 < len(sys.argv):
        SET_HASH_ID = sys.argv[index + 1]
    if arg == "--set-proxy-ip" and index + 1 < len(sys.argv):
        SET_PROXY_IP = sys.argv[index + 1]
    if arg == "--set-proxy-port" and index + 1 < len(sys.argv):
        SET_PROXY_PORT = sys.argv[index + 1]
    if arg == "--set-proxy-protocol" and index + 1 < len(sys.argv):
        SET_PROXY_PROTOCOL = sys.argv[index + 1]


def save_core_config(config_data):
    """Сохраняет актуальный конфиг ядра в core_conf.json"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4, ensure_ascii=False)


def apply_preconfigured_credentials():
    """Если переданы флаги для API/прокси, сразу сохраняет их в core_conf.json и возвращает True."""
    if not SET_APP_ID and not SET_HASH_ID and not SET_PROXY_IP and not SET_PROXY_PORT and not SET_PROXY_PROTOCOL:
        return False

    config_data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception:
            config_data = {}

    if SET_APP_ID:
        try:
            config_data["app_id"] = int(SET_APP_ID)
        except ValueError:
            raise ValueError("--set-app-id должен быть числом")
    if SET_HASH_ID:
        config_data["hash_id"] = SET_HASH_ID

    if SET_PROXY_IP or SET_PROXY_PORT or SET_PROXY_PROTOCOL:
        proxy_config = config_data.get("proxy")
        if not isinstance(proxy_config, dict):
            proxy_config = {}
            config_data["proxy"] = proxy_config
        if SET_PROXY_IP:
            proxy_config["addr"] = SET_PROXY_IP
        if SET_PROXY_PORT:
            try:
                proxy_config["port"] = int(SET_PROXY_PORT)
            except ValueError:
                raise ValueError("--set-proxy-port должен быть числом")
        if SET_PROXY_PROTOCOL:
            proxy_config["proxy_type"] = SET_PROXY_PROTOCOL

    save_core_config(config_data)
    return True


def get_proxy_config(config):
    """
    Извлекает настройки прокси из конфигурации ядра (core_conf.json).
    Прокси не обязателен. Если прокси не задан в конфиге или выключен, возвращает None.
    """
    if not isinstance(config, dict):
        return None

    proxy = config.get("proxy")
    if not proxy or not isinstance(proxy, dict):
        return None

    addr = proxy.get("addr")
    port = proxy.get("port")
    
    if not addr or port is None:
        return None

    try:
        port = int(port)
    except (ValueError, TypeError):
        return None

    proxy_type = str(proxy.get("proxy_type", "http")).lower()

    proxy_dict = {
        'proxy_type': proxy_type,
        'addr': str(addr),
        'port': port
    }

    if proxy.get("username"):
        proxy_dict['username'] = str(proxy.get("username"))
    if proxy.get("password"):
        proxy_dict['password'] = str(proxy.get("password"))
    if "rdns" in proxy:
        proxy_dict['rdns'] = bool(proxy.get("rdns"))

    return proxy_dict

def prompt_for_core_config():
    """Консольный путь настройки, используемый при запуске с --no-web."""
    print("\n" + "="*50)
    print("=== ПЕРВЫЙ ЗАПУСК: НАСТРОЙКА API ТЕЛЕГРАМА ===")
    print("Получить app_id и hash_id можно на сайте https://my.telegram.org")
    print("="*50)

    while True:
        try:
            app_id_input = input("Введите ваш app_id (только цифры): ").strip()
            app_id = int(app_id_input)
            break
        except ValueError:
            print("❌ Ошибка: app_id должен состоять только из цифр. Попробуйте еще раз.")

    hash_id = input("Введите ваш hash_id (строка): ").strip()

    config_data = {
        "app_id": app_id,
        "hash_id": hash_id
    }

    print("\n💡 Настройка юзернейма встроенного Telegram бота.")
    print("   Оставьте пустым (нажмите Enter) для авто-поиска или случайной генерации.")
    desired_bot_un = input("Введите желаемый юзернейм бота (например, my_cool_ub_bot) [Enter — авто]: ").strip()
    if desired_bot_un:
        config_data["desired_bot_username"] = desired_bot_un.lstrip("@")

    use_proxy = input("\nХотите настроить прокси? (y/N): ").strip().lower()
    if use_proxy in ['y', 'yes', 'да', 'д'] or any([SET_PROXY_IP, SET_PROXY_PORT, SET_PROXY_PROTOCOL]):
        proxy_type = (SET_PROXY_PROTOCOL or input("Тип прокси (http/socks5/socks4) [по умолчанию http]: ").strip().lower() or "http").lower()
        addr = (SET_PROXY_IP or input("Адрес прокси (например, 127.0.0.1): ").strip())
        if SET_PROXY_PORT is not None:
            port_str = str(SET_PROXY_PORT)
        else:
            while True:
                try:
                    port_str = input("Порт прокси: ").strip()
                    if not port_str:
                        print("❌ Ошибка: порт не может быть пустым.")
                        continue
                    port = int(port_str)
                    break
                except ValueError:
                    print("❌ Ошибка: порт должен быть числом.")
            port = int(port_str)
        if SET_PROXY_PORT is None:
            port = int(port_str)
        else:
            port = int(SET_PROXY_PORT)
        username = input("Логин прокси (оставьте пустым, если не требуется): ").strip()
        password = input("Пароль прокси (оставьте пустым, если не требуется): ").strip()

        proxy_dict = {
            "proxy_type": proxy_type,
            "addr": addr,
            "port": port
        }
        if username:
            proxy_dict["username"] = username
        if password:
            proxy_dict["password"] = password

        config_data["proxy"] = proxy_dict

    save_core_config(config_data)
    print(f"[Core] ✅ Настройки успешно сохранены в файл {CONFIG_FILE}!\n")
    return config_data


class WebConfigRequestHandler(BaseHTTPRequestHandler):
    """Простой HTTP-сервер для веб-настройки первого запуска."""

    def _render_setup_page(self):
        show_api_step = not NO_API_SETUP and not (SET_APP_ID or SET_HASH_ID)
        if not show_api_step:
            show_api_step = False
        show_proxy_step = not NO_PROXY_SETUP
        visible_step_count = 4 - (0 if show_api_step else 1) - (0 if show_proxy_step else 1)
        steps_label = "шага" if visible_step_count == 2 else "шагов"
        subtitle_text = f"Выполните {visible_step_count} {steps_label}, и юзербот будет готов к запуску."
        progress_html = []
        step_number = 1
        if show_api_step:
            progress_html.append('<div class="step-pill active">1. API Telegram</div>')
            step_number = 2
        if show_proxy_step:
            progress_html.append('<div class="step-pill active">' + str(step_number) + '. Прокси</div>')
            step_number += 1
        progress_html.append('<div class="step-pill">' + str(step_number) + '. Юзернейм бота</div>')
        progress_html.append('<div class="step-pill">' + str(step_number + 1) + '. Вход в аккаунт</div>')

        api_step_html = """
        <div class="step active" data-step="1">
          <h2>Шаг 1 — Настройка API Telegram</h2>
          <p>Получите app_id и hash_id на my.telegram.org.</p>
          <div class="row">
            <label for="app_id">app_id</label>
            <input id="app_id" name="app_id" required placeholder="Только цифры">
          </div>
          <div class="row">
            <label for="hash_id">hash_id</label>
            <input id="hash_id" name="hash_id" required placeholder="Строка с Telegram API hash">
          </div>
        </div>
        """ if show_api_step else ""

        proxy_use_selected = "1" if any([SET_PROXY_IP, SET_PROXY_PORT, SET_PROXY_PROTOCOL]) else "0"
        proxy_default_type = (SET_PROXY_PROTOCOL or "http").lower()
        proxy_default_addr = SET_PROXY_IP or ""
        proxy_default_port = SET_PROXY_PORT or ""
        proxy_step_html = f"""
        <div class="step" data-step="2">
          <h2>Шаг 2 — Прокси</h2>
          <p>Если у вас нет прокси, просто оставьте значения пустыми или выберите «Нет».</p>
          <div class="row">
            <label for="use_proxy">Использовать прокси?</label>
            <select id="use_proxy" name="use_proxy">
              <option value="0" {'selected' if proxy_use_selected == '0' else ''}>Нет</option>
              <option value="1" {'selected' if proxy_use_selected == '1' else ''}>Да</option>
            </select>
          </div>
          <div class="row">
            <label for="proxy_type">Тип прокси</label>
            <select id="proxy_type" name="proxy_type">
              <option value="http" {'selected' if proxy_default_type == 'http' else ''}>http</option>
              <option value="socks5" {'selected' if proxy_default_type == 'socks5' else ''}>socks5</option>
              <option value="socks4" {'selected' if proxy_default_type == 'socks4' else ''}>socks4</option>
            </select>
          </div>
          <div class="row">
            <label for="proxy_addr">Адрес прокси</label>
            <input id="proxy_addr" name="proxy_addr" placeholder="127.0.0.1" value="{proxy_default_addr}">
          </div>
          <div class="row">
            <label for="proxy_port">Порт прокси</label>
            <input id="proxy_port" name="proxy_port" placeholder="1080" value="{proxy_default_port}">
          </div>
          <div class="row">
            <label for="proxy_username">Логин прокси</label>
            <input id="proxy_username" name="proxy_username" placeholder="Необязательно">
          </div>
          <div class="row">
            <label for="proxy_password">Пароль прокси</label>
            <input id="proxy_password" name="proxy_password" placeholder="Необязательно">
          </div>
        </div>
        """ if show_proxy_step else ""

        bot_step_html = """
        <div class="step" data-step="3">
          <h2>Шаг 3 — Юзернейм бота</h2>
          <p>Можно оставить пустым — тогда бот будет создан или найден автоматически.</p>
          <div class="row">
            <label for="desired_bot_username">Желаемый юзернейм бота</label>
            <input id="desired_bot_username" name="desired_bot_username" placeholder="my_cool_ub_bot">
            <div class="hint">Например: my_cool_ub_bot</div>
          </div>
        </div>
        """

        login_step_html = """
        <div class="step" data-step="4">
          <h2>Шаг 4 — Генерация QR и вход в аккаунт</h2>
          <div class="banner">
            После сохранения настроек в терминале будет показан QR-код для входа в Telegram-аккаунт.
          </div>
          <div class="warning">
            Если включён облачный пароль, после сканирования QR вам потребуется ввести его в консоль.
          </div>
        </div>
        """

        # Добавлено active к первому доступному шагу, если API пропущен
        if not show_api_step and show_proxy_step:
            proxy_step_html = proxy_step_html.replace('class="step"', 'class="step active"')
        elif not show_api_step and not show_proxy_step:
            bot_step_html = bot_step_html.replace('class="step"', 'class="step active"')

        html = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UBTG — Пошаговая настройка</title>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-1: #0f172a;
      --bg-2: #1e1b4b;
      --card-bg: rgba(30, 41, 59, 0.65);
      --card-border: rgba(255, 255, 255, 0.1);
      --text: #f8fafc;
      --muted: #94a3b8;
      --accent: #6366f1;
      --accent-gradient: linear-gradient(135deg, #6366f1, #a855f7);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: 'Outfit', sans-serif;
      background: linear-gradient(135deg, var(--bg-1), var(--bg-2));
      color: var(--text);
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      overflow-x: hidden;
    }
    body::before {
      content: ''; position: absolute; top: -20%; left: -10%; width: 50%; height: 50%;
      background: radial-gradient(circle, rgba(99,102,241,0.2) 0%, transparent 70%);
      z-index: -1; filter: blur(40px);
    }
    body::after {
      content: ''; position: absolute; bottom: -20%; right: -10%; width: 50%; height: 50%;
      background: radial-gradient(circle, rgba(168,85,247,0.15) 0%, transparent 70%);
      z-index: -1; filter: blur(40px);
    }
    .card {
      width: min(850px, 100%);
      background: var(--card-bg);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid var(--card-border);
      border-radius: 24px;
      box-shadow: 0 30px 60px rgba(0,0,0,0.4);
      overflow: hidden;
      animation: slideUp 0.6s cubic-bezier(0.16, 1, 0.3, 1);
    }
    @keyframes slideUp { from { opacity: 0; transform: translateY(30px); } to { opacity: 1; transform: translateY(0); } }
    .header {
      padding: 32px 32px 20px;
      border-bottom: 1px solid var(--card-border);
      text-align: center;
    }
    .header h1 { margin: 0 0 12px; font-size: 2rem; font-weight: 700; background: var(--accent-gradient); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .header p { margin: 0; color: var(--muted); font-size: 1.05rem; }
    .progress { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; padding: 24px 32px 0; }
    .step-pill {
      text-align: center; padding: 12px 10px; border-radius: 12px;
      background: rgba(255,255,255,0.05); color: var(--muted);
      font-size: 0.95rem; font-weight: 500; border: 1px solid transparent; transition: all 0.3s ease;
    }
    .step-pill.active {
      color: #fff; background: rgba(99,102,241,0.15);
      border-color: rgba(99,102,241,0.4); box-shadow: 0 0 15px rgba(99,102,241,0.2);
    }
    .body { padding: 32px; }
    form { display: grid; gap: 24px; }
    .step { display: none; animation: fadeIn 0.4s ease; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
    .step.active { display: grid; gap: 16px; }
    .step h2 { margin: 0 0 8px; font-size: 1.4rem; font-weight: 600; }
    .step p { margin: 0 0 12px; color: var(--muted); font-size: 0.95rem; line-height: 1.5; }
    .row { display: grid; gap: 8px; }
    label { font-weight: 500; font-size: 0.95rem; color: #e2e8f0; }
    input, select {
      width: 100%; padding: 14px 16px; border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.1); background: rgba(15, 23, 42, 0.5);
      color: var(--text); font-family: inherit; font-size: 1rem; transition: all 0.2s;
    }
    input:focus, select:focus { outline: none; border-color: var(--accent); background: rgba(15, 23, 42, 0.8); box-shadow: 0 0 0 4px rgba(99,102,241,0.15); }
    .hint { color: var(--muted); font-size: 0.85rem; margin-top: 4px; }
    .actions {
      display: flex; justify-content: space-between; gap: 16px;
      margin-top: 16px; padding-top: 24px; border-top: 1px solid var(--card-border);
    }
    button {
      cursor: pointer; font-weight: 600; font-size: 1rem; padding: 14px 28px;
      border-radius: 14px; border: none; transition: all 0.2s ease; font-family: inherit;
    }
    button:hover { transform: translateY(-2px); }
    .btn-primary { background: var(--accent-gradient); color: white; box-shadow: 0 10px 20px rgba(99,102,241,0.3); }
    .btn-primary:hover { box-shadow: 0 15px 25px rgba(99,102,241,0.4); }
    .btn-secondary { background: rgba(255,255,255,0.05); color: var(--text); border: 1px solid var(--card-border); }
    .btn-secondary:hover { background: rgba(255,255,255,0.1); }
    .banner { padding: 16px 20px; border-radius: 14px; background: rgba(16,185,129,0.1); border: 1px solid rgba(16,185,129,0.2); color: #34d399; font-weight: 500; line-height: 1.5; }
    .warning { background: rgba(239,68,68,0.1); border-color: rgba(239,68,68,0.2); color: #f87171; font-weight: 500; line-height: 1.5; padding: 16px 20px; border-radius: 14px; }
    @media (max-width: 768px) {
      .progress { grid-template-columns: repeat(2, 1fr); }
      .actions { flex-direction: column-reverse; }
      button { width: 100%; }
      .header h1 { font-size: 1.7rem; }
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h1>UBTG — пошаговая настройка</h1>
      <p>{{SETUP_SUBTITLE}}</p>
    </div>
    {{PROGRESS_HTML}}
    <div class="body">
      <form method="post" id="setupForm">
        {{API_STEP_HTML}}
        {{PROXY_STEP_HTML}}
        {{BOT_STEP_HTML}}
        {{LOGIN_STEP_HTML}}

        <div class="actions">
          <button type="button" class="btn-secondary" id="prevBtn">Назад</button>
          <button type="button" class="btn-primary" id="nextBtn">Далее</button>
          <button type="submit" class="btn-primary" id="submitBtn" style="display:none;">Сохранить и продолжить</button>
        </div>
      </form>
    </div>
  </div>

  <script>
    const steps = Array.from(document.querySelectorAll('.step'));
    const pills = Array.from(document.querySelectorAll('.step-pill'));
    const prevBtn = document.getElementById('prevBtn');
    const nextBtn = document.getElementById('nextBtn');
    const submitBtn = document.getElementById('submitBtn');
    let currentStep = 0;

    function showStep(index) {
      steps.forEach((step, i) => {
        step.classList.toggle('active', i === index);
      });
      pills.forEach((pill, i) => {
        pill.classList.toggle('active', i === index);
      });
      prevBtn.style.display = index === 0 ? 'none' : 'inline-block';
      nextBtn.style.display = index === steps.length - 1 ? 'none' : 'inline-block';
      submitBtn.style.display = index === steps.length - 1 ? 'inline-block' : 'none';
      currentStep = index;
    }

    function validateCurrentStep() {
      const activeStep = document.querySelector('.step.active');
      if (!activeStep) return true;
      
      const stepNum = activeStep.getAttribute('data-step');
      
      if (stepNum === "1") {
        const appIdEl = document.getElementById('app_id');
        const hashIdEl = document.getElementById('hash_id');
        if (appIdEl && hashIdEl) {
            const appId = appIdEl.value.trim();
            const hashId = hashIdEl.value.trim();
            if (!appId || !hashId) {
              alert('Пожалуйста, заполните app_id и hash_id.');
              return false;
            }
            if (!/^\\d+$/.test(appId)) {
              alert('app_id должен состоять только из цифр.');
              return false;
            }
        }
      }
      if (stepNum === "2") {
        const useProxyEl = document.getElementById('use_proxy');
        if (useProxyEl && useProxyEl.value === '1') {
          const addr = document.getElementById('proxy_addr').value.trim();
          const port = document.getElementById('proxy_port').value.trim();
          if (!addr || !port) {
            alert('Если включён прокси, укажите адрес и порт.');
            return false;
          }
        }
      }
      return true;
    }

    prevBtn.addEventListener('click', () => {
      if (currentStep > 0) showStep(currentStep - 1);
    });

    nextBtn.addEventListener('click', () => {
      if (!validateCurrentStep()) return;
      if (currentStep < steps.length - 1) showStep(currentStep + 1);
    });

    document.getElementById('setupForm').addEventListener('submit', (event) => {
      if (!validateCurrentStep()) {
        event.preventDefault();
      }
    });

    showStep(0);
  </script>
</body>
</html>"""
        html = html.replace("{{SETUP_SUBTITLE}}", subtitle_text)
        html = html.replace("{{PROGRESS_HTML}}", '<div class="progress">' + ''.join(progress_html) + '</div>')
        html = html.replace("{{API_STEP_HTML}}", api_step_html)
        html = html.replace("{{PROXY_STEP_HTML}}", proxy_step_html)
        html = html.replace("{{BOT_STEP_HTML}}", bot_step_html)
        html = html.replace("{{LOGIN_STEP_HTML}}", login_step_html)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _render_qr_page(self):
        status_text = getattr(self.server, "qr_status", "Ожидаю QR-код для входа в аккаунт...")
        qr_svg = getattr(self.server, "qr_svg", None)
        qr_url = getattr(self.server, "qr_url", "")
        page_title = "QR-код для входа"
        
        qr_html = ""
        if qr_svg:
            qr_html = f'<div class="qr-box">{qr_svg}</div>'
        else:
            qr_html = '<div class="spinner"></div>'

        html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="3">
  <title>{page_title}</title>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {{ --bg-1: #0f172a; --bg-2: #1e1b4b; --text: #f8fafc; --accent: #6366f1; --card-bg: rgba(30, 41, 59, 0.65); --card-border: rgba(255, 255, 255, 0.1); }}
    body {{ font-family: 'Outfit', sans-serif; margin: 0; background: linear-gradient(135deg, var(--bg-1), var(--bg-2)); color: var(--text); display: grid; place-items: center; min-height: 100vh; padding: 24px; text-align: center; overflow-x: hidden; }}
    body::before {{ content: ''; position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: radial-gradient(circle at 50% 50%, rgba(99,102,241,0.15) 0%, transparent 60%); z-index: -1; pointer-events: none; }}
    .card {{ max-width: 500px; width: 100%; background: var(--card-bg); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); border: 1px solid var(--card-border); border-radius: 24px; padding: 40px 32px; box-shadow: 0 30px 60px rgba(0,0,0,0.4); animation: scaleIn 0.5s cubic-bezier(0.16, 1, 0.3, 1); }}
    @keyframes scaleIn {{ from {{ opacity: 0; transform: scale(0.95); }} to {{ opacity: 1; transform: scale(1); }} }}
    h1 {{ margin: 0 0 16px; font-size: 1.8rem; font-weight: 700; }}
    .banner {{ padding: 16px; border-radius: 14px; background: rgba(99,102,241,0.1); border: 1px solid rgba(99,102,241,0.2); color: #818cf8; margin-bottom: 32px; font-weight: 500; font-size: 1.05rem; line-height: 1.5; }}
    .qr-box {{ background: white; padding: 20px; border-radius: 20px; display: inline-block; box-shadow: 0 0 0 rgba(99,102,241,0.4); border: 4px solid rgba(255,255,255,0.05); animation: pulse 2s infinite; }}
    @keyframes pulse {{ 0% {{ box-shadow: 0 0 0 0 rgba(99,102,241,0.4); }} 70% {{ box-shadow: 0 0 0 15px rgba(99,102,241,0); }} 100% {{ box-shadow: 0 0 0 0 rgba(99,102,241,0); }} }}
    .url {{ word-break: break-all; color: #94a3b8; margin-top: 24px; font-size: 0.85rem; padding: 12px; background: rgba(0,0,0,0.2); border-radius: 10px; }}
    .spinner {{ display: inline-block; width: 40px; height: 40px; border: 4px solid rgba(255,255,255,0.1); border-left-color: var(--accent); border-radius: 50%; animation: loader 1s linear infinite; margin: 20px auto; }}
    @keyframes loader {{ to {{ transform: rotate(360deg); }} }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Вход в Telegram</h1>
    <div class="banner">{status_text}</div>
    {qr_html}
    <div class="url">{qr_url if qr_url else 'Ожидание ссылки...'}</div>
  </div>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    # ИСПРАВЛЕНИЕ: Оставили только один метод do_GET с правильной логикой роутинга
    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path not in ("/", "/setup", "/qr"):
            self.send_error(404, "Not Found")
            return

        # Если запрошен /qr или сервер перешел в режим отображения QR
        if parsed_path.path == "/qr" or getattr(self.server, "show_qr_page", False):
            self._render_qr_page()
            return

        self._render_setup_page()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8", errors="ignore")
        form = parse_qs(raw_body, keep_blank_values=True)

        def first_value(key):
            values = form.get(key, [])
            return values[0].strip() if values else ""

        app_id_input = first_value("app_id")
        hash_id = first_value("hash_id")
        desired_bot = first_value("desired_bot_username").lstrip("@")
        use_proxy = first_value("use_proxy").lower() in {"1", "true", "yes", "да", "д", "y"}

        config_data = {}

        if not NO_API_SETUP:
            try:
                app_id = int(app_id_input)
            except ValueError:
                self._send_text(400, "app_id должен состоять только из цифр.")
                return

            if not hash_id:
                self._send_text(400, "hash_id не может быть пустым.")
                return

            config_data["app_id"] = app_id
            config_data["hash_id"] = hash_id
        else:
            if app_id_input:
                try:
                    config_data["app_id"] = int(app_id_input)
                except ValueError:
                    self._send_text(400, "app_id должен состоять только из цифр.")
                    return
            if hash_id:
                config_data["hash_id"] = hash_id
        if desired_bot:
            config_data["desired_bot_username"] = desired_bot

        if not NO_PROXY_SETUP and use_proxy:
            proxy_type = first_value("proxy_type") or SET_PROXY_PROTOCOL or "http"
            addr = first_value("proxy_addr") or SET_PROXY_IP
            port_text = first_value("proxy_port") or SET_PROXY_PORT
            if not addr or not port_text:
                self._send_text(400, "Если включён прокси, нужно указать адрес и порт.")
                return
            try:
                port = int(port_text)
            except ValueError:
                self._send_text(400, "Порт прокси должен быть числом.")
                return

            proxy_dict = {"proxy_type": proxy_type, "addr": addr, "port": port}
            proxy_username = first_value("proxy_username")
            proxy_password = first_value("proxy_password")
            if proxy_username:
                proxy_dict["username"] = proxy_username
            if proxy_password:
                proxy_dict["password"] = proxy_password
            config_data["proxy"] = proxy_dict

        save_core_config(config_data)
        self.server.config_received_event.set()
        self.server.config_payload = config_data
        self.server.show_qr_page = True
        self.server.qr_status = "Настройки сохранены. Ожидаю QR-код для входа в аккаунт..."
        self.server.qr_svg = None
        self.server.qr_url = ""
        self._send_redirect("/qr")

    def _send_text(self, status_code, text):
        self.send_response(status_code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(text.encode("utf-8"))

    def _send_redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, format, *args):
        return


def start_web_config_server():
    """Запускает локальный веб-сервер для первичной настройки."""
    global WEB_SETUP_SERVER
    if WEB_SETUP_SERVER is not None:
        return WEB_SETUP_SERVER, None

    server = ThreadingHTTPServer((WEB_SETUP_HOST, 0), WebConfigRequestHandler)
    server.daemon_threads = True
    server.config_received_event = threading.Event()
    server.config_payload = None
    server.show_qr_page = False
    server.qr_svg = None
    server.qr_url = ""
    server.qr_status = "Ожидаю настройки..."
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    WEB_SETUP_SERVER = server
    return server, thread


def ensure_web_setup_server():
    """Гарантирует, что веб-сервер для QR-страницы запущен и возвращает его."""
    global WEB_SETUP_SERVER
    if WEB_SETUP_SERVER is not None:
        return WEB_SETUP_SERVER

    server, _ = start_web_config_server()
    print(f"🌐 Откройте страницу QR: http://{WEB_SETUP_HOST}:{server.server_address[1]}/qr")
    return server


def print_web_setup_links(server):
    """Печатает локальную и публичную ссылки для веб-настройки."""
    print(f"🌐 Локальная веб-настройка: http://{WEB_SETUP_HOST}:{server.server_address[1]}/")
    print(f"🌐 QR-страница: http://{WEB_SETUP_HOST}:{server.server_address[1]}/qr")


def set_web_setup_qr(url, status_text):
    """Обновляет веб-страницу QR-кодом для текущего шага входа."""
    global WEB_SETUP_SERVER
    if not WEB_SETUP_SERVER:
        ensure_web_setup_server()
        if not WEB_SETUP_SERVER:
            return

    WEB_SETUP_SERVER.qr_url = url or ""
    WEB_SETUP_SERVER.qr_status = status_text
    WEB_SETUP_SERVER.show_qr_page = True

    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
        qr = qrcode.QRCode(box_size=10, border=4)
        qr.add_data(url or "")
        qr.make(fit=True)
        img = qr.make_image(image_factory=SvgPathImage)
        WEB_SETUP_SERVER.qr_svg = img.to_string(encoding="unicode")
    except Exception:
        WEB_SETUP_SERVER.qr_svg = None


def shutdown_web_setup_server():
    """Останавливает веб-сервер настройки после завершения входа."""
    global WEB_SETUP_SERVER
    if not WEB_SETUP_SERVER:
        return
    try:
        WEB_SETUP_SERVER.shutdown()
        WEB_SETUP_SERVER.server_close()
    except Exception:
        pass
    WEB_SETUP_SERVER = None


def start_localtunnel(port, timeout=20):
    """Запускает localtunnel для временного публичного домена и возвращает URL, если доступно."""
    if shutil.which("npx"):
        command = ["npx", "--yes", "localtunnel", "--port", str(port)]
    elif shutil.which("lt"):
        command = ["lt", "--port", str(port)]
    else:
        return None, None

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    found_url = []
    def read_output():
        if not process.stdout:
            return
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            match = re.search(r"https?://[^\s]+", line)
            if match:
                found_url.append(match.group(0))
                break

    reader_thread = threading.Thread(target=read_output, daemon=True)
    reader_thread.start()

    deadline = time.time() + timeout
    while time.time() < deadline:
        if found_url:
            return process, found_url[0]
        if process.poll() is not None:
            break
        time.sleep(0.25)

    return process, None


def stop_localtunnel(process):
    """Останавливает background-процесс localtunnel, если он был запущен."""
    if not process:
        return
    try:
        process.terminate()
        process.wait(timeout=3)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def wait_for_web_config():
    """Ожидает, пока пользователь сохранит настройки через веб-форму."""
    server, _ = start_web_config_server()
    tunnel_process = None
    tunnel_url = None
    try:
        tunnel_process, tunnel_url = start_localtunnel(server.server_address[1])
    except Exception as e:
        print(f"[Core] ⚠️ Не удалось запустить localtunnel: {e}")

    print("\n" + "="*50)
    print("=== ПЕРВЫЙ ЗАПУСК: ВЕБ-НАСТРОЙКА API ТЕЛЕГРАМА ===")
    print("Откройте браузер по адресу ниже и заполните форму.")
    print("="*50)
    print_web_setup_links(server)
    if tunnel_url:
        print(f"🌍 Публичный временный домен: {tunnel_url}")
    else:
        print("ℹ️ localtunnel недоступен или не успел подняться; используется только локальный адрес.")
    print("Ожидаю сохранения настроек...")
    print("\n💡 Для перехода к QR-коду откройте: http://127.0.0.1:" + str(server.server_address[1]) + "/qr")

    try:
        if server.config_received_event.wait(WEB_SETUP_TIMEOUT):
            if server.config_payload:
                return server.config_payload
    finally:
        stop_localtunnel(tunnel_process)
        if not getattr(server, "config_payload", None):
            shutdown_web_setup_server()

    raise TimeoutError("Время ожидания веб-настройки истекло.")


def load_or_create_config():
    """Загружает конфиг core_conf.json, а если его нет - запрашивает данные через веб или консоль."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                if "app_id" in config and "hash_id" in config:
                    return config
        except Exception as e:
            print(f"[Core] ⚠️ Ошибка при чтении конфига: {e}. Создаем новый.")

    try:
        if apply_preconfigured_credentials():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except ValueError as e:
        print(f"[Core] ⚠️ {e}")
        raise

    if "--no-web" in sys.argv:
        return prompt_for_core_config()

    try:
        return wait_for_web_config()
    except TimeoutError:
        print("[Core] ⏳ Веб-настройка не завершилась вовремя. Переключаюсь на консольный ввод.")
        return prompt_for_core_config()
    except KeyboardInterrupt:
        print("\n[Core] ⚠️ Настройка прервана. Переключаюсь на консольный ввод.")
        return prompt_for_core_config()

# Получаем креды до инициализации клиента
raw_config = load_or_create_config()
API_ID = int(raw_config["app_id"])
API_HASH = raw_config["hash_id"]
proxy_config = get_proxy_config(raw_config)

if proxy_config:
    print(f"[Core] 🌐 Используется прокси ({proxy_config['proxy_type']}://{proxy_config['addr']}:{proxy_config['port']})")
else:
    print("[Core] 🌐 Прокси не используется (не прописан в конфиге)")

# Инициализируем юзербот клиента
client = TelegramClient(
    'my_account',
    API_ID,
    API_HASH,
    device_model="MacBook Pro",
    system_version="macOS 14.5",
    app_version="10.11.1",
    proxy=proxy_config
)

async def auto_setup_bot(userbot_client, me):
    """
    Проверяет наличие токена бота в core_conf.json.
    Если его нет — обращается к @BotFather:
    1. Пробует создать бота с указанным пользователем юзернеймом (если был введён).
    2. Если юзернейм занят/не указан — ищет существующего бота юзербота (/token).
    3. Если подхоящий бот не найден — создаёт нового бота со случайным юзернеймом.
    """
    config = load_or_create_config()
    bot_token = config.get("bot_token")
    bot_username = config.get("bot_username")
    is_first_run = config.get("is_first_run", True)
    desired_username = config.get("desired_bot_username")

    if bot_token and bot_username:
        return bot_token, bot_username, is_first_run

    print("\n[Core] 🤖 Настройка встроенного Telegram Бота...")
    print("[Core] Подключаемся к @BotFather...")

    try:
        bf_entity = await userbot_client.get_input_entity("BotFather")
        async with userbot_client.conversation(bf_entity, timeout=30) as conv:
            found_token = None
            found_username = None

            # --- ЭТАП 1: Попытка создания с указанным юзернеймом ---
            if desired_username:
                target_un = desired_username
                if not target_un.lower().endswith("bot"):
                    target_un = f"{target_un}_bot"

                print(f"[Core] 🎯 Пытаемся создать бота с указанным юзернеймом: @{target_un}...")
                await conv.send_message("/newbot")
                resp = await conv.get_response()

                if any(phrase in resp.text.lower() for phrase in ["name for your bot", "how are we going to call it", "new bot"]):
                    bot_name = f"{me.first_name}'s Assistant"
                    await conv.send_message(bot_name)
                    resp = await conv.get_response()

                    await conv.send_message(target_un)
                    resp = await conv.get_response()

                    match = re.search(r"(\d+:[A-Za-z0-9_-]+)", resp.text)
                    if match:
                        found_token = match.group(1)
                        found_username = target_un
                        print(f"[Core] 🎉 Успешно создан бот с указанным юзернеймом @{found_username}!")
                    else:
                        print(f"[Core] ⚠️ Юзернейм @{target_un} занят или недоступен.")
                        print("[Core] 🔄 Сбрасываем диалог и переходим к авто-поиску/созданию со случайным юзернеймом...")
                        await conv.send_message("/cancel")
                        try:
                            await conv.get_response()
                        except Exception:
                            pass

            # --- ЭТАП 2: Поиск существующего подходящего бота ---
            if not found_token:
                print("[Core] 🔍 Проверяем наличие существующих ботов...")
                await conv.send_message("/token")
                resp = await conv.get_response()

                no_bots_keywords = ["don't have any", "no bots", "у вас нет", "нет ботов", "you have no"]
                has_no_bots = any(kw in resp.text.lower() for kw in no_bots_keywords)

                if not has_no_bots:
                    candidate_bots = []

                    if resp.buttons:
                        for row_idx, row in enumerate(resp.buttons):
                            for col_idx, btn in enumerate(row):
                                btn_text = btn.text or ""
                                m = re.search(r"@?([A-Za-z0-9_]+_bot)", btn_text, re.IGNORECASE)
                                if m:
                                    candidate_bots.append((m.group(1), btn))

                    if not candidate_bots:
                        for m in re.finditer(r"@([A-Za-z0-9_]+_bot)", resp.text, re.IGNORECASE):
                            candidate_bots.append((m.group(1), None))

                    if candidate_bots:
                        # Ищем бота, предназначенного именно для юзербота (содержащего _ub_, userbot или assistant)
                        chosen = None
                        for b in candidate_bots:
                            un_lower = b[0].lower()
                            if "_ub_" in un_lower or "userbot" in un_lower or "assistant" in un_lower:
                                chosen = b
                                break

                        if chosen:
                            target_username, btn_obj = chosen
                            print(f"[Core] 💡 Найден подходящий бот юзербота: @{target_username}")

                            if btn_obj is not None:
                                try:
                                    await btn_obj.click()
                                except Exception:
                                    await conv.send_message(f"@{target_username}")
                            else:
                                await conv.send_message(f"@{target_username}")

                            token_resp = await conv.get_response()
                            match = re.search(r"(\d+:[A-Za-z0-9_-]+)", token_resp.text)
                            if match:
                                found_token = match.group(1)
                                found_username = target_username
                                print(f"[Core] ✅ Успешно получен токен для существующего бота @{found_username}")
                        else:
                            print("[Core] ℹ️ Подходящих ботов для юзербота не найдено среди существующих.")

            # --- ЭТАП 3: Создание нового бота со случайным юзернеймом ---
            if not found_token:
                print("[Core] ➕ Создаем нового бота со случайным юзернеймом...")
                await conv.send_message("/newbot")
                resp = await conv.get_response()

                if any(phrase in resp.text.lower() for phrase in ["name for your bot", "how are we going to call it", "new bot"]):
                    bot_name = f"{me.first_name}'s Assistant"
                    await conv.send_message(bot_name)
                    resp = await conv.get_response()

                    base_un = (me.username or f"user_{me.id}").lower()
                    suffix = random.randint(1000, 9999)
                    target_username = f"{base_un}_ub_{suffix}_bot"
                    await conv.send_message(target_username)
                    resp = await conv.get_response()

                    match = re.search(r"(\d+:[A-Za-z0-9_-]+)", resp.text)
                    if match:
                        found_token = match.group(1)
                        found_username = target_username
                        print(f"[Core] 🎉 Новый бот @{found_username} успешно создан через @BotFather!")

            # --- ЭТАП 3: Настройка Inline-режима ---
            if found_token and found_username:
                try:
                    await conv.send_message("/setinline")
                    setinline_resp = await conv.get_response()
                    if any(w in setinline_resp.text.lower() for w in ["choose a bot", "выберите бота", "which bot"]):
                        await conv.send_message(f"@{found_username}")
                        setinline_resp = await conv.get_response()

                    if any(w in setinline_resp.text.lower() for w in ["placeholder", "текст", "label", "empty inline"]):
                        await conv.send_message("Search")
                        await conv.get_response()
                except Exception as ex_inline:
                    print(f"[Core] ⚠️ Внимание при проверке inline режима: {ex_inline}")

                config["bot_token"] = found_token
                config["bot_username"] = found_username
                config["is_first_run"] = True
                save_core_config(config)
                return found_token, found_username, True

    except Exception as e:
        print(f"[Core] ⚠️ Не удалось автоматически настроить бота через @BotFather: {e}")

    # Фолбэк: если автонастройка не сработало, просим ввод токена ручками
    print("\n" + "="*50)
    print("=== НАСТРОЙКА ВСТРОЕННОГО ТЕЛЕГРАМ БОТА ===")
    print("Создайте бота через @BotFather и вставьте его токен ниже.")
    print("="*50)

    bot_token = input("Введите Bot Token (например: 123456789:ABC...): ").strip()
    
    # Извлекаем username бота через временный клиент в оперативной памяти
    temp_bot = TelegramClient(MemorySession(), API_ID, API_HASH, proxy=proxy_config)
    await temp_bot.start(bot_token=bot_token)
    temp_me = await temp_bot.get_me()
    bot_username = temp_me.username or f"id_{temp_me.id}"
    await temp_bot.disconnect()

    config["bot_token"] = bot_token
    config["bot_username"] = bot_username
    config["is_first_run"] = True
    save_core_config(config)
    print(f"[Core] ✅ Бот @{bot_username} сохранен в {CONFIG_FILE}!\n")
    return bot_token, bot_username, True

def load_modules():
    """Динамически подгружает все .py файлы из папок system_modules и modules"""
    base_dir = os.path.dirname(__file__)
    
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

        if folder_path not in sys.path:
            sys.path.insert(0, folder_path)

        for file in os.listdir(folder_path):
            if file.endswith('.py') and not file.startswith('__'):
                module_name = file[:-3]
                try:
                    importlib.import_module(module_name)
                    
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
                    print(f"[Core] ❌ Ошибка при загрузке модуля '{module_name}': {e}")

async def notify_after_restart():
    """Сообщает об успешном рестарте юзербота."""
    restart_info = pop_restart_info()
    if not restart_info:
        return

    try:
        elapsed = time.time() - restart_info.get("time", time.time())
        text = f"✅ **Успешно перезагружен!**\n⏱ Заняло: `{elapsed:.2f} сек.`"
        await client.edit_message(
            restart_info["chat_id"],
            restart_info["message_id"],
            text
        )
        print("[Core] Уведомление о перезагрузке отправлено.")
        await send_bot_notification(text)
    except Exception as e:
        print(f"[Core] ⚠️ Не удалось отредактировать сообщение после рестарта: {e}")

async def handle_incoming_messages(event):
    """Слушает сообщения и триггерит команды модулей юзербота"""
    if not event.out:
        return

    text = event.raw_text
    print(f"[Debug] Отправлено сообщение: '{text}'")

    if text.startswith('.'):
        parts = text.split(maxsplit=1)
        cmd = parts[0][1:]
        args = parts[1] if len(parts) > 1 else ""

        print(f"[Debug] Обнаружена команда: .{cmd} с аргументами: '{args}'")

        if cmd in modules_repo["commands"]:
            if is_rate_limited():
                rem = get_rate_limit_remaining()
                mins, secs = divmod(rem, 60)
                await event.edit(
                    f"⚠️ **Запросы к Telegram API временно ограничены!**\n"
                    f"⏱ Осталось подождать: `{rem} сек.` (`{mins}м {secs}с`)\n"
                    f"🤖 Уведомление об ограничении отправлено от лица бота."
                )
                return

            try:
                await check_cmd_rate_limit()
                await modules_repo["commands"][cmd](client, event, args)
                print(f"[Debug] Команда .{cmd} успешно выполнена!")
            except errors.FloodWaitError as e:
                print(f"[Core] ⚠️ Пойман FloodWaitError: {e.seconds} сек.")
                await apply_flood_wait(e.seconds, source=f"Команда .{cmd}")
                await event.edit(
                    f"⚠️ **Сработало ограничение Telegram API (FloodWait):** `{e.seconds} сек.`\n"
                    f"🤖 Уведомление с подробностями отправлено от лица бота."
                )
            except PermissionError as pe:
                await event.edit(f"⚠️ {pe}")
            except Exception as e:
                print(f"[Debug] ❌ Ошибка при выполнении .{cmd}: {e}")
                await event.edit(f"**Ошибка в модуле [.{cmd}]:**\n`{e}`")

async def send_bot_status_msg(event):
    """Отправляет актуальный статус юзербота в бот"""
    uptime_sec = int(time.time() - get_userbot_start_time())
    hours, remainder = divmod(uptime_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}ч {minutes}м {seconds}с"

    cpu_usage = psutil.cpu_percent()
    ram_usage = psutil.virtual_memory().percent

    modules_count = len(modules_repo["modules"])
    commands_count = len(modules_repo["commands"])

    status_text = (
        f"⚙️ **Статус UBTG Userbot:**\n\n"
        f"⏱ **Аптайм:** `{uptime_str}`\n"
        f"📦 **Загружено модулей:** `{modules_count}`\n"
        f"🛠 **Всего команд:** `{commands_count}`\n\n"
        f"💻 **Нагрузка системы:**\n"
        f"• CPU: `{cpu_usage}%`\n"
        f"• RAM: `{ram_usage}%`"
    )
    await event.respond(status_text)

def setup_core_bot_handlers(bot_client):
    """Подключает базовые обработчики Telegram Бота"""

    @bot_client.on(events.InlineQuery)
    async def inline_handler(event):
        query = event.text.strip()
        if query in inline_payload_cache:
            payload = inline_payload_cache.pop(query)
            builder = event.builder
            result = builder.article(
                title="Инлайн Вывод",
                text=payload["text"],
                buttons=payload.get("buttons")
            )
            await event.answer([result], cache_time=0)
        else:
            builder = event.builder
            result = builder.article(
                title="Юзербот Ассистент",
                text="🤖 Встроенный бот юзербота активен!",
                buttons=[[Button.url("Юзербот", "https://t.me")]]
            )
            await event.answer([result], cache_time=1)

    @bot_client.on(events.CallbackQuery)
    async def callback_handler(event):
        data = event.data.decode("utf-8") if isinstance(event.data, bytes) else str(event.data)

        if data == "bot_status":
            await event.answer("Загрузка...")
            await send_bot_status_msg(event)
            return
        elif data == "bot_ping":
            await event.answer("Замер пинга...")
            start = time.time()
            diff = (time.time() - start) * 1000
            await event.respond(f"🏓 **ПОНГ!**\n⏱ Отклик бота: `{diff:.2f} мс`")
            return

        # Сортируем префиксы по длине от самых длинных к коротким, чтобы длинные совпадали первыми
        sorted_prefixes = sorted(callback_handlers.keys(), key=len, reverse=True)
        for prefix in sorted_prefixes:
            if data.startswith(prefix):
                handler = callback_handlers[prefix]
                try:
                    await handler(event, data)
                except errors.MessageNotModifiedError:
                    await event.answer()
                except Exception as e:
                    if "not modified" in str(e).lower():
                        await event.answer()
                    else:
                        print(f"[Bot] ❌ Ошибка в обработчике кнопок ({prefix}): {e}")
                        await event.answer(f"Ошибка: {e}", alert=True)
                return

    @bot_client.on(events.NewMessage)
    async def bot_pm_handler(event):
        if not event.is_private:
            return

        owner_id = get_owner_id()
        if owner_id and event.sender_id != owner_id:
            await event.respond("⛔ **Доступ запрещен.** Это персональный бот юзербота.")
            return

        text = event.raw_text.strip()
        if text.startswith("/start") or text.startswith("/help"):
            bot_me = await bot_client.get_me()
            start_msg = (
                f"👋 **Привет! Я встроенный Telegram Бот твоего UBTG Userbot.** (@{bot_me.username})\n\n"
                f"Я являюсь неотъемлемой частью ядра юзербота и присылаю системные уведомления.\n\n"
                f"🛠 **Доступные команды:**\n"
                f"• `/status` — Статус юзербота и системы\n"
                f"• `/ping` — Проверка отклика бота\n"
                f"• `/restart` — Дистанционный перезапуск юзербота\n"
            )
            buttons = [
                [Button.inline("📊 Статус", b"bot_status"), Button.inline("🏓 Пинг", b"bot_ping")]
            ]
            await event.respond(start_msg, buttons=buttons)

        elif text.startswith("/status"):
            await send_bot_status_msg(event)

        elif text.startswith("/ping"):
            start = time.time()
            msg = await event.respond("🏓 Замер пинга...")
            diff = (time.time() - start) * 1000
            await msg.edit(f"🏓 **ПОНГ!**\n⏱ Отклик бота: `{diff:.2f} мс`")

        elif text.startswith("/restart"):
            await event.respond("🔄 **Запущен дистанционный перезапуск юзербота...**")
            save_restart_info(event.chat_id, event.id)
            try:
                await client.disconnect()
            except Exception:
                pass
            python = sys.executable
            script = os.path.abspath(sys.argv[0])
            os.execv(python, [python, script] + sys.argv[1:])

async def main():
    await client.connect()

    if not await client.is_user_authorized():
        print("=== Запуск генерации QR-кода ===")
        qr_login = await client.qr_login()
        server = ensure_web_setup_server()
        
        # ИСПРАВЛЕНИЕ: Запускаем localtunnel здесь, если мы пропустили web config
        tunnel_process = None
        try:
            tunnel_process, tunnel_url = start_localtunnel(server.server_address[1])
            if tunnel_url:
                 print(f"🌍 Публичная ссылка для QR: {tunnel_url}/qr")
        except Exception as e:
             pass

        print_web_setup_links(server)
        set_web_setup_qr(qr_login.url, "Сканируйте QR-код в приложении Telegram для входа в аккаунт.")
        
        try:
            while True:
                try:
                    # Ждем сканирования кода 20 секунд
                    await qr_login.wait(timeout=20)
                    set_web_setup_qr(qr_login.url, "Вход выполнен успешно. Подготовка к запуску...")
                    print("Ура! Успешно залогинились!")
                    break
                except asyncio.TimeoutError:
                    print("[Core] Время жизни QR-кода истекло, генерируем новый (авто-обновление)...")
                    await qr_login.recreate()
                    set_web_setup_qr(qr_login.url, "Время действия предыдущего QR-кода истекло. Отсканируйте новый.")
        except errors.SessionPasswordNeededError:
            set_web_setup_qr(qr_login.url, "Требуется ввод облачного пароля (2FA). Введите его в терминал.")
            password = input("У тебя включен облачный пароль (2FA). Введи его сюда: ")
            await client.sign_in(password=password)
            set_web_setup_qr(qr_login.url, "Вход выполнен успешно. Подготовка к запуску...")
        except Exception as e:
            set_web_setup_qr(qr_login.url, f"Ошибка при входе: {e}")
            print(f"Ошибка при входе: {e}")
            await client.disconnect()
            shutdown_web_setup_server()
            if tunnel_process:
                stop_localtunnel(tunnel_process)
            return
        
        if tunnel_process:
            stop_localtunnel(tunnel_process)

    me = await client.get_me()
    set_owner_id(me.id)
    print(f"[Core] 👤 Владелец: {me.first_name} (ID: {me.id})")

    # Инициализация и автонастройка встроенного Telegram Бота
    bot_token, bot_username, is_first_run = await auto_setup_bot(client, me)
    try:
        bot_client = TelegramClient(
            'bot_session',
            API_ID,
            API_HASH,
            device_model="MacBook Pro",
            system_version="macOS 14.5",
            app_version="10.11.1",
            proxy=proxy_config
        )
        await bot_client.start(bot_token=bot_token)
    except Exception as e:
        print(f"[Core] ⚠️ Сессия бота повреждена или несовместима ({e}). Пересоздаем bot_session.session...")
        if os.path.exists("bot_session.session"):
            try:
                os.remove("bot_session.session")
            except Exception:
                pass
        
        bot_client = TelegramClient(
            'bot_session',
            API_ID,
            API_HASH,
            device_model="MacBook Pro",
            system_version="macOS 14.5",
            app_version="10.11.1",
            proxy=proxy_config
        )
        await bot_client.start(bot_token=bot_token)
        
    set_bot(bot_client, bot_username)
    setup_core_bot_handlers(bot_client)
    print(f"[Core] 🤖 Встроенный Telegram Бот активен (@{bot_username})!")

    # --- ИНИЦИАЛИЗАЦИЯ ДИАЛОГА (Рукопожатие) ---
    try:
        await client.send_message(f"@{bot_username}", "/start")
        await asyncio.sleep(0.5)
    except Exception as e:
        print(f"[Core] ⚠️ Не удалось отправить приветственный /start боту: {e}")

    print("\n[Core] Загружаем модули...")
    load_modules()

    client.add_event_handler(handle_incoming_messages, events.NewMessage(outgoing=True))

    print(f"\n[Core] Запущено команд: {len(modules_repo['commands'])}")
    print(f"[Core] Запущено фоновых задач: {len(modules_repo['background_tasks'])}")
    print("\n=== UBTG Userbot полностью готов к работе! ===")

    for task in modules_repo["background_tasks"]:
        asyncio.create_task(task(client))

    await notify_after_restart()

    # Уведомление владельца через бота при обычном запуске
    if not os.path.exists("restart_info.json"):
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

        if is_first_run:
            welcome_text = (
                f"🎉 **Поздравляем с установкой UBTG Userbot!**\n\n"
                f"🤖 Ваш персональный Telegram Бот (@{bot_username}) успешно привязан и активирован.\n\n"
                f"👤 **Владелец:** [{me.first_name}](tg://user?id={me.id})\n"
                f"⏱ **Время установки:** `{now_str}`\n"
                f"📦 **Загружено модулей:** `{len(modules_repo['modules'])}`\n"
                f"⚙️ **Загружено команд:** `{len(modules_repo['commands'])}`\n\n"
                f"💬 Отправляйте `/start` или `/status` в этот диалог для управления юзерботом."
            )
            await send_bot_notification(welcome_text)
            config = load_or_create_config()
            config["is_first_run"] = False
            save_core_config(config)
        else:
            startup_text = (
                f"🚀 **UBTG Userbot успешно запущен!**\n\n"
                f"👤 **Владелец:** [{me.first_name}](tg://user?id={me.id})\n"
                f"⏱ **Время:** `{now_str}`\n"
                f"📦 **Модулей:** `{len(modules_repo['modules'])}`\n"
                f"⚙️ **Команд:** `{len(modules_repo['commands'])}`"
            )
            await send_bot_notification(startup_text)

    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\n[Core] Юзербот остановлен вручную.")
    finally:
        try:
            bot = get_bot()
            if bot and bot.is_connected():
                client.loop.run_until_complete(bot.disconnect())
        except Exception:
            pass

        try:
            if client and client.is_connected():
                client.loop.run_until_complete(client.disconnect())
        except Exception:
            pass