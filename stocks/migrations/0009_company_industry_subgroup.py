from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("stocks", "0008_technicalindicators_dma_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="industry_subgroup",
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
    ]