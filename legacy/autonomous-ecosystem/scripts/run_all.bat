@echo off
echo ============================================
echo  Starting Autonomous Ecosystem
echo ============================================
echo.

cd /d "%~dp0.."

:: Ensure Docker containers are running
docker-compose up -d 2>nul
timeout /t 3 /nobreak >nul

:: Activate venv
call ecosystem-venv\Scripts\activate.bat 2>nul

echo Starting both systems...
echo.

python -c "
import sys
sys.path.insert(0, '.')
import asyncio
from shared.utils.logging import setup_logging
from shared.database import db
from shared.alerts import telegram
from deal_scout_phase2 import purchaser
from investor_bot.analysis import risk, scorer, screener
from investor_bot.execution import paper
from investor_bot import orchestrator

async def main():
    setup_logging('ecosystem')

    # Initialize shared infrastructure
    db.init_db()
    db.init_redis()
    try:
        db.run_schema()
    except Exception:
        pass

    # Initialize Telegram (single bot for both systems)
    app = await telegram.init_telegram_bot()
    await telegram.send_startup_message()

    # Initialize purchaser (graceful if unavailable)
    try:
        await purchaser.init_purchaser()
    except Exception as e:
        print(f'Purchaser init warning: {e}')

    # Start all loops
    tasks = [
        # Deal Scout Phase 2
        purchaser.purchaser_loop(),
        # Investor Bot scan loops
        orchestrator.scan_loop_aggressive(),
        orchestrator.scan_loop_balanced(),
        orchestrator.scan_loop_steady(),
        # Shared services
        orchestrator.expiry_loop(),
        orchestrator.approval_processor_loop(),
        # Telegram polling
        app.run_polling(drop_pending_updates=True),
    ]

    print('All systems running. Press Ctrl+C to stop.')
    await asyncio.gather(*tasks)

try:
    asyncio.run(main())
except KeyboardInterrupt:
    print('Shutting down...')
"

pause
