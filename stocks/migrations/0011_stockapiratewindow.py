from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("stocks", "0010_alter_company_country_alter_company_ticker_and_more")]

    operations = [
        migrations.CreateModel(
            name="StockApiRateWindow",
            fields=[
                ("client_key", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("requests", models.JSONField(default=list)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
            ],
        ),
    ]
