from django.db import migrations


def configure_entry_forms(apps, schema_editor):
    Category = apps.get_model('portfolio', 'AssetCategory')
    for category in Category.objects.using(schema_editor.connection.alias).all():
        schema = dict(category.metadata_schema)
        properties = dict(schema.get('properties', {}))
        entry = {'quantity_label': 'Quantity / units', 'default_unit': 'unit', 'valuation_basis': 'unit',
                 'valuation_label': 'Recorded value per unit', 'book_value_label': 'Purchase / opening value'}
        if category.code in ('stocks', 'etfs', 'mutual-funds'):
            entry.update(symbol_label='Symbol', default_unit='share', quantity_label='Shares / units')
            properties['exchange'] = {'type': 'string', 'title': 'Exchange', 'maxLength': 50}
        if category.code in ('gold', 'silver'):
            entry.update(quantity_label='Weight', default_unit='gram', unit_options=['gram', 'troy ounce', 'kilogram'])
            properties['metal_type'] = {'type': 'string', 'title': 'Metal type', 'enum': [category.name], 'default': category.name}
        if category.code == 'property':
            entry.update(name_label='Property name', default_unit='property', quantity_label='Owned properties / fraction',
                         default_quantity='1', valuation_basis='total', valuation_label='Estimated value of your owned interest')
            properties.update(property_type={'type': 'string', 'title': 'Property type'},
                              location={'type': 'string', 'title': 'Location'})
        if category.code == 'pensions':
            entry.update(name_label='Pension name', default_unit='unit', unit_options=['unit', '$currency'],
                         quantity_label='Fund units / reported balance')
            properties['fund_name'] = {'type': 'string', 'title': 'Fund name'}
        if category.transaction_policy == 'cash':
            entry.update(name_label='Account label', default_unit='$currency', quantity_label='Balance',
                         balance_mode=True, valuation_label='Recorded balance', valuation_basis='total')
        if category.code == 'liabilities':
            entry.update(quantity_label='Outstanding balance', default_unit='$currency', balance_mode=True,
                         valuation_label='Recorded outstanding balance', valuation_basis='total')
        schema['properties'] = properties
        schema['x-entry'] = entry
        category.metadata_schema = schema
        category.save(using=schema_editor.connection.alias)


class Migration(migrations.Migration):
    dependencies = [('portfolio', '0004_category_registry')]
    operations = [migrations.RunPython(configure_entry_forms, migrations.RunPython.noop)]
