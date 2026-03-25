@echo off
echo ============================================
echo  Autonomous Ecosystem — Setup
echo ============================================
echo.

:: Check Python 3.11+
python --version 2>nul | findstr /R "3\.1[1-9]\. 3\.[2-9][0-9]\." >nul
if %errorlevel% neq 0 (
    echo [ERROR] Python 3.11+ is required but not found.
    echo Please install Python 3.11 or later from https://python.org
    pause
    exit /b 1
)
echo [OK] Python found
python --version

:: Check Docker Desktop
docker ps >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Docker Desktop is not running.
    echo Please start Docker Desktop and try again.
    pause
    exit /b 1
)
echo [OK] Docker Desktop is running

:: Start containers
echo.
echo Starting PostgreSQL and Redis containers...
cd /d "%~dp0.."
docker-compose up -d
if %errorlevel% neq 0 (
    echo [ERROR] docker-compose up failed
    pause
    exit /b 1
)
echo [OK] Containers started
echo Waiting 5 seconds for services to initialize...
timeout /t 5 /nobreak >nul

:: Create Python virtual environment
echo.
echo Creating Python virtual environment...
if not exist "ecosystem-venv" (
    python -m venv ecosystem-venv
)
echo [OK] Virtual environment ready

:: Activate venv and install dependencies
echo.
echo Installing Python dependencies...
call ecosystem-venv\Scripts\activate.bat
pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [WARNING] Some packages may have failed to install
    echo This is OK if optional packages like browserbase or mcp failed
)
echo [OK] Python dependencies installed

:: Check Node.js
echo.
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] Node.js is not installed.
    echo AgentCard CLI requires Node.js.
    echo Download from: https://nodejs.org
    echo Press any key to continue without Node.js...
    pause >nul
) else (
    echo [OK] Node.js found
    node --version
    echo Installing AgentCard CLI...
    npm install -g @agentcard/cli 2>nul
    if %errorlevel% neq 0 (
        echo [WARNING] AgentCard CLI install failed — purchasing will be disabled
    ) else (
        echo [OK] AgentCard CLI installed
    )
)

:: Apply database schema
echo.
echo Applying database schema...
call ecosystem-venv\Scripts\activate.bat
python -c "import sys; sys.path.insert(0, '.'); from shared.database.db import init_db, run_schema; init_db(); run_schema(); print('[OK] Database schema applied')"
if %errorlevel% neq 0 (
    echo [WARNING] Schema application failed — will retry on first run
)

:: Run connection tests
echo.
echo Running connection tests...
call "%~dp0test_connections.bat"

echo.
echo ============================================
echo  Setup Complete!
echo ============================================
echo.
echo Next steps:
echo   1. Edit config\config.yaml with your API keys
echo   2. Run scripts\test_connections.bat to verify
echo   3. Run scripts\run_all.bat to start
echo.
pause
