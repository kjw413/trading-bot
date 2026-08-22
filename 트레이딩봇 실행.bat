@echo off
REM Double-click launcher for the trading bot GUI.
REM
REM ENCODING: UTF-8, no BOM, with the "chcp 65001" below before the first
REM Korean line -- the same arrangement as the other two launchers here.
REM Do NOT convert this file back to ANSI/CP949. The comment that used to sit
REM here said cmd parses .bat in the OEM codepage, which describes the old
REM conhost world; Windows 11 opens .bat files in Windows Terminal, whose
REM console is UTF-8 (65001) whatever the registry OEM codepage says, so
REM CP949 bytes print as mojibake. That stale comment is what got the weekly
REM briefing launcher written in CP949 in the first place.
REM
REM Every byte of a UTF-8 Korean character is >= 0x80, so no ASCII
REM metacharacter can appear inside one and a console still sitting in CP949
REM cannot desync on the comments above. Keep *commands* ASCII until the chcp.
REM
REM The echo lines only ever show on a first run, while the virtual
REM environment is still being built. After that this window closes at once,
REM because the GUI is started detached with pythonw.exe.
chcp 65001 > nul
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
