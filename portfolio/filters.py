from django.utils import timezone
from rest_framework import filters, serializers

from .models import Asset, AssetTransaction, Currency


class PortfolioQuery(serializers.Serializer):
    is_archived = serializers.BooleanField(required=False)
    base_currency = serializers.ChoiceField(choices=Currency.choices, required=False)


class CategoryQuery(serializers.Serializer):
    is_liability = serializers.BooleanField(required=False)


class AssetQuery(serializers.Serializer):
    portfolio = serializers.IntegerField(min_value=1, required=False)
    category = serializers.IntegerField(min_value=1, required=False)
    category_code = serializers.SlugField(required=False)
    currency = serializers.ChoiceField(choices=Currency.choices, required=False)
    is_archived = serializers.BooleanField(required=False)
    valuation_method = serializers.ChoiceField(choices=Asset.ValuationMethod.choices, required=False)
    as_of = serializers.DateField(required=False)


class TransactionQuery(serializers.Serializer):
    asset = serializers.IntegerField(min_value=1, required=False)
    portfolio = serializers.IntegerField(min_value=1, required=False)
    category = serializers.IntegerField(min_value=1, required=False)
    kind = serializers.ChoiceField(choices=AssetTransaction.Kind.choices, required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)

    def validate(self, attrs):
        if 'date_from' in attrs and 'date_to' in attrs and attrs['date_from'] > attrs['date_to']:
            raise serializers.ValidationError('date_from cannot follow date_to.')
        return attrs


class AsOfQuery(serializers.Serializer):
    as_of = serializers.DateField(default=timezone.localdate)


class RegistryFilterBackend(filters.BaseFilterBackend):
    """Typed filters without changing global DRF settings or list response formats."""
    def filter_queryset(self, request, queryset, view):
        serializer_class = view.query_serializer_class
        field_names = set(serializer_class().fields)
        unknown = set(request.query_params) - field_names - {'search', 'ordering', 'page', 'page_size', 'format'}
        if unknown:
            raise serializers.ValidationError({key: ['Unknown query parameter.'] for key in unknown})
        serializer = serializer_class(data={key: request.query_params[key] for key in field_names if key in request.query_params})
        serializer.is_valid(raise_exception=True)
        mapping = view.filter_mapping
        for key, value in serializer.validated_data.items():
            if key in mapping:
                queryset = queryset.filter(**{mapping[key]: value})
        return queryset
