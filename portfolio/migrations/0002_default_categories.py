from django.db import migrations


CATEGORIES = [
    ('stocks', 'Stocks', False, {'isin': {'type': 'string', 'maxLength': 12}}),
    ('etfs', 'ETFs', False, {'isin': {'type': 'string', 'maxLength': 12}}),
    ('gold', 'Gold', False, {'purity': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 1}}),
    ('silver', 'Silver', False, {'purity': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 1}}),
    ('property', 'Property', False, {'address': {'type': 'string'}}),
    ('pensions', 'Pension funds', False, {'provider': {'type': 'string'}}),
    ('cash', 'Cash', False, {'institution': {'type': 'string'}}),
    ('other', 'Other assets', False, {}),
    ('liabilities', 'Liabilities', True, {'lender': {'type': 'string'}}),
]


def seed_categories(apps, schema_editor):
    Category = apps.get_model('portfolio', 'AssetCategory')
    for code, name, liability, properties in CATEGORIES:
        Category.objects.using(schema_editor.connection.alias).get_or_create(
            code=code, defaults={
                'name': name, 'is_liability': liability,
                'transaction_policy': 'cash' if code == 'cash' else 'position',
                'metadata_schema': {'type': 'object', 'properties': properties, 'additionalProperties': False},
            },
        )


class Migration(migrations.Migration):
    dependencies = [('portfolio', '0001_initial')]
    # Seeded reference definitions are historical data, so rollback leaves them intact.
    operations = [migrations.RunPython(seed_categories, migrations.RunPython.noop)]
