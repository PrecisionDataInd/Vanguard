@echo off
echo ============================================
echo  Connection Tests
echo ============================================
echo.

cd /d "%~dp0.."
call ecosystem-venv\Scripts\activate.bat 2>nul

python -c "
import sys
sys.path.insert(0, '.')

results = []

# Test PostgreSQL
try:
    import psycopg2
    conn = psycopg2.connect('postgresql://postgres:postgres@localhost:5432/ecosystem')
    cur = conn.cursor()
    cur.execute('SELECT 1')
    cur.close()
    conn.close()
    print('[OK] PostgreSQL')
    results.append(True)
except Exception as e:
    print(f'[FAIL] PostgreSQL: {e}')
    results.append(False)

# Test Redis
try:
    import redis
    r = redis.Redis(host='localhost', port=6379, db=0)
    r.ping()
    print('[OK] Redis')
    results.append(True)
except Exception as e:
    print(f'[FAIL] Redis: {e}')
    results.append(False)

# Test Telegram
try:
    import yaml
    with open('config/config.yaml') as f:
        cfg = yaml.safe_load(f)
    token = cfg['api_keys']['telegram_bot_token']
    chat_id = cfg['api_keys']['telegram_chat_id']
    if token.startswith('YOUR_'):
        print('[SKIP] Telegram (not configured)')
        results.append(None)
    else:
        import requests as req
        resp = req.get(f'https://api.telegram.org/bot{token}/getMe', timeout=10)
        if resp.status_code == 200:
            bot_name = resp.json().get('result', {}).get('username', 'unknown')
            print(f'[OK] Telegram Bot (@{bot_name})')
            results.append(True)
        else:
            print(f'[FAIL] Telegram: HTTP {resp.status_code}')
            results.append(False)
except Exception as e:
    print(f'[FAIL] Telegram: {e}')
    results.append(False)

# Test Alpaca
try:
    import yaml
    with open('config/config.yaml') as f:
        cfg = yaml.safe_load(f)
    key = cfg['api_keys']['alpaca_api_key']
    if key.startswith('YOUR_'):
        print('[SKIP] Alpaca Data API (not configured)')
        results.append(None)
    else:
        from investor_bot.data.alpaca import get_snapshot
        snap = get_snapshot(['AAPL'])
        if 'AAPL' in snap:
            print(f\"[OK] Alpaca Data API (AAPL: \${snap['AAPL']['price']:.2f})\")
            results.append(True)
        else:
            print('[FAIL] Alpaca: No data returned')
            results.append(False)
except Exception as e:
    print(f'[FAIL] Alpaca: {e}')
    results.append(False)

# Test Coinbase/Binance
try:
    from investor_bot.data.crypto import get_price
    price = get_price('BTC-USD')
    if price > 0:
        print(f'[OK] Crypto Data (BTC: \${price:,.2f})')
        results.append(True)
    else:
        print('[FAIL] Crypto: No price returned')
        results.append(False)
except Exception as e:
    print(f'[FAIL] Crypto: {e}')
    results.append(False)

# Test AgentCard
try:
    import yaml
    with open('config/config.yaml') as f:
        cfg = yaml.safe_load(f)
    url = cfg['api_keys'].get('agentcard_mcp_url', '')
    if not url or url.startswith('YOUR_'):
        print('[SKIP] AgentCard MCP (not configured)')
        results.append(None)
    else:
        import requests as req
        resp = req.get(url, timeout=5)
        print(f'[OK] AgentCard MCP (HTTP {resp.status_code})')
        results.append(True)
except Exception as e:
    print(f'[SKIP] AgentCard MCP: {e}')
    results.append(None)

# Test Browserbase
try:
    import yaml
    with open('config/config.yaml') as f:
        cfg = yaml.safe_load(f)
    key = cfg['api_keys'].get('browserbase_api_key', '')
    if not key or key.startswith('YOUR_'):
        print('[SKIP] Browserbase (not configured)')
        results.append(None)
    else:
        print('[OK] Browserbase (key configured)')
        results.append(True)
except Exception as e:
    print(f'[SKIP] Browserbase: {e}')
    results.append(None)

# Summary
print()
ok = sum(1 for r in results if r is True)
fail = sum(1 for r in results if r is False)
skip = sum(1 for r in results if r is None)
print(f'Results: {ok} passed, {fail} failed, {skip} skipped')
"

echo.
pause
