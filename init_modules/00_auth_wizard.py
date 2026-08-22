import os
import sys
import json
import time
import re
import socket
import shutil
import subprocess
import threading
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from telethon import errors

# --- КОНСТАНТЫ И АРГУМЕНТЫ CLI ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "core_conf.json")
AUTH_LINK_FILE = os.path.join(BASE_DIR, "auth_link.txt")
AUTH_URL_FILE = os.path.join(BASE_DIR, "auth_url.txt")
WEB_URL_FILE = os.path.join(BASE_DIR, "web_url.txt")
SETUP_URL_FILE = os.path.join(BASE_DIR, "setup_url.txt")
QR_IMAGE_FILE = os.path.join(BASE_DIR, "qr.png")
QR_SVG_FILE = os.path.join(BASE_DIR, "qr.svg")
ALL_LINK_FILES = (AUTH_LINK_FILE, AUTH_URL_FILE, WEB_URL_FILE, SETUP_URL_FILE, QR_IMAGE_FILE, QR_SVG_FILE)
WEB_SETUP_HOST = "127.0.0.1"
WEB_SETUP_TIMEOUT = 600
DEFAULT_WEB_SETUP_PORT = 8080
SET_WEB_PORT = None

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
    if arg in ("--port", "-p", "--web-port", "--set-web-port", "--set-port") and index + 1 < len(sys.argv):
        try:
            SET_WEB_PORT = int(sys.argv[index + 1])
        except ValueError:
            print(f"[Init:Auth] ⚠️ Некорректный номер порта: {sys.argv[index + 1]}")


def save_core_config(config_data):
    """Сохраняет актуальный конфиг ядра в core_conf.json"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4, ensure_ascii=False)


def apply_preconfigured_credentials():
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

    def _render_setup_page(self):
        show_api_step = not NO_API_SETUP and not (SET_APP_ID or SET_HASH_ID)
        if not show_api_step:
            show_api_step = False
        show_proxy_step = not NO_PROXY_SETUP
        visible_step_count = 3 - (0 if show_api_step else 1) - (0 if show_proxy_step else 1)
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
        progress_html.append('<div class="step-pill">' + str(step_number) + '. Вход в аккаунт</div>')

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

        login_step_html = """
        <div class="step" data-step="3">
          <h2>Шаг 3 — Генерация QR и вход в аккаунт</h2>
          <div class="banner">
            После сохранения настроек в терминале будет показан QR-код для входа в Telegram-аккаунт.
          </div>
          <div class="warning">
            Если включён облачный пароль, после сканирования QR вам потребуется ввести его в консоль или на веб-странице.
          </div>
        </div>
        """

        if not show_api_step and show_proxy_step:
            proxy_step_html = proxy_step_html.replace('class="step"', 'class="step active"')
        elif not show_api_step and not show_proxy_step:
            login_step_html = login_step_html.replace('class="step"', 'class="step active"')

        html = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UBTG — Пошаговая настройка</title>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-1: #0f172a; --bg-2: #1e1b4b;
      --card-bg: rgba(30, 41, 59, 0.65); --card-border: rgba(255, 255, 255, 0.1);
      --text: #f8fafc; --muted: #94a3b8;
      --accent: #6366f1; --accent-gradient: linear-gradient(135deg, #6366f1, #a855f7);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; font-family: 'Outfit', sans-serif; background: linear-gradient(135deg, var(--bg-1), var(--bg-2));
      color: var(--text); min-height: 100vh; display: grid; place-items: center; padding: 24px; overflow-x: hidden;
    }
    body::before { content: ''; position: absolute; top: -20%; left: -10%; width: 50%; height: 50%; background: radial-gradient(circle, rgba(99,102,241,0.2) 0%, transparent 70%); z-index: -1; filter: blur(40px); }
    body::after { content: ''; position: absolute; bottom: -20%; right: -10%; width: 50%; height: 50%; background: radial-gradient(circle, rgba(168,85,247,0.15) 0%, transparent 70%); z-index: -1; filter: blur(40px); }
    .card { width: min(850px, 100%); background: var(--card-bg); backdrop-filter: blur(20px); border: 1px solid var(--card-border); border-radius: 24px; box-shadow: 0 30px 60px rgba(0,0,0,0.4); overflow: hidden; animation: slideUp 0.6s cubic-bezier(0.16, 1, 0.3, 1); }
    @keyframes slideUp { from { opacity: 0; transform: translateY(30px); } to { opacity: 1; transform: translateY(0); } }
    .header { padding: 32px 32px 20px; border-bottom: 1px solid var(--card-border); text-align: center; }
    .header h1 { margin: 0 0 12px; font-size: 2rem; font-weight: 700; background: var(--accent-gradient); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .header p { margin: 0; color: var(--muted); font-size: 1.05rem; }
    .progress { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; padding: 24px 32px 0; }
    .step-pill { text-align: center; padding: 12px 10px; border-radius: 12px; background: rgba(255,255,255,0.05); color: var(--muted); font-size: 0.95rem; font-weight: 500; border: 1px solid transparent; transition: all 0.3s ease; }
    .step-pill.active { color: #fff; background: rgba(99,102,241,0.15); border-color: rgba(99,102,241,0.4); box-shadow: 0 0 15px rgba(99,102,241,0.2); }
    .body { padding: 32px; }
    form { display: grid; gap: 24px; }
    .step { display: none; animation: fadeIn 0.4s ease; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
    .step.active { display: grid; gap: 16px; }
    .step h2 { margin: 0 0 8px; font-size: 1.4rem; font-weight: 600; }
    .step p { margin: 0 0 12px; color: var(--muted); font-size: 0.95rem; line-height: 1.5; }
    .row { display: grid; gap: 8px; }
    label { font-weight: 500; font-size: 0.95rem; color: #e2e8f0; }
    input, select { width: 100%; padding: 14px 16px; border-radius: 14px; border: 1px solid rgba(255,255,255,0.1); background: rgba(15, 23, 42, 0.5); color: var(--text); font-family: inherit; font-size: 1rem; transition: all 0.2s; }
    input:focus, select:focus { outline: none; border-color: var(--accent); background: rgba(15, 23, 42, 0.8); box-shadow: 0 0 0 4px rgba(99,102,241,0.15); }
    .hint { color: var(--muted); font-size: 0.85rem; margin-top: 4px; }
    .actions { display: flex; justify-content: space-between; gap: 16px; margin-top: 16px; padding-top: 24px; border-top: 1px solid var(--card-border); }
    button { cursor: pointer; font-weight: 600; font-size: 1rem; padding: 14px 28px; border-radius: 14px; border: none; transition: all 0.2s ease; font-family: inherit; }
    button:hover { transform: translateY(-2px); }
    .btn-primary { background: var(--accent-gradient); color: white; box-shadow: 0 10px 20px rgba(99,102,241,0.3); }
    .btn-primary:hover { box-shadow: 0 15px 25px rgba(99,102,241,0.4); }
    .btn-secondary { background: rgba(255,255,255,0.05); color: var(--text); border: 1px solid var(--card-border); }
    .btn-secondary:hover { background: rgba(255,255,255,0.1); }
    .banner { padding: 16px 20px; border-radius: 14px; background: rgba(16,185,129,0.1); border: 1px solid rgba(16,185,129,0.2); color: #34d399; font-weight: 500; line-height: 1.5; }
    .warning { background: rgba(239,68,68,0.1); border-color: rgba(239,68,68,0.2); color: #f87171; font-weight: 500; line-height: 1.5; padding: 16px 20px; border-radius: 14px; }
    @media (max-width: 768px) { .progress { grid-template-columns: repeat(2, 1fr); } .actions { flex-direction: column-reverse; } button { width: 100%; } .header h1 { font-size: 1.7rem; } }
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
      <form method="post" action="/setup" id="setupForm">
        {{API_STEP_HTML}}
        {{PROXY_STEP_HTML}}
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
      steps.forEach((step, i) => { step.classList.toggle('active', i === index); });
      pills.forEach((pill, i) => { pill.classList.toggle('active', i === index); });
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
            if (!appId || !hashId) { alert('Пожалуйста, заполните app_id и hash_id.'); return false; }
            if (!/^\\d+$/.test(appId)) { alert('app_id должен состоять только из цифр.'); return false; }
        }
      }
      if (stepNum === "2") {
        const useProxyEl = document.getElementById('use_proxy');
        if (useProxyEl && useProxyEl.value === '1') {
          const addr = document.getElementById('proxy_addr').value.trim();
          const port = document.getElementById('proxy_port').value.trim();
          if (!addr || !port) { alert('Если включён прокси, укажите адрес и порт.'); return false; }
        }
      }
      return true;
    }

    prevBtn.addEventListener('click', () => { if (currentStep > 0) showStep(currentStep - 1); });
    nextBtn.addEventListener('click', () => { if (!validateCurrentStep()) return; if (currentStep < steps.length - 1) showStep(currentStep + 1); });
    document.getElementById('setupForm').addEventListener('submit', (event) => { if (!validateCurrentStep()) event.preventDefault(); });
    showStep(0);
  </script>
</body>
</html>"""
        html = html.replace("{{SETUP_SUBTITLE}}", subtitle_text)
        html = html.replace("{{PROGRESS_HTML}}", '<div class="progress">' + ''.join(progress_html) + '</div>')
        html = html.replace("{{API_STEP_HTML}}", api_step_html)
        html = html.replace("{{PROXY_STEP_HTML}}", proxy_step_html)
        html = html.replace("{{LOGIN_STEP_HTML}}", login_step_html)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _render_bot_setup_page(self):
        """Страница настройки юзернейма бота после входа."""
        html = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Настройка бота</title>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root { --bg-1: #0f172a; --bg-2: #1e1b4b; --text: #f8fafc; --accent: #6366f1; --card-bg: rgba(30, 41, 59, 0.65); --card-border: rgba(255, 255, 255, 0.1); }
    body { font-family: 'Outfit', sans-serif; margin: 0; background: linear-gradient(135deg, var(--bg-1), var(--bg-2)); color: var(--text); display: grid; place-items: center; min-height: 100vh; padding: 24px; text-align: center; overflow-x: hidden; }
    body::before { content: ''; position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: radial-gradient(circle at 50% 50%, rgba(99,102,241,0.15) 0%, transparent 60%); z-index: -1; pointer-events: none; }
    .card { max-width: 500px; width: 100%; background: var(--card-bg); backdrop-filter: blur(20px); border: 1px solid var(--card-border); border-radius: 24px; padding: 40px 32px; box-shadow: 0 30px 60px rgba(0,0,0,0.4); animation: scaleIn 0.5s cubic-bezier(0.16, 1, 0.3, 1); }
    @keyframes scaleIn { from { opacity: 0; transform: scale(0.95); } to { opacity: 1; transform: scale(1); } }
    h1 { margin: 0 0 16px; font-size: 1.8rem; font-weight: 700; }
    p { color: #94a3b8; font-size: 1.05rem; line-height: 1.5; margin-bottom: 24px; }
    input { width: 100%; padding: 16px; border-radius: 14px; border: 1px solid rgba(255,255,255,0.1); background: rgba(15, 23, 42, 0.5); color: var(--text); font-family: inherit; font-size: 1.1rem; margin-bottom: 8px; box-sizing: border-box; transition: all 0.2s; }
    input:focus { outline: none; border-color: var(--accent); background: rgba(15, 23, 42, 0.8); box-shadow: 0 0 0 4px rgba(99,102,241,0.15); }
    .hint { color: var(--muted); font-size: 0.85rem; margin-bottom: 20px; text-align: left; }
    button { width: 100%; cursor: pointer; font-weight: 600; font-size: 1.1rem; padding: 16px; border-radius: 14px; border: none; background: linear-gradient(135deg, #6366f1, #a855f7); color: white; box-shadow: 0 10px 20px rgba(99,102,241,0.3); transition: all 0.2s ease; font-family: inherit; }
    button:hover { transform: translateY(-2px); box-shadow: 0 15px 25px rgba(99,102,241,0.4); }
  </style>
</head>
<body>
  <div class="card">
    <h1>🎉 Вход выполнен!</h1>
    <p>Теперь укажите желаемый юзернейм для встроенного бота. Вы можете оставить поле пустым — бот будет создан автоматически.</p>
    <form method="post" action="/bot_setup">
      <input type="text" name="desired_bot_username" placeholder="my_cool_ub_bot">
      <div class="hint">Например: my_cool_ub_bot</div>
      <button type="submit">Завершить настройку</button>
    </form>
  </div>
</body>
</html>"""
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

    def _render_2fa_page(self):
        """Страница ввода облачного пароля."""
        error_msg = getattr(self.server, "error_msg", None)
        error_html = f'<div class="warning" style="margin-bottom: 20px;">{error_msg}</div>' if error_msg else ""
        
        html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Требуется 2FA пароль</title>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {{ --bg-1: #0f172a; --bg-2: #1e1b4b; --text: #f8fafc; --accent: #6366f1; --card-bg: rgba(30, 41, 59, 0.65); --card-border: rgba(255, 255, 255, 0.1); }}
    body {{ font-family: 'Outfit', sans-serif; margin: 0; background: linear-gradient(135deg, var(--bg-1), var(--bg-2)); color: var(--text); display: grid; place-items: center; min-height: 100vh; padding: 24px; text-align: center; overflow-x: hidden; }}
    body::before {{ content: ''; position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: radial-gradient(circle at 50% 50%, rgba(99,102,241,0.15) 0%, transparent 60%); z-index: -1; pointer-events: none; }}
    .card {{ max-width: 500px; width: 100%; background: var(--card-bg); backdrop-filter: blur(20px); border: 1px solid var(--card-border); border-radius: 24px; padding: 40px 32px; box-shadow: 0 30px 60px rgba(0,0,0,0.4); animation: scaleIn 0.5s cubic-bezier(0.16, 1, 0.3, 1); }}
    @keyframes scaleIn {{ from {{ opacity: 0; transform: scale(0.95); }} to {{ opacity: 1; transform: scale(1); }} }}
    h1 {{ margin: 0 0 16px; font-size: 1.8rem; font-weight: 700; }}
    p {{ color: #94a3b8; font-size: 1.05rem; line-height: 1.5; margin-bottom: 24px; }}
    .warning {{ background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.2); color: #f87171; font-weight: 500; padding: 14px; border-radius: 14px; text-align: left; }}
    input {{ width: 100%; padding: 16px; border-radius: 14px; border: 1px solid rgba(255,255,255,0.1); background: rgba(15, 23, 42, 0.5); color: var(--text); font-family: inherit; font-size: 1.1rem; margin-bottom: 20px; box-sizing: border-box; transition: all 0.2s; }}
    input:focus {{ outline: none; border-color: var(--accent); background: rgba(15, 23, 42, 0.8); box-shadow: 0 0 0 4px rgba(99,102,241,0.15); }}
    button {{ width: 100%; cursor: pointer; font-weight: 600; font-size: 1.1rem; padding: 16px; border-radius: 14px; border: none; background: linear-gradient(135deg, #6366f1, #a855f7); color: white; box-shadow: 0 10px 20px rgba(99,102,241,0.3); transition: all 0.2s ease; font-family: inherit; }}
    button:hover {{ transform: translateY(-2px); box-shadow: 0 15px 25px rgba(99,102,241,0.4); }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Облачный пароль</h1>
    <p>Для этого аккаунта включена двухэтапная аутентификация (2FA). Пожалуйста, введите ваш пароль для продолжения.</p>
    {error_html}
    <form method="post" action="/2fa">
      <input type="password" name="password_2fa" placeholder="Ваш пароль..." required autofocus>
      <button type="submit">Подтвердить и войти</button>
    </form>
  </div>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _render_success_page(self):
        """Страница успешного запуска юзербота."""
        html = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Вход выполнен!</title>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root { --bg-1: #0f172a; --bg-2: #1e1b4b; --text: #f8fafc; --card-bg: rgba(30, 41, 59, 0.65); --card-border: rgba(255, 255, 255, 0.1); }
    body { font-family: 'Outfit', sans-serif; margin: 0; background: linear-gradient(135deg, var(--bg-1), var(--bg-2)); color: var(--text); display: grid; place-items: center; min-height: 100vh; padding: 24px; text-align: center; overflow-x: hidden; }
    body::before { content: ''; position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: radial-gradient(circle at 50% 50%, rgba(16,185,129,0.15) 0%, transparent 60%); z-index: -1; pointer-events: none; }
    .card { max-width: 500px; width: 100%; background: var(--card-bg); backdrop-filter: blur(20px); border: 1px solid var(--card-border); border-radius: 24px; padding: 50px 32px; box-shadow: 0 30px 60px rgba(0,0,0,0.4); animation: scaleIn 0.5s cubic-bezier(0.16, 1, 0.3, 1); }
    @keyframes scaleIn { from { opacity: 0; transform: scale(0.95); } to { opacity: 1; transform: scale(1); } }
    h1 { margin: 0 0 16px; font-size: 2.2rem; font-weight: 700; color: #34d399; }
    p { color: #94a3b8; font-size: 1.1rem; line-height: 1.6; margin: 0; }
    .icon { font-size: 72px; margin-bottom: 20px; animation: bounce 2s infinite ease-in-out; }
    @keyframes bounce { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-10px); } }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h1>Успешно!</h1>
    <p>Юзербот авторизован и запускается.<br>Вы можете безопасно закрыть эту вкладку.</p>
  </div>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def do_GET(self):
        parsed_path = urlparse(self.path)

        # 0. Получение картинки QR-кода
        if parsed_path.path in ("/qr.png", "/qr_code.png"):
            if os.path.exists(QR_IMAGE_FILE):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.end_headers()
                with open(QR_IMAGE_FILE, "rb") as f:
                    self.wfile.write(f.read())
                return
            elif os.path.exists(QR_SVG_FILE):
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.end_headers()
                with open(QR_SVG_FILE, "rb") as f:
                    self.wfile.write(f.read())
                return
            else:
                self.send_error(404, "QR image not found")
                return

        # 1. Страница Успеха
        if getattr(self.server, "show_success_page", False):
            if parsed_path.path != "/success":
                self._send_redirect("/success")
                return
            self._render_success_page()
            return
        elif parsed_path.path == "/success":
            self._send_redirect("/")
            return

        # 2. Страница Настройки Бота
        if getattr(self.server, "show_bot_setup_page", False):
            if parsed_path.path != "/bot_setup":
                self._send_redirect("/bot_setup")
                return
            self._render_bot_setup_page()
            return
        elif parsed_path.path == "/bot_setup":
            self._send_redirect("/")
            return

        # 3. Страница 2FA (Облачный пароль)
        if getattr(self.server, "show_2fa_page", False):
            if parsed_path.path != "/2fa":
                self._send_redirect("/2fa")
                return
            self._render_2fa_page()
            return
        elif parsed_path.path == "/2fa":
            self._send_redirect("/")
            return

        # 4. Страница с QR-кодом
        if getattr(self.server, "show_qr_page", False):
            if parsed_path.path != "/qr":
                self._send_redirect("/qr")
                return
            self._render_qr_page()
            return
        elif parsed_path.path == "/qr":
            self._render_qr_page()
            return

        # 5. Базовая страница настройки
        if parsed_path.path in ("/", "/setup"):
            self._render_setup_page()
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8", errors="ignore")
        form = parse_qs(raw_body, keep_blank_values=True)
        parsed_path = urlparse(self.path)

        def first_value(key):
            values = form.get(key, [])
            return values[0].strip() if values else ""

        # --- Обработка формы Настройки Бота ---
        if parsed_path.path == "/bot_setup":
            desired_bot = first_value("desired_bot_username").lstrip("@")
            config_data = {}
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        config_data = json.load(f)
                except Exception:
                    pass
            
            if desired_bot:
                config_data["desired_bot_username"] = desired_bot
            elif "desired_bot_username" in config_data:
                del config_data["desired_bot_username"]
                
            save_core_config(config_data)
            self.server.bot_setup_event.set()
            show_success_ui()
            self._send_redirect("/success")
            return

        # --- Обработка формы 2FA ---
        if parsed_path.path == "/2fa":
            password_2fa = first_value("password_2fa")
            if password_2fa:
                self.server.password_2fa = password_2fa
                self.server.password_event.set()
                
                # Временно показываем индикатор загрузки, перебрасывая на страницу QR без картинки
                self.server.show_2fa_page = False
                self.server.show_qr_page = True
                self.server.qr_status = "🔄 Проверка пароля..."
                self.server.qr_svg = None
                self._send_redirect("/qr")
            else:
                self.server.error_msg = "Пароль не может быть пустым."
                self._send_redirect("/2fa")
            return

        # --- Обработка формы Первоначальной Настройки ---
        app_id_input = first_value("app_id")
        hash_id = first_value("hash_id")
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


def verify_server_running(host, port, thread, timeout=2.5):
    """
    Проверяет, что фоновый поток веб-сервера жив и сокет принимает входящие TCP соединения.
    """
    if not thread or not thread.is_alive():
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not thread.is_alive():
            return False
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(0.05)

    return False


def create_and_start_web_server(base_port=8080, max_attempts=100):
    """
    Создает и запускает ThreadingHTTPServer, начиная с base_port.
    Если порт занят (OSError/EADDRINUSE), автоматически пробует следующий порт (+1 к номеру занятого порта).
    Перед возвратом гарантирует, что сервер успешно запустился и отвечает на запросы.
    """
    start_port = base_port
    for attempt in range(max_attempts):
        port = start_port + attempt
        try:
            server = ThreadingHTTPServer((WEB_SETUP_HOST, port), WebConfigRequestHandler)
            server.daemon_threads = True
            server.config_received_event = threading.Event()
            server.config_payload = None
            
            # Состояния UI страниц
            server.show_qr_page = False
            server.show_2fa_page = False
            server.show_bot_setup_page = False
            server.show_success_page = False
            
            # Состояния для QR и 2FA
            server.qr_svg = None
            server.qr_url = ""
            server.qr_status = "Ожидаю настройки..."
            server.password_event = threading.Event()
            server.bot_setup_event = threading.Event()
            server.password_2fa = None
            server.error_msg = None

            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            actual_port = server.server_address[1]
            if verify_server_running(WEB_SETUP_HOST, actual_port, thread):
                if attempt > 0:
                    print(f"[Init:Auth] 🌐 Веб-сервер успешно запущен на порту {actual_port} (после смещения на +{attempt})")
                return server, thread
            else:
                try:
                    server.shutdown()
                    server.server_close()
                except Exception:
                    pass
        except OSError as e:
            print(f"[Init:Auth] ⚠️ Порт {port} занят ({e}). Пробую следующий порт {port + 1}...")
            continue
        except Exception as e:
            print(f"[Init:Auth] ⚠️ Ошибка запуска на порту {port}: {e}. Пробую порт {port + 1}...")
            continue

    raise RuntimeError(f"Не удалось запустить веб-сервер после {max_attempts} попыток (порты {start_port}-{start_port + max_attempts - 1}).")


def start_web_config_server():
    """Запускает локальный веб-сервер для первичной настройки с авто-подбором порта."""
    global WEB_SETUP_SERVER
    if WEB_SETUP_SERVER is not None:
        if getattr(WEB_SETUP_SERVER, "server_address", None):
            return WEB_SETUP_SERVER, None

    base_port = SET_WEB_PORT or DEFAULT_WEB_SETUP_PORT
    server, thread = create_and_start_web_server(base_port=base_port)
    WEB_SETUP_SERVER = server
    return server, thread


def dump_web_links(server, tunnel_url=None, path=""):
    """Сохраняет актуальные ссылки на веб-интерфейс в текстовые файлы."""
    if not server or not getattr(server, "server_address", None):
        return
    
    port = server.server_address[1]
    local_url = f"http://{WEB_SETUP_HOST}:{port}{path}"
    
    lines = []
    if tunnel_url:
        public_url = f"{tunnel_url.rstrip('/')}{path}"
        lines.append(public_url)
        lines.append(local_url)
    else:
        lines.append(local_url)
    
    content = "\n".join(lines) + "\n"
    
    for file_path in (AUTH_LINK_FILE, AUTH_URL_FILE, WEB_URL_FILE, SETUP_URL_FILE):
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"[Init:Auth] ⚠️ Не удалось сохранить ссылку в {file_path}: {e}")


def remove_auth_link():
    """Удаляет временные файлы со ссылками авторизации после завершения входа."""
    for file_path in ALL_LINK_FILES:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass


def ensure_web_setup_server():
    """Гарантирует, что веб-сервер для QR-страницы запущен и возвращает его."""
    global WEB_SETUP_SERVER
    if WEB_SETUP_SERVER is not None and getattr(WEB_SETUP_SERVER, "server_address", None):
        return WEB_SETUP_SERVER

    server, _ = start_web_config_server()
    if not server or not getattr(server, "server_address", None):
        raise RuntimeError("Не удалось запустить веб-сервер для QR-авторизации.")

    dump_web_links(server, path="/qr")
    print(f"🌐 Откройте страницу QR: http://{WEB_SETUP_HOST}:{server.server_address[1]}/qr")
    return server


def print_web_setup_links(server):
    """Печатает локальную и публичную ссылки для веб-настройки."""
    if not server or not getattr(server, "server_address", None):
        return
    port = server.server_address[1]
    print(f"🌐 Локальная веб-настройка: http://{WEB_SETUP_HOST}:{port}/")
    print(f"🌐 QR-страница: http://{WEB_SETUP_HOST}:{port}/qr")


def save_qr_image(url):
    """Сохраняет QR-код в файл-картинку (qr.png) в корневой директории."""
    if not url:
        return None
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(url)
        qr.make(fit=True)
        try:
            img = qr.make_image(fill_color="black", back_color="white")
            img.save(QR_IMAGE_FILE)
            return QR_IMAGE_FILE
        except Exception:
            from qrcode.image.svg import SvgPathImage
            img_svg = qr.make_image(image_factory=SvgPathImage)
            img_svg.save(QR_SVG_FILE)
            return QR_SVG_FILE
    except Exception as e:
        print(f"[Init:Auth] ⚠️ Не удалось сохранить QR-код в файл картинки: {e}")
        return None


def update_qr_ui(url, status_text):
    """Обновляет веб-страницу QR-кодом для текущего шага входа и сохраняет в файл-картинку."""
    save_qr_image(url)

    global WEB_SETUP_SERVER
    if not WEB_SETUP_SERVER:
        ensure_web_setup_server()
        if not WEB_SETUP_SERVER:
            return

    WEB_SETUP_SERVER.qr_url = url or ""
    WEB_SETUP_SERVER.qr_status = status_text
    WEB_SETUP_SERVER.show_2fa_page = False
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


def show_2fa_ui(error_msg=None):
    """Переключает веб-сервер в режим отображения формы 2FA."""
    global WEB_SETUP_SERVER
    if WEB_SETUP_SERVER:
        WEB_SETUP_SERVER.error_msg = error_msg
        WEB_SETUP_SERVER.show_qr_page = False
        WEB_SETUP_SERVER.show_bot_setup_page = False
        WEB_SETUP_SERVER.show_success_page = False
        WEB_SETUP_SERVER.show_2fa_page = True


def show_bot_setup_ui():
    """Переключает веб-сервер в режим отображения настройки бота."""
    global WEB_SETUP_SERVER
    if WEB_SETUP_SERVER:
        WEB_SETUP_SERVER.show_qr_page = False
        WEB_SETUP_SERVER.show_2fa_page = False
        WEB_SETUP_SERVER.show_success_page = False
        WEB_SETUP_SERVER.show_bot_setup_page = True


def show_success_ui():
    """Переключает веб-сервер в режим отображения страницы успеха."""
    global WEB_SETUP_SERVER
    if WEB_SETUP_SERVER:
        WEB_SETUP_SERVER.show_qr_page = False
        WEB_SETUP_SERVER.show_2fa_page = False
        WEB_SETUP_SERVER.show_bot_setup_page = False
        WEB_SETUP_SERVER.show_success_page = True


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
    remove_auth_link()


def start_localtunnel(port, timeout=3):
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
    if not server or not getattr(server, "server_address", None):
        raise RuntimeError("Не удалось запустить веб-сервер для первоначальной настройки.")

    actual_port = server.server_address[1]

    # 1. Генерируем локальные ссылки
    dump_web_links(server, path="/")
    tunnel_process = None
    tunnel_url = None
    try:
        tunnel_process, tunnel_url = start_localtunnel(actual_port)
        if tunnel_url:
            dump_web_links(server, tunnel_url=tunnel_url, path="/")
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
    print(f"📁 Ссылка для веб-настройки сохранена в файл: {AUTH_LINK_FILE}")
    print("Ожидаю сохранения настроек...")
    print(f"\n💡 Для перехода к QR-коду откройте: http://{WEB_SETUP_HOST}:{actual_port}/qr")

    try:
        if server.config_received_event.wait(WEB_SETUP_TIMEOUT):
            if server.config_payload:
                return server.config_payload
    finally:
        stop_localtunnel(tunnel_process)
        if not getattr(server, "config_payload", None):
            shutdown_web_setup_server()

    raise TimeoutError("Время ожидания веб-настройки истекло.")


def setup_qr_web_ui():
    """Создает и настраивает веб-интерфейс для QR кода."""
    server = ensure_web_setup_server()
    if not server or not getattr(server, "server_address", None):
        raise RuntimeError("Не удалось запустить веб-сервер для QR-кода.")

    actual_port = server.server_address[1]
    dump_web_links(server, path="/qr")
    tunnel_process = None
    tunnel_url = None
    try:
        tunnel_process, tunnel_url = start_localtunnel(actual_port)
        if tunnel_url:
            dump_web_links(server, tunnel_url=tunnel_url, path="/qr")
            print(f"🌍 Публичная ссылка для QR: {tunnel_url}/qr")
    except Exception:
        pass
    print_web_setup_links(server)
    print(f"📁 Ссылка на веб-интерфейс сохранена в файл: {AUTH_LINK_FILE}")
    return server, tunnel_process


def close_qr_ui(server, tunnel_process):
    """Останавливает UI и туннель для QR и удаляет временные файлы со ссылкой."""
    shutdown_web_setup_server()
    if tunnel_process:
        stop_localtunnel(tunnel_process)
    remove_auth_link()


# ==========================================
# ИНТЕРФЕЙС INIT-МОДУЛЯ ДЛЯ ЯДРА
# ==========================================

def setup_config():
    """Синхронный хук, вызывается ядром до создания TelegramClient"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                if "app_id" in config and "hash_id" in config:
                    return config
        except Exception as e:
            print(f"[Init:Auth] ⚠️ Ошибка при чтении конфига: {e}. Создаем новый.")

    try:
        if apply_preconfigured_credentials():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except ValueError as e:
        print(f"[Init:Auth] ⚠️ {e}")
        raise

    if "--no-web" in sys.argv:
        return prompt_for_core_config()

    try:
        return wait_for_web_config()
    except TimeoutError:
        print("[Init:Auth] ⏳ Веб-настройка не завершилась вовремя. Переключаюсь на консольный ввод.")
        return prompt_for_core_config()
    except KeyboardInterrupt:
        print("\n[Init:Auth] ⚠️ Настройка прервана. Переключаюсь на консольный ввод.")
        return prompt_for_core_config()

async def pre_auth(client):
    """Асинхронный хук, вызывается ядром сразу после запуска TelegramClient, но до авторизации."""
    if await client.is_user_authorized():
        # Если юзер уже авторизован, молча пропускаем этот этап
        return

    print("=== Запуск генерации QR-кода ===")
    qr_login = await client.qr_login()
    
    # Немедленно генерируем и сохраняем картинку qr.png на диске
    save_qr_image(qr_login.url)
    print(f"🔗 Telegram-ссылка для авторизации: {qr_login.url}")
    if os.path.exists(QR_IMAGE_FILE):
        print(f"🖼 QR-код сохранен в файл: {QR_IMAGE_FILE}")
    elif os.path.exists(QR_SVG_FILE):
        print(f"🖼 QR-код сохранен в файл: {QR_SVG_FILE}")

    server, tunnel_process = setup_qr_web_ui()
    update_qr_ui(qr_login.url, "Сканируйте QR-код в приложении Telegram для входа в аккаунт.")
    
    try:
        while True:
            try:
                await qr_login.wait(timeout=20)
                
                show_bot_setup_ui()
                print("[Init:Auth] Ура! Успешно залогинились!")
                print("[Init:Auth] Вы можете задать юзернейм бота в браузере или прямо здесь, в консоли (Enter для авто-генерации):")
                
                def bot_username_thread(srv):
                    b_user = input("Желаемый юзернейм бота: ").strip().lstrip("@")
                    if not srv.bot_setup_event.is_set():
                        config_data = {}
                        if os.path.exists(CONFIG_FILE):
                            try:
                                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                                    config_data = json.load(f)
                            except Exception: pass
                        if b_user:
                            config_data["desired_bot_username"] = b_user
                        elif "desired_bot_username" in config_data:
                            del config_data["desired_bot_username"]
                        save_core_config(config_data)
                        srv.bot_setup_event.set()
                        show_success_ui()

                threading.Thread(target=bot_username_thread, args=(server,), daemon=True).start()
                
                while not server.bot_setup_event.is_set():
                    await asyncio.sleep(0.5)

                print("[Init:Auth] Настройка завершена!")
                # Даем браузеру время сделать refresh и загрузить страницу /success
                await asyncio.sleep(4) 
                break
            except asyncio.TimeoutError:
                print("[Init:Auth] Время жизни QR-кода истекло, генерируем новый (авто-обновление)...")
                await qr_login.recreate()
                save_qr_image(qr_login.url)
                update_qr_ui(qr_login.url, "Время действия предыдущего QR-кода истекло. Отсканируйте новый.")
                print(f"🔗 Новая Telegram-ссылка для авторизации: {qr_login.url}")
                if os.path.exists(QR_IMAGE_FILE):
                    print(f"🖼 Обновленный QR-код сохранен в файл: {QR_IMAGE_FILE}")
                elif os.path.exists(QR_SVG_FILE):
                    print(f"🖼 Обновленный QR-код сохранен в файл: {QR_SVG_FILE}")
    except errors.SessionPasswordNeededError:
        error_msg = None
        while True:
            show_2fa_ui(error_msg)
            server.password_event.clear()
            
            print(f"[Init:Auth] 🔒 Требуется облачный пароль (2FA)!")
            print(f"[Init:Auth] Вы можете ввести его в браузере ИЛИ прямо здесь, в консоли.")
            
            # Запускаем ввод пароля в консоли как фоновый процесс, чтобы не заблокировать веб-сервер
            def console_input_thread(srv):
                pwd = input("Ваш 2FA пароль: ").strip()
                if not srv.password_event.is_set() and pwd:
                    srv.password_2fa = pwd
                    srv.password_event.set()
            
            threading.Thread(target=console_input_thread, args=(server,), daemon=True).start()
            
            # Ждем ввода либо с веб-страницы, либо из консоли
            while not server.password_event.is_set():
                await asyncio.sleep(0.5)
                
            password = server.password_2fa
            
            try:
                await client.sign_in(password=password)
                
                show_bot_setup_ui()
                print("[Init:Auth] Ура! Успешно залогинились (с облачным паролем)!")
                print("[Init:Auth] Вы можете задать юзернейм бота в браузере или прямо здесь, в консоли (Enter для авто-генерации):")
                
                def bot_username_thread_2fa(srv):
                    b_user = input("Желаемый юзернейм бота: ").strip().lstrip("@")
                    if not srv.bot_setup_event.is_set():
                        config_data = {}
                        if os.path.exists(CONFIG_FILE):
                            try:
                                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                                    config_data = json.load(f)
                            except Exception: pass
                        if b_user:
                            config_data["desired_bot_username"] = b_user
                        elif "desired_bot_username" in config_data:
                            del config_data["desired_bot_username"]
                        save_core_config(config_data)
                        srv.bot_setup_event.set()
                        show_success_ui()

                threading.Thread(target=bot_username_thread_2fa, args=(server,), daemon=True).start()
                
                while not server.bot_setup_event.is_set():
                    await asyncio.sleep(0.5)

                print("[Init:Auth] Настройка завершена!")
                await asyncio.sleep(4)
                break
            except errors.PasswordHashInvalidError:
                error_msg = "Неверный пароль. Попробуйте еще раз."
                print(f"\n[Init:Auth] ❌ {error_msg}")
            except Exception as e:
                error_msg = f"Произошла ошибка при входе: {e}"
                print(f"\n[Init:Auth] ❌ {error_msg}")

    except Exception as e:
        update_qr_ui(qr_login.url, f"Ошибка при входе: {e}")
        print(f"[Init:Auth] Ошибка при входе: {e}")
        await client.disconnect()
        close_qr_ui(server, tunnel_process)
        sys.exit(1)
    
    close_qr_ui(server, tunnel_process)