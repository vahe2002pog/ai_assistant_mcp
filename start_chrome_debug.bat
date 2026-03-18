@echo off
set EDGE="C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not exist %EDGE% set EDGE="C:\Program Files\Microsoft\Edge\Application\msedge.exe"

netstat -ano | findstr ":9222" >nul 2>&1
if %errorlevel%==0 (
    echo Edge debug port 9222 already active.
    exit /b 0
)

echo Closing Edge...
taskkill /f /im msedge.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo Starting Edge with remote debugging...
start "" %EDGE% --remote-debugging-port=9222
timeout /t 3 /nobreak >nul

netstat -ano | findstr ":9222" >nul 2>&1
if %errorlevel%==0 (
    echo Success! Edge running on port 9222.
) else (
    echo Failed to start Edge on port 9222.
)
