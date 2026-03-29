import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import psutil

ROOT = os.path.dirname(os.path.abspath(__file__))
NGROK = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    r"Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe",
)
ENV_FILE = os.path.join(ROOT, ".env")
PORT = int(os.environ.get("PORT", 8000))


def free_port(port):
    for conn in psutil.net_connections(kind="inet"):
        if conn.laddr.port == port and conn.status == "LISTEN":
            try:
                psutil.Process(conn.pid).terminate()
                print(f"[launch] Убит процесс {conn.pid} на порту {port}")
            except Exception as e:
                print(f"[launch] Не удалось убить процесс: {e}")


def update_env_url(url):
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    content = re.sub(r"WEBAPP_URL=.*", f"WEBAPP_URL={url}", content)
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[launch] WEBAPP_URL обновлён: {url}")


def drain(pipe):
    for line in pipe:
        line = line.strip()
        if line:
            print(f"[cloudflared] {line}")


def kill_ngrok():
    for proc in psutil.process_iter(["name", "pid"]):
        if proc.info["name"] and "ngrok" in proc.info["name"].lower():
            try:
                proc.terminate()
                print(f"[launch] Убит ngrok (pid {proc.info['pid']})")
            except Exception:
                pass


def start_tunnel():
    proc = subprocess.Popen(
        [NGROK, "http", str(PORT), "--log", "stdout"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    # Ждём пока ngrok поднимется и получаем URL через локальный API
    url = None
    for _ in range(40):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2) as r:
                data = json.loads(r.read())
                for tunnel in data.get("tunnels", []):
                    if tunnel.get("proto") == "https":
                        url = tunnel["public_url"]
                        break
            if url:
                break
        except Exception:
            pass

    if url is None:
        proc.terminate()
        return None, None

    print(f"[ngrok] Туннель: {url}")
    threading.Thread(target=drain, args=(proc.stdout,), daemon=True).start()
    return proc, url


def keepalive(url):
    """Пингует тоннель каждые 20с чтобы соединение не простаивало."""
    while True:
        time.sleep(20)
        try:
            urllib.request.urlopen(url, timeout=5)
            print("[keepalive] OK")
        except Exception as e:
            print(f"[keepalive] FAIL: {e}")


if __name__ == "__main__":
    print("[launch] Освобождаю порт...")
    free_port(PORT)
    kill_ngrok()

    time.sleep(2)  # ждём пока старый ngrok полностью освободит порт 4040
    print("[launch] Запускаю ngrok...")
    tunnel_proc, url = start_tunnel()

    if not url:
        print("[launch] Туннель не поднялся. Запускаю бота без туннеля...")
    else:
        update_env_url(url)
        threading.Thread(target=keepalive, args=(url,), daemon=True).start()

    print("[launch] Запускаю бота...")
    bot_proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "main.py")])

    print(f"[launch] Жду готовности порта {PORT}...")
    for _ in range(30):
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=1):
                break
        except OSError:
            time.sleep(0.5)
    else:
        print("[launch] Порт так и не поднялся, но продолжаем...")
    print("[launch] Сервер готов.")

    try:
        bot_proc.wait()
    except KeyboardInterrupt:
        print("\n[launch] Остановка...")
    finally:
        bot_proc.terminate()
        if tunnel_proc:
            tunnel_proc.terminate()
