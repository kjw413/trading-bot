@echo off
REM Double-click launcher for the trading bot GUI.
REM IMPORTANT: this file must stay in ANSI/CP949 encoding, NOT UTF-8.
REM cmd parses .bat files in the OEM codepage, so UTF-8 Korean text
REM desyncs the parser and garbles the whole file. Comments ASCII-only.
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" goto run

echo 처음 실행이라 프로그램 구성 요소를 설치합니다. 몇 분 걸릴 수 있어요...
py -m uv --version >nul 2>&1
if errorlevel 1 py -m pip install uv
py -m uv sync --extra dev
if errorlevel 1 (
    echo.
    echo 설치에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 실행해 보세요.
    echo 계속 실패하면 README.md 문서를 참고하세요.
    pause
    exit /b 1
)

:run
start "" ".venv\Scripts\pythonw.exe" -m tradingbot gui
