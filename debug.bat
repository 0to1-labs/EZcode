@echo off
chcp 65001 >nul
cd /d "%~dp0"
title EZcode · 调试模式(保留控制台以查看报错)

rem ═══════════ 排障入口: 带控制台, 可见 traceback ═══════════
set "PY="
py -X utf8 -c "import requests,webview" >nul 2>&1 && set "PY=py"
if not defined PY python -X utf8 -c "import requests,webview" >nul 2>&1 && set "PY=python"

if not defined PY (
    echo [错误] 未找到已安装依赖的Python解释器
    echo        请在任意Python中执行: pip install requests pywebview
    pause
    exit /b 1
)

echo 正在以调试模式启动(引擎: %PY%)...
%PY% -X utf8 app\client.py
echo.
echo 客户端已退出, 上面如有报错请截图反馈。
pause
