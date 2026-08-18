@echo off
REM Double-click launcher for the weekly account briefing.
REM IMPORTANT: this file must stay in ANSI/CP949 encoding, NOT UTF-8.
REM cmd parses .bat files in the OEM codepage, so UTF-8 Korean text
REM desyncs the parser and garbles the whole file. Comments ASCII-only.
REM Runs python.exe rather than pythonw.exe on purpose: when Telegram
REM delivery fails, this console is the only place the briefing is left,
REM so it has to stay visible. Same reason for the pause at the end.
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto run

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
".venv\Scripts\python.exe" -m tradingbot briefing weekly
echo.
if errorlevel 1 (
    echo 브리핑을 폰으로 보내지 못했습니다. 위에 적힌 내용을 읽어보세요.
    echo 계좌 기록은 저장되었으니, 문제를 고친 뒤 다시 실행하면 됩니다.
) else (
    echo 브리핑을 폰으로 보냈습니다. 이 창은 닫아도 됩니다.
)
pause
