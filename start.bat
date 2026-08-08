@echo off
chcp 65001 >nul
rem 启动网页界面。关掉这个窗口即停止服务。
cd /d "%~dp0"
python app.py
pause
