@echo off
echo Starting Deal Scout Phase 2...
cd /d "%~dp0.."
call ecosystem-venv\Scripts\activate.bat 2>nul

python -c "
import sys
sys.path.insert(0, '.')
import asyncio
from shared.utils.logging import setup_logging
from shared.database import db
from shared.alerts import telegram
from deal_scout_phase2 import purchaser

async def main():
    setup_logging('deal_scout_phase2')
    db.init_db()
    db.init_redis()
    try:
        db.run_schema()
    except Exception:
        pass
    app = await telegram.init_telegram_bot()
    await telegram.send_startup_message()
    available = await purchaser.init_purchaser()
    print(f'Purchaser available: {available}')
    await asyncio.gather(
        purchaser.purchaser_loop(),
        telegram.expire_old_approvals(),
        app.run_polling(drop_pending_updates=True),
    )

asyncio.run(main())
"

pause
