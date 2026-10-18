# Technical indicators

Run `python manage.py compute_technicals --days 60` to recompute recent indicators from stored prices. Use `--days 0` only when a full historical recompute is needed. The command does not download missing prices.

RS Industry is the percentile rank of each stock's 63-observation price return among its industry peers with valid returns on the same date. Each ticker uses its own trading observations, so holidays on other exchanges do not shorten its lookback. `--tickers` limits writes, while ranking still includes all peers. A filtered run therefore loads the full price universe.

RS stays null for the first 63 observations and when either endpoint price is missing, non-finite, or non-positive. Do not replace these values with zero or stale scores. The command reports latest output coverage and sample tickers without RS. Missing price history must be backfilled before recomputing.

The latest APIs select the newest dated row per ticker; the RS threshold is applied after that selection. Deploy the API fix to stop historical warm-up rows appearing as the latest RS. Recompute recent indicators to apply the corrected trading-calendar calculation. API caches expire within five minutes; `run_daily_market_refresh` clears the cache after a successful refresh. Failed indicator writes now cause the command to fail instead of reporting success.

# Fill missing company profiles from Yahoo Finance

From the repository root, preview a small sample:

```sh
.venv/bin/python backend/manage.py enrich_companies_yahoo --dry-run --limit 3
```

Run once for all incomplete companies:

```sh
.venv/bin/python -u backend/manage.py enrich_companies_yahoo
```

The command preserves populated fields and stored tickers, converts `.US`, `.NSE`, and `.LSE` tickers for Yahoo, and fills descriptions, websites, addresses, names, countries, sectors, and industry subgroups where available. It preserves the app's convention of storing broad sector groups in `industry`. Text is limited to existing database field lengths (descriptions: 2,000 characters). It does not create companies or change prices or technical indicators.

Use `--tickers AAPL.US RELIANCE.NSE`, `--limit`, `--sleep 2`, or `--retries 2` to control a run. Each run prints the path to an exclusive JSONL audit file under `/tmp`, recording original and proposed values before writes and errors per company. Save that file elsewhere if you need a permanent audit. `--audit-file PATH` chooses another location; it must not already exist. Updates commit per company, so completed companies remain updated if interrupted. Re-running fills remaining gaps. Five consecutive failures stop the run. Yahoo may not provide every field for every company. Cached browser data can remain visible until its 12-hour freshness period expires.

# Stock API access restrictions

All `/stocks/` endpoints (including company lists/details, historical/latest prices, technicals and industry rankings) require the exact `Origin: https://tradezens.com` header. Missing origins, `null`, HTTP origins, other domains and `www` are rejected with 403. This also applies to authenticated/staff requests. CORS and CSRF trusted origins now use this same allowlist; the old `FRONTEND_ORIGINS` setting no longer controls it. Direct browser navigation, Swagger tools and scripts must supply the allowed origin. This header is a browser-origin restriction, not authentication: a non-browser client can forge it. Existing write permissions still apply.

Each IP shares 20 stock requests in any rolling 60 seconds, across all endpoints, users, workers and replicas. Request 21 receives 429 with `Retry-After`. Cached responses also consume the budget; allowed OPTIONS preflights do not. Users behind the same public IP share a budget. Rotating tokens cannot reset it. Rate-limit storage errors fail closed with 503.

**Before deploying the updated backend**, apply its migration to the configured PostgreSQL database:

```sh
.venv/bin/python backend/manage.py migrate stocks
```

The strict limiter uses a shared database row lock and requires PostgreSQL (or another database with real `SELECT FOR UPDATE` support). SQLite deliberately returns 503 for stock requests. No Redis service is needed. Run this cleanup periodically, for example daily:

```sh
.venv/bin/python backend/manage.py cleanup_stock_rate_windows
```

Production defaults accept only `https://tradezens.com`. For local frontend development only, explicitly set `STOCK_API_ALLOWED_ORIGIN=http://localhost:5173` in the local backend environment; do not apply that override in production. The value is a single origin, never a comma-separated list or wildcard.

On Railway, `RAILWAY_ENVIRONMENT_ID` automatically enables `STOCK_API_TRUST_RAILWAY_PROXY=true`: the limiter uses the edge's `X-Real-IP` header. Enable this only when the service is reachable exclusively through an edge that overwrites this header. Outside Railway it defaults to false and uses the socket's remote address, ignoring caller-supplied forwarding headers. Keep this trust setting false for direct connections; otherwise forged headers could reset the budget.

Validate the restrictions without network/database access:

```sh
.venv/bin/python backend/manage.py test stocks.test_api_security --noinput
```
