@echo off
REM Daily data collection batch for Windows Task Scheduler.
REM Schedule: weekdays 19:00 KST (after KRX close).
chcp 65001 > nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 가상환경이 없습니다. 먼저 uv sync를 실행하세요.
    exit /b 1
)
".venv\Scripts\python.exe" -m tradingbot data pipeline --market KR
if errorlevel 1 (
    echo 일부 소스 수집에 실패했습니다. state\pipeline_log의 최신 JSON을 확인하세요.
    exit /b 1
)
echo 데이터 수집이 완료되었습니다.
exit /b 0
