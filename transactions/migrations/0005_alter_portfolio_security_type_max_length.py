from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("transactions", "0004_rename_quantitity_portfolio_quantity"),
    ]

    operations = [
        migrations.AlterField(
            model_name="portfolio",
            name="security_type",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("Stocks", "stocks"),
                    ("MF", "mf"),
                    ("Bond", "bond"),
                    ("ETF", "etf"),
                ],
                default="Stocks",
            ),
        ),
    ]
