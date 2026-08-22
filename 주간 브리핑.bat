@echo off
REM Double-click launcher for the weekly account briefing.
REM
REM ENCODING: UTF-8, no BOM, and the chcp line below is what makes that safe --
REM same arrangement as "데이터 수집.bat". Do NOT convert this file to ANSI/CP949:
REM Windows 11 opens .bat files in Windows Terminal, whose console is UTF-8 (65001)
REM whatever the registry OEM codepage says, so CP949 bytes print as garbage.
REM Every byte of a UTF-8 Korean character is >= 0x80, so no ASCII
REM metacharacter can appear inside one and a console still sitting in CP949
REM cannot desync on the comments above. Keep *commands* ASCII until the chcp.
REM
REM Runs python.exe rather than pythonw.exe on purpose: when Telegram delivery
REM fails, this console is the only place the briefing is left, so it has to
REM stay visible. Same reason for the pause at the end.
chcp 65001 > nul
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
    echo 문제를 고친 뒤 이 파일을 다시 실행하면 됩니다.
) else (
    echo 브리핑을 폰으로 보냈습니다. 이 창은 닫아도 됩니다.
)
pause
