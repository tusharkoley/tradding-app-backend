Daily Naked Trader share alerts
==============================

Standalone macOS/Linux batch script using the existing Python dependencies and
SMTP settings. Monitors the default Shares table, including sales and corrections;
excludes the separate spread bet tables and diary text.

Add the recipient to the backend `.env`; reuse existing SMTP credentials:

```dotenv
NAKED_TRADER_EMAIL_TO=you@example.com
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-sender@gmail.com
EMAIL_HOST_PASSWORD=your-gmail-app-password
DEFAULT_FROM_EMAIL=your-sender@gmail.com
```

SMTP uses STARTTLS. Never commit credentials. From the backend directory:

```sh
venv/bin/python scripts/check_naked_trader.py --dry-run
venv/bin/python scripts/check_naked_trader.py
```

The first regular run saves a baseline without emailing historical trades.
Subsequent runs email new/updated rows, including sales, target/stop changes and
corrections. Archived row removals do not trigger emails. Failed fetches, parsing,
or email delivery leave state unchanged for a retry on the next run. A crash
after delivery but before saving can cause a duplicate. Preserve the
`.naked_trader/state.json` file across deployments. Concurrent runs are locked.

Run the baseline command first to create the log directory. Then add to
`crontab -e` for a daily 18:00 check on this machine:

```cron
0 18 * * * /Users/tusharkoley/Desktop/code/tradezens/backend/venv/bin/python /Users/tusharkoley/Desktop/code/tradezens/backend/scripts/check_naked_trader.py >> /Users/tusharkoley/Desktop/code/tradezens/backend/.naked_trader/check.log 2>&1
```

Use paths appropriate to the deployment host. Cron uses the host timezone;
configure Europe/London for UK time. The machine must be awake and online.
Check the log for errors. For hourly checks use `0 * * * *` instead.
Alerts arrive at the next successful check after publication, not at trade time.
The site says table updates may lag the diary by a day or two.

Tests:

```sh
venv/bin/python -m unittest discover -s scripts -p 'test_naked_trader.py'
```
