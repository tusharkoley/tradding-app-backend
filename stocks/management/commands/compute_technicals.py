import os
from datetime import date, timedelta

import numpy as np
import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, transaction

from stocks.models import Company, Price, TechnicalIndicators


class Command(BaseCommand):
    help = (
        "Compute technical indicators (ATR-14, HV-20, VWAP-20, DMA-20/50/200, Beta-252, Alpha-252, "
        "RS-Industry) and upsert into TechnicalIndicators table."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=60,
            help="Only write results for the last N calendar days (0 = full history, default 60)",
        )
        parser.add_argument(
            "--benchmark",
            default="SPY.US",
            help="Benchmark ticker for beta/alpha (default: SPY.US)",
        )
        parser.add_argument(
            "--tickers",
            nargs="+",
            default=None,
            help="Limit computation to specific tickers",
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=2000,
            help="Rows per bulk_create chunk (default 2000)",
        )

    def handle(self, *args, **options):
        days = options["days"]
        benchmark_ticker = options["benchmark"]
        filter_tickers = options.get("tickers")
        chunk_size = options["chunk_size"]

        # ── Load price data ──────────────────────────────────────────────────────
        self.stdout.write("Loading price data from DB...")
        close_old_connections()

        # Fetch enough history for the longest rolling window (252) plus the
        # requested output window so we have valid values on the first output date.
        if days > 0:
            since = date.today() - timedelta(days=days + 380)
            price_qs = Price.objects.filter(date__gte=since)
        else:
            price_qs = Price.objects.all()

        if filter_tickers:
            price_qs = price_qs.filter(ticker__in=filter_tickers)

        prices_df = pd.DataFrame(
            price_qs.values("ticker", "date", "open", "high", "low", "close", "volume")
        )
        if prices_df.empty:
            self.stdout.write(self.style.WARNING("No price data found — nothing to compute."))
            return

        prices_df["date"] = pd.to_datetime(prices_df["date"])
        prices_df = prices_df.sort_values(["ticker", "date"]).reset_index(drop=True)

        # ── Company map: ticker -> (id, normalized industry) ─────────────────
        company_map = {
            c.ticker: (c.id, c.industry or c.soctor or "Unknown")
            for c in Company.objects.only("id", "ticker", "industry", "soctor")
        }

        # ── Benchmark returns ────────────────────────────────────────────────────
        self.stdout.write(f"Loading benchmark {benchmark_ticker}...")
        close_old_connections()
        bench_qs = Price.objects.filter(ticker=benchmark_ticker)
        if days > 0:
            bench_qs = bench_qs.filter(date__gte=since)
        bench_df = pd.DataFrame(bench_qs.values("date", "close"))
        if bench_df.empty:
            self.stdout.write(
                self.style.WARNING(f"Benchmark {benchmark_ticker} not found — beta/alpha will be null.")
            )
            bench_returns = None
        else:
            bench_df["date"] = pd.to_datetime(bench_df["date"])
            bench_df = bench_df.sort_values("date").set_index("date")
            bench_returns = bench_df["close"].pct_change()  # Series indexed by date

        # ── Cross-sectional RS-Industry ──────────────────────────────────────────
        # Strategy: pivot to wide (date × ticker), compute 63-day % return,
        # then rank within industry group for each date — fully vectorised.
        self.stdout.write("Computing industry relative strength (RS)...")
        close_pivot = prices_df.pivot(index="date", columns="ticker", values="close")
        perf_63 = close_pivot.pct_change(63)  # 63 trading days ≈ 1 quarter

        industry_series = pd.Series(
            {t: company_map.get(t, (None, "Unknown"))[1] for t in close_pivot.columns},
            name="industry",
        )

        # Stack to long, join industry, groupby-rank — O(dates × tickers) not O(dates) loops
        perf_long = perf_63.stack(future_stack=True).reset_index()
        perf_long.columns = ["date", "ticker", "perf"]
        perf_long = perf_long.dropna(subset=["perf"])
        perf_long["industry"] = perf_long["ticker"].map(industry_series)
        perf_long["rs_industry"] = (
            perf_long.groupby(["date", "industry"])["perf"]
            .rank(pct=True)
            .mul(100)
        )
        # Build a lookup: (date, ticker) -> rs_industry
        rs_lookup = perf_long.set_index(["date", "ticker"])["rs_industry"]

        # ── Determine output date cutoff ─────────────────────────────────────────
        if days > 0:
            cutoff = pd.Timestamp(date.today() - timedelta(days=days))
        else:
            cutoff = None

        # ── Per-ticker rolling indicators ────────────────────────────────────────
        self.stdout.write("Computing per-ticker indicators (ATR, HV, VWAP, DMA, Beta, Alpha)...")
        tickers = prices_df["ticker"].unique()
        all_records = []

        for ticker in tickers:
            df = (
                prices_df[prices_df["ticker"] == ticker]
                .set_index("date")
                .sort_index()
            )

            # ATR-14
            prev_close = df["close"].shift(1)
            tr = pd.concat(
                [
                    df["high"] - df["low"],
                    (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs(),
                ],
                axis=1,
            ).max(axis=1)
            atr_14 = tr.rolling(14).mean()

            # Historical Volatility-20 (annualised std of log returns)
            log_ret = np.log(df["close"] / df["close"].shift(1))
            hv_20 = log_ret.rolling(20).std() * np.sqrt(252)

            # VWAP-20 (rolling 20-day volume-weighted average price)
            vwap_20 = (
                (df["close"] * df["volume"]).rolling(20).sum()
                / df["volume"].rolling(20).sum()
            )

            # DMA (simple moving average of close)
            dma_20 = df["close"].rolling(20).mean()
            dma_50 = df["close"].rolling(50).mean()
            dma_200 = df["close"].rolling(200).mean()

            # Beta-252 & Alpha-252 vs benchmark
            if bench_returns is not None:
                stock_ret = df["close"].pct_change()
                aligned = pd.concat(
                    [stock_ret.rename("stock"), bench_returns.rename("bench")],
                    axis=1,
                    join="inner",
                ).dropna()

                if len(aligned) >= 30:
                    roll_cov = aligned["stock"].rolling(252).cov(aligned["bench"])
                    roll_var = aligned["bench"].rolling(252).var()
                    beta_252 = (roll_cov / roll_var).reindex(df.index)
                    alpha_252 = (
                        (
                            aligned["stock"].rolling(252).mean()
                            - (roll_cov / roll_var) * aligned["bench"].rolling(252).mean()
                        )
                        * 252
                    ).reindex(df.index)
                else:
                    beta_252 = pd.Series(np.nan, index=df.index)
                    alpha_252 = pd.Series(np.nan, index=df.index)
            else:
                beta_252 = pd.Series(np.nan, index=df.index)
                alpha_252 = pd.Series(np.nan, index=df.index)

            comp_id = company_map.get(ticker, (None,))[0]

            # Write only dates within the requested output window
            write_dates = df.index if cutoff is None else df.index[df.index >= cutoff]

            for dt in write_dates:
                def _f(s):
                    """Float or None, suppressing NaN."""
                    v = s.get(dt) if isinstance(s, pd.Series) else None
                    if v is None or (isinstance(v, float) and np.isnan(v)):
                        return None
                    return float(v)

                rs_val = rs_lookup.get((dt, ticker))
                if rs_val is not None and (isinstance(rs_val, float) and np.isnan(rs_val)):
                    rs_val = None
                elif rs_val is not None:
                    rs_val = float(rs_val)

                all_records.append(
                    TechnicalIndicators(
                        ticker=ticker,
                        date=dt.date(),
                        company_id=comp_id,
                        atr_14=_f(atr_14),
                        hist_volatility_20=_f(hv_20),
                        vwap_20=_f(vwap_20),
                        dma_20=_f(dma_20),
                        dma_50=_f(dma_50),
                        dma_200=_f(dma_200),
                        beta_252=_f(beta_252),
                        alpha_252=_f(alpha_252),
                        rs_industry=rs_val,
                    )
                )

        # ── Bulk upsert in chunks ────────────────────────────────────────────────
        total = len(all_records)
        self.stdout.write(f"Upserting {total:,} records to DB...")
        update_fields = [
            "atr_14", "hist_volatility_20", "vwap_20",
            "dma_20", "dma_50", "dma_200",
            "beta_252", "alpha_252", "rs_industry", "company_id",
        ]
        close_old_connections()
        for i in range(0, total, chunk_size):
            chunk = all_records[i : i + chunk_size]
            try:
                with transaction.atomic():
                    TechnicalIndicators.objects.bulk_create(
                        chunk,
                        update_conflicts=True,
                        unique_fields=["ticker", "date"],
                        update_fields=update_fields,
                    )
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Chunk {i}–{i+chunk_size} failed: {e}"))
                close_old_connections()
                continue

            if i % 10000 == 0 and i > 0:
                self.stdout.write(f"  {i:,}/{total:,} rows upserted")
                close_old_connections()

        self.stdout.write(self.style.SUCCESS(f"Done. {total:,} indicator rows upserted."))
