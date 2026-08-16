import math
from datetime import datetime
from pathlib import Path

import pandas as pd
from django.core.management.base import BaseCommand
import numpy as np

from stocks.models import Price


def first_last_prices(df_year):
    # df_year: DataFrame with columns ['ticker','date','close'] for one year
    # returns DataFrame indexed by ticker with first_close and last_close
    df_sorted = df_year.sort_values(["ticker", "date"]) 
    first = df_sorted.groupby("ticker").first().reset_index()
    last = df_sorted.groupby("ticker").last().reset_index()
    merged = pd.merge(first[["ticker", "close"]].rename(columns={"close": "first_close"}),
                      last[["ticker", "close"]].rename(columns={"close": "last_close"}),
                      on="ticker")
    return merged


class Command(BaseCommand):
    help = "Backtest simple yearly 'top-N by previous year performance' strategy"

    def add_arguments(self, parser):
        parser.add_argument("--start-year", type=int, default=2011, help="First investment year (e.g. 2011)")
        parser.add_argument("--end-year", type=int, default=2025, help="Last investment year (inclusive)")
        parser.add_argument("--top-n", type=int, default=10, help="Number of top performers to select each year")
        parser.add_argument("--output", default=None, help="Optional CSV path to write annual returns")
        parser.add_argument("--country", default=None, help="Optional country filter (e.g. 'India' or 'USA')")
        parser.add_argument("--min-price", type=float, default=10.0, help="Exclude tickers with first-year price below this value")

    def handle(self, *args, **options):
        start_year = options["start_year"]
        end_year = options["end_year"]
        top_n = options["top_n"]
        out_path = options["output"]

        if start_year < 1900 or end_year < start_year:
            self.stdout.write(self.style.ERROR("Invalid year range"))
            return

        # We need price data from (start_year-1) through end_year
        load_from = f"{start_year - 1}-01-01"
        load_to = f"{end_year}-12-31"

        base_qs = Price.objects.filter(date__gte=load_from, date__lte=load_to)
        country = options.get("country")
        if country:
            base_qs = base_qs.filter(company__country__iexact=country)
        qs = base_qs.values("ticker", "date", "close")
        min_price = options.get("min_price") or 0.0
        if not qs:
            self.stdout.write(self.style.ERROR("No price data found for the requested range"))
            return

        df = pd.DataFrame.from_records(list(qs))
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
        df["year"] = df["date"].dt.year

        results = []
        portfolio_values = []

        # For each selection year (year0) we pick top performers during year0 and invest for year1
        for selection_year in range(start_year - 1, end_year):
            invest_year = selection_year + 1
            if invest_year > end_year:
                break

            df_sel = df[df["year"] == selection_year][["ticker", "date", "close"]]
            df_inv = df[df["year"] == invest_year][["ticker", "date", "close"]]

            if df_sel.empty:
                self.stdout.write(self.style.WARNING(f"No selection-year data for {selection_year}, skipping"))
                continue

            sel_prices = first_last_prices(df_sel)
            # exclude low-price tickers based on first_close
            if min_price and min_price > 0:
                sel_prices = sel_prices[sel_prices["first_close"] >= min_price]
            # compute selection-year returns, guard against divide-by-zero and NaN/inf
            sel_prices["ret"] = sel_prices["last_close"] / sel_prices["first_close"] - 1.0
            sel_prices["ret"] = sel_prices["ret"].replace([np.inf, -np.inf], np.nan).fillna(0.0)

            # choose top N by selection-year return
            top = sel_prices.sort_values("ret", ascending=False).head(top_n)
            tickers = top["ticker"].tolist()

            # compute investment-year returns for those tickers
            if df_inv.empty:
                self.stdout.write(self.style.WARNING(f"No investment-year data for {invest_year}, skipping"))
                continue

            inv_prices = first_last_prices(df_inv)
            inv_prices = inv_prices.set_index("ticker")

            indiv_returns = []
            available = 0
            for t in tickers:
                if t in inv_prices.index:
                    fc = inv_prices.loc[t, "first_close"]
                    lc = inv_prices.loc[t, "last_close"]
                    # exclude tickers that are below min_price in invest year as well
                    if min_price and not pd.isna(fc) and fc < min_price:
                        # treat as missing (skip)
                        indiv_returns.append(None)
                        continue
                    # guard against NaN/zero first_close
                    if pd.isna(fc) or fc == 0:
                        indiv_returns.append(None)
                        continue
                    r = lc / fc - 1.0
                    # normalize any inf/nan to 0
                    if pd.isna(r) or math.isinf(r):
                        r = 0.0
                    indiv_returns.append(r)
                    available += 1
                else:
                    # missing data for this ticker during invest year: skip
                    indiv_returns.append(None)

            # compute portfolio return as equal-weighted average of available returns
            # treat None as 0 (shouldn't be present after above logic) and compute equal-weighted return
            valid = [r for r in indiv_returns if r is not None]
            if not valid:
                self.stdout.write(self.style.WARNING(f"No valid tickers for invest year {invest_year}, skipping"))
                continue

            port_ret = sum(valid) / len(valid)
            if pd.isna(port_ret) or math.isinf(port_ret):
                port_ret = 0.0

            results.append({
                "selection_year": selection_year,
                "invest_year": invest_year,
                "selected_count": len(tickers),
                "available_count": len(valid),
                "portfolio_return": port_ret,
            })

            country_note = f" (country={country})" if country else ""
            self.stdout.write(f"Selected {len(tickers)} tickers from {selection_year} -> Invest {invest_year}: available {len(valid)}; annual return {port_ret*100:.4f}% {country_note}")

        if not results:
            self.stdout.write(self.style.WARNING("No backtest results produced"))
            return

        df_res = pd.DataFrame(results)
        # sanitize portfolio returns
        df_res["portfolio_return"] = df_res["portfolio_return"].replace([np.inf, -np.inf], np.nan).fillna(0.0)
        # compute cumulative and average
        df_res["one_plus_r"] = 1.0 + df_res["portfolio_return"]
        cumulative = df_res["one_plus_r"].prod() - 1.0
        years = df_res.shape[0]
        cagr = (1.0 + cumulative) ** (1.0 / years) - 1.0 if years > 0 else float("nan")
        avg_arith = df_res["portfolio_return"].mean()

        self.stdout.write(self.style.SUCCESS(f"Backtest period: {start_year} - {end_year} ({years} years)"))
        self.stdout.write(self.style.SUCCESS(f"Cumulative return: {cumulative:.4%}"))
        self.stdout.write(self.style.SUCCESS(f"CAGR: {cagr:.4%}"))
        self.stdout.write(self.style.SUCCESS(f"Average annual (arithmetic): {avg_arith:.4%}"))

        if out_path:
            p = Path(out_path)
            df_res.to_csv(p, index=False)
            self.stdout.write(self.style.SUCCESS(f"Wrote annual results to {p}"))
