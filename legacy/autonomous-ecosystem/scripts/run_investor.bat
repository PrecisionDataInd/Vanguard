@echo off
echo Starting Investor Bot...
cd /d "%~dp0.."
call ecosystem-venv\Scripts\activate.bat 2>nul

python -c "
import sys
sys.path.insert(0, '.')
from investor_bot.orchestrator import run
run()
"

pause
