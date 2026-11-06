import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.urls import resolve
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker
from rest_framework.test import APITestCase

from .models import Asset, AssetCategory, Portfolio


class RegistryContractTests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.spec = json.loads(Path(__file__).with_name('openapi.json').read_text())

    def validator(self, schema):
        document = {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                    'components': self.spec['components'], **schema}
        return Draft202012Validator(document, format_checker=FormatChecker())

    def test_all_schemas_and_saved_examples_are_valid(self):
        for name, schema in self.spec['components']['schemas'].items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(schema)
        for path, methods in self.spec['paths'].items():
            for method, operation in methods.items():
                contents = [response.get('content', {}) for response in operation['responses'].values()]
                if 'requestBody' in operation:
                    contents.append(operation['requestBody']['content'])
                for content in contents:
                    body = content.get('application/json', {})
                    if 'example' in body:
                        with self.subTest(path=path, method=method):
                            self.validator(body['schema']).validate(body['example'])

    def test_every_documented_operation_resolves_to_an_implemented_method(self):
        for path, methods in self.spec['paths'].items():
            match = resolve(path.replace('{id}', '1'))
            for method in methods:
                with self.subTest(path=path, method=method):
                    self.assertIn(method, match.func.actions)

    def test_real_responses_for_every_operation_match_the_contract(self):
        user = get_user_model().objects.create_user(email='contract@example.com', is_active=True, is_staff=False)
        self.client.force_authenticate(user=user)
        portfolio = Portfolio.objects.create(owner=user, name='Personal')
        category = AssetCategory.objects.get(code='stocks')
        asset = Asset.objects.create(portfolio=portfolio, category=category, name='Shares')
        visited = set()

        def call(method, path, data=None, pk=None):
            actual_path = path.replace('{id}', str(pk))
            response = getattr(self.client, method)(actual_path, data=data or {}, format='json')
            operation = self.spec['paths'][path][method]
            code = str(response.status_code)
            self.assertIn(code, ('200', '201'), (method, path, response.data))
            self.validator(operation['responses'][code]['content']['application/json']['schema']).validate(response.json())
            visited.add((path, method))
            return response.json()

        for resource, pk in [('portfolios', portfolio.pk), ('categories', category.pk), ('assets', asset.pk)]:
            call('get', f'/portfolio/{resource}/')
            call('get', f'/portfolio/{resource}/{{id}}/', pk=pk)
        call('post', '/portfolio/portfolios/', {'name': 'Second'})
        call('patch', '/portfolio/portfolios/{id}/', {'name': 'Renamed'}, pk=portfolio.pk)
        call('put', '/portfolio/portfolios/{id}/', {'name': 'Personal'}, pk=portfolio.pk)
        asset_body = {'portfolio': portfolio.pk, 'category': category.pk, 'name': 'New shares'}
        call('post', '/portfolio/assets/', asset_body)
        call('patch', '/portfolio/assets/{id}/', {'name': 'Updated'}, pk=asset.pk)
        call('put', '/portfolio/assets/{id}/', {**asset_body, 'name': 'Shares'}, pk=asset.pk)
        body = {'asset': asset.pk, 'kind': 'buy', 'transaction_date': str(timezone.localdate()),
                'quantity': '10', 'unit_price': '2', 'amount': '20'}
        entry = call('post', '/portfolio/transactions/', body)
        call('get', '/portfolio/transactions/')
        call('get', '/portfolio/transactions/{id}/', pk=entry['id'])
        call('get', '/portfolio/holdings/')
        call('get', '/portfolio/assets/{id}/summary/', pk=asset.pk)
        call('post', '/portfolio/transactions/{id}/reverse/', {'reason': 'Duplicate'}, pk=entry['id'])
        entry = call('post', '/portfolio/transactions/', body)
        call('post', '/portfolio/transactions/{id}/correct/',
             {'reason': 'Quantity typo', 'changes': {'quantity': '12', 'amount': '24'}}, pk=entry['id'])
        call('post', '/portfolio/assets/{id}/valuations/',
             {'valued_at': str(timezone.localdate()) + 'T00:00:00Z', 'basis': 'unit', 'value': '25'}, pk=asset.pk)
        call('get', '/portfolio/assets/{id}/valuations/', pk=asset.pk)
        call('post', '/portfolio/assets/{id}/archive/', pk=asset.pk)
        self.assertEqual(visited, {(path, method) for path, methods in self.spec['paths'].items() for method in methods})
