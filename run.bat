@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 가상환경이 없습니다. README의 설치 순서를 먼저 실행하세요.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 7860
if errorlevel 1 pause
