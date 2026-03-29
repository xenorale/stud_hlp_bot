@echo off
chcp 65001 >nul
title Помощник студента ВГУ

echo [start] Проверяю VPN (MantaRay)...
powershell -Command "try { $t = New-Object Net.Sockets.TcpClient; $t.Connect('127.0.0.1', 2080); $t.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ╔══════════════════════════════════════════╗
    echo  ║   MantaRay VPN не запущен!               ║
    echo  ║   Включи VPN и запусти start.bat снова.  ║
    echo  ╚══════════════════════════════════════════╝
    echo.
    pause
    exit /b 1
)
echo [start] VPN активен. Запускаю бота...
echo.

:loop
".venv\Scripts\python.exe" launch.py
echo.
echo [start] Бот завершился. Перезапуск через 5 секунд... (Ctrl+C для выхода)
timeout /t 5 /nobreak >nul
goto loop
