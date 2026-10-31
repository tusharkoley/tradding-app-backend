from django.db import models

# Create your models here.

class Company(models.Model):
    id = models.AutoField(primary_key=True)
    ticker = models.CharField(max_length=20, db_index=True)
    company_name = models.CharField(max_length=200)
    soctor = models.CharField(max_length=200, null=True)
    industry = models.CharField(max_length=200, null=True)
    industry_subgroup = models.CharField(max_length=200, null=True, blank=True)
    description = models.CharField(max_length=2000)
    country = models.CharField(max_length=100, db_index=True)
    website = models.URLField(max_length=200,null=True)
    address = models.CharField(max_length=300)

    class Meta:
        indexes = [
            models.Index(fields=["industry", "country"]),
        ]
    


class Price(models.Model):
    id = models.AutoField(primary_key=True)
    ticker = models.CharField(max_length=20, db_index=True)
    date = models.DateField(db_index=True)
    open = models.FloatField()
    close = models.FloatField()
    high = models.FloatField()
    low = models.FloatField()
    volume = models.BigIntegerField()
    stock_splits = models.FloatField()
    dividends = models.FloatField()

    company = models.ForeignKey('Company', related_name='company', on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["ticker", "date"], name="unique_ticker_date"),
        ]
        indexes = [
            models.Index(fields=["ticker", "-date"]),
            models.Index(fields=["date", "ticker"]),
        ]


class StockSplit(models.Model):
    """An effective-date split; ratio is new shares per old share (4 for 4:1)."""

    ticker = models.CharField(max_length=20)
    date = models.DateField(help_text="First trading date on the post-split basis")
    ratio = models.DecimalField(max_digits=24, decimal_places=12)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["ticker", "date"], name="unique_stock_split_ticker_date"),
            models.CheckConstraint(condition=models.Q(ratio__gt=0), name="stock_split_ratio_positive"),
        ]
        indexes = [models.Index(fields=["ticker", "-date"])]

    def __str__(self):
        return f"{self.ticker} {self.date} ({self.ratio}:1)"


class SplitAdjustedPrice(models.Model):
    """Stored split-only OHLC, with ticker/date available through the raw price.

    adjusted OHLC = raw OHLC * adjustment_factor. Rebuild from raw prices
    when split history changes; this model does not apply adjustments itself.
    Volume is intentionally omitted: the EODHD source already adjusts it.
    """

    price = models.OneToOneField(
        Price, on_delete=models.CASCADE, related_name="split_adjusted_price",
    )
    open = models.FloatField()
    high = models.FloatField()
    low = models.FloatField()
    close = models.FloatField()
    adjustment_factor = models.FloatField(
        help_text="Multiplier for raw OHLC; 0.25 for a subsequent 4-for-1 split",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(adjustment_factor__gt=0),
                name="split_adjusted_factor_positive",
            ),
        ]

    def __str__(self):
        return f"Split-adjusted price {self.price_id}"


class TechnicalIndicators(models.Model):
    """
    Pre-computed technical indicators stored per ticker per date.
    Computed by: python manage.py compute_technicals
    """
    company = models.ForeignKey('Company', on_delete=models.CASCADE, null=True, blank=True)
    ticker = models.CharField(max_length=20, db_index=True)
    date = models.DateField(db_index=True)

    # Volatility
    atr_14 = models.FloatField(null=True, blank=True, help_text="Average True Range (14-day)")
    hist_volatility_20 = models.FloatField(null=True, blank=True, help_text="Annualized 20-day historical volatility")

    # Momentum / Relative Strength
    rs_industry = models.FloatField(null=True, blank=True, help_text="Industry Relative Strength: percentile rank (0-100) of 63-day return within the same industry group")

    # Volume-price
    vwap_20 = models.FloatField(null=True, blank=True, help_text="20-day rolling Volume Weighted Average Price")

    # Trend
    dma_20 = models.FloatField(null=True, blank=True, help_text="20-day simple moving average of close")
    dma_50 = models.FloatField(null=True, blank=True, help_text="50-day simple moving average of close")
    dma_200 = models.FloatField(null=True, blank=True, help_text="200-day simple moving average of close")

    # Market-relative (vs benchmark ticker, default SPY.US)
    beta_252 = models.FloatField(null=True, blank=True, help_text="252-day rolling beta vs benchmark")
    alpha_252 = models.FloatField(null=True, blank=True, help_text="252-day rolling annualized alpha vs benchmark")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["ticker", "date"], name="unique_technical_ticker_date"),
        ]
        indexes = [
            models.Index(fields=["ticker", "-date"]),
        ]

    def __str__(self):
        return f"{self.ticker} {self.date}"






class StockApiRateWindow(models.Model):
    """Shared rolling request history, serialized by a database row lock."""
    client_key = models.CharField(max_length=64, primary_key=True)
    requests = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
