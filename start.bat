@echo off
chcp 65001 >nul
title Помощник студента ВГУ

echo [start] Запускаю бота...
echo.

:loop
".venv\Scripts\python.exe" launch.py
echo.
echo [start] Бот завершился. Перезапуск через 5 секунд... (Ctrl+C для выхода)
timeout /t 5 /nobreak >nul
goto loop
