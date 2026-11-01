Shared alert runner
===================

Railway Start Command (backend root containing manage.py):

```sh
python -u manage.py send_alerts --state-dir /data
```

Set Railway Cron Schedule to `0 * * * *` (hourly), use a persistent volume mounted
at `/data`, disable the batch service's HTTP healthcheck and set restart policy
Never. Replace the existing Naked Trader cron service with this runner rather
than running both; separate services/volumes cannot coordinate duplicate sends.
The runner decides which alerts are due, so Railway needs only one cron job.

Configuration: [config.json](config.json). `enabled: false` disables an alert.
Change hours, timezone, markets or top_n here, then redeploy. `ALERT_CONFIG_PATH`
or `--config PATH` selects an alternate JSON file. Optional top-level `recipient`
overrides the environment recipient for both alerts. Otherwise set
`ALERT_EMAIL_TO=tusharkoley@gmail.com`; the existing `NAKED_TRADER_EMAIL_TO` remains
supported as a fallback. Reuse EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER,
EMAIL_HOST_PASSWORD and DEFAULT_FROM_EMAIL. Sender uses SMTP STARTTLS.

Default schedules use Europe/London (automatic summer-time changes):

- Naked Trader: daily 10:00 and 16:00.
- Weekly performers: Friday 22:00, with retries through Sunday if needed.

The hourly UTC Railway trigger checks schedules using the configured local time.
Successful slots are remembered in `/data/sent.json`; failed ones retry on the
next invocation. A missed daily slot is superseded by the next due daily slot;
weekly runs catch up through Sunday, then wait for the next Friday. Config changes
do not resend an already completed slot. A crash after email delivery but before
saving state can cause a duplicate. Locks cover processes sharing this volume.

The existing Naked Trader baseline path `/data/naked-trader/state.json` is reused
when `--state-dir /data` is supplied, so keep the SAME service and volume. No new
historical alert is generated on a first baseline run. If migrating to another
volume, copy that file first. Local default state is `.alerts/`, excluded from Git.

Weekly calculation
------------------

The report has separate US, UK and India rankings, up to 10 each. These are the
best percentage returns among tracked companies with eligible data, even if all
returns are negative. Country aliases follow the existing price loader. This is
not a ranking of every stock listed in those countries.

Return = (Friday split-adjusted close / previous week's last available
split-adjusted close - 1) * 100. The baseline is the previous Friday, falling back
to Monday–Thursday of that prior week for holidays/missing observations. Each row
shows its actual endpoint dates. Endpoints use the SAME Friday for all tickers;
no stale endpoint substitution. Missing/non-positive/non-finite adjusted prices
are excluded, with eligible and excluded counts shown per market. Dividends are
excluded. No raw/adjusted price mixing or currency conversion is performed.

Prerequisites: load Company and Price data for all three countries, and run
`backfill_split_adjusted_prices` after the raw-price loader, finishing before
Friday 22:00 UK time. Confirm the underlying raw history is truly unadjusted;
see `stocks/README.md`. The report does not download data or rebuild adjustments.
If a market has no eligible stocks, no report is sent, the job fails visibly,
and the slot stays retryable. Partial coverage is disclosed in the email. On a
Friday market holiday that market has no Friday prices, so the report is deferred
and ultimately skipped for that week unless Friday data is subsequently loaded.

Preview and verification
------------------------

```sh
python manage.py send_alerts --state-dir /data --dry-run
python manage.py send_alerts --state-dir /data --only weekly_performers --dry-run
python manage.py test stocks.test_alerts --noinput
python -m unittest discover -s scripts -p 'test_naked_trader.py'
```

Preview respects schedules and completed slots; weekly reports are due only
Friday 22:00 through Sunday. Dry-run prints the report when due without sending
email or updating baselines/success records (it creates directories/lock files).

To add an alert, implement a handler in `stocks/alerts/`, register its name in
runner.HANDLERS and add a config entry. A handler must raise on failure and return
only after success. Use the common send_email function for delivery. Available
schedules are daily hours or weekly weekday/hour; no new Railway service is needed.
The original `check_naked_trader` management command remains available.
