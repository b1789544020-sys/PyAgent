@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title PyAgent 本地知识工作台

where python >nul 2>nul
if errorlevel 1 (
    echo [PyAgent] 找不到 Python。请先安装 Python 3，并勾选 Add to PATH。
    goto :failed
)

echo [PyAgent] 正在检查本地 Ollama 服务，请勿关闭此窗口……
powershell -NoProfile -ExecutionPolicy Bypass -File ".\start_ollama_cpu.ps1"
if errorlevel 1 goto :failed

echo [PyAgent] 正在启动浏览器界面。首次回答需要加载模型，请耐心等待。
echo [PyAgent] 关闭此窗口或按 Ctrl+C 会停止网页服务；不会停止独立 Ollama 服务。
python ".\03_agent\web_ui.py"
if errorlevel 1 goto :failed
exit /b 0

:failed
echo.
echo [PyAgent] 启动未完成。请保留并查看上方错误信息。
pause
exit /b 1
