@echo off
rem ═══════════ EZcode 桌面客户端 · 静默启动 ═══════════
rem 优先拉起已安装的 EZcode.exe; 未安装时自动探测本机 Python 跑源码。
if exist "%LOCALAPPDATA%\Programs\EZcode\EZcode.exe" (
  start "" "%LOCALAPPDATA%\Programs\EZcode\EZcode.exe"
) else (
  cd /d "%~dp0"
  where pyw >nul 2>&1
  if %errorlevel%==0 (start "" pyw -X utf8 "app\client.py" & goto :eof)
  where pythonw >nul 2>&1
  if %errorlevel%==0 (start "" pythonw -X utf8 "app\client.py" & goto :eof)
  start "" python -X utf8 "app\client.py"
)
exit
