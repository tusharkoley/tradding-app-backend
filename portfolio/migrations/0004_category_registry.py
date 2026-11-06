from django.db import migrations


def configure_categories(apps, schema_editor):
    Category = apps.get_model('portfolio', 'AssetCategory')
    alias = schema_editor.connection.alias
    for category in Category.objects.using(alias).all():
        kinds = (['deposit', 'withdrawal', 'opening', 'adjustment']
                 if category.transaction_policy == 'cash' else ['buy', 'sell', 'opening', 'adjustment'])
        # Keep existing used categories compatible with all their historical entries.
        if category.code == 'pensions':
            kinds = ['buy', 'sell', 'deposit', 'withdrawal', 'opening', 'adjustment']
        strategy = 'manual' if category.code in ('cash', 'property', 'pensions', 'other', 'liabilities') else 'manual_or_market'
        if category.assets.using(alias).filter(valuation_method='market').exists():
            strategy = 'manual_or_market'
        category.supported_transaction_types = kinds
        category.valuation_strategy = strategy
        category.save(using=alias)
    Category.objects.using(alias).get_or_create(code='mutual-funds', defaults={
        'name': 'Mutual funds', 'transaction_policy': 'position',
        'supported_transaction_types': ['buy', 'sell', 'opening', 'adjustment'],
        'valuation_strategy': 'manual_or_market',
        'metadata_schema': {'type': 'object', 'properties': {
            'isin': {'type': 'string', 'maxLength': 12}, 'provider': {'type': 'string'},
        }, 'additionalProperties': False},
    })


class Migration(migrations.Migration):
    dependencies = [('portfolio', '0003_assetcategory_supported_transaction_types_and_more')]
    operations = [migrations.RunPython(configure_categories, migrations.RunPython.noop)]
