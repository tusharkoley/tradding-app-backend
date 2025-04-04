# Generated by Django 5.1.4 on 2025-02-08 09:11

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("transactions", "0002_portfolio_quantitity_portfolio_security_type_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="transactions",
            name="ticker",
            field=models.CharField(max_length=50, null=True),
        ),
        migrations.AlterField(
            model_name="transactions",
            name="transaction_type",
            field=models.TextField(
                choices=[
                    ("Buy", "buy"),
                    ("Sell", "sell"),
                    ("Deposite", "deposite"),
                    ("Withdraw", "withdraw"),
                    ("Divident", "divident"),
                    ("Interest", "interest"),
                    ("Brokarage", "brokarage"),
                    ("Fees", "fees"),
                ],
                default="Deposite",
            ),
        ),
    ]
