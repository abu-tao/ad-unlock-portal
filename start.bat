@echo off
chcp 65001 >nul
title AD 域控自助解锁平台
cd /d %~dp0

rem 首次运行自动创建虚拟环境
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] 正在创建虚拟环境...
    python -m venv .venv
)

echo [2/3] 正在安装依赖...
call ".venv\Scripts\activate.bat"
pip install -r requirements.txt -q

echo [3/3] 正在启动服务（http://0.0.0.0:5000）...
echo 按 Ctrl+C 停止
python app.py
pause
