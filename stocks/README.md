# Technical indicators

Run `python manage.py compute_technicals --days 60` to recompute recent indicators from stored prices. Use `--days 0` only when a full historical recompute is needed. The command does not download missing prices.

RS Industry is the percentile rank of each stock's 63-observation price return among its industry peers with valid returns on the same date. Each ticker uses its own trading observations, so holidays on other exchanges do not shorten its lookback. `--tickers` limits writes, while ranking still includes all peers. A filtered run therefore loads the full price universe.

RS stays null for the first 63 observations and when either endpoint price is missing, non-finite, or non-positive. Do not replace these values with zero or stale scores. The command reports latest output coverage and sample tickers without RS. Missing price history must be backfilled before recomputing.

The latest APIs select the newest dated row per ticker; the RS threshold is applied after that selection. Deploy the API fix to stop historical warm-up rows appearing as the latest RS. Recompute recent indicators to apply the corrected trading-calendar calculation. API caches expire within five minutes; `run_daily_market_refresh` clears the cache after a successful refresh. Failed indicator writes now cause the command to fail instead of reporting success.
