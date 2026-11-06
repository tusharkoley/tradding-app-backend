from collections.abc import Mapping
from decimal import Decimal, localcontext
from django.db.models import Q

from rest_framework import serializers

from . import services
from .models import Asset, AssetCategory, AssetTransaction, AssetValuation, Currency, Portfolio
from .recorded_values import recorded_value


class StrictInputMixin:
    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            writable = {name for name, field in self.fields.items() if not field.read_only}
            errors = {name: ['Unknown or read-only field.'] for name in data if name not in writable}
            if errors:
                raise serializers.ValidationError(errors)
        return super().to_internal_value(data)


class PortfolioSerializer(StrictInputMixin, serializers.ModelSerializer):
    class Meta:
        model = Portfolio
        fields = ('id', 'owner', 'name', 'base_currency', 'is_archived', 'created_at', 'updated_at')
        read_only_fields = ('id', 'owner', 'created_at', 'updated_at')

    def create(self, validated_data):
        return services.create_portfolio(user=self.context['request'].user, data=validated_data)

    def update(self, instance, validated_data):
        return services.update_portfolio(user=self.context['request'].user, portfolio_id=instance.pk, data=validated_data)


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetCategory
        fields = ('id', 'code', 'name', 'is_liability', 'transaction_policy', 'metadata_schema',
                  'supported_transaction_types', 'valuation_strategy')
        read_only_fields = fields


class OpeningTransactionSerializer(StrictInputMixin, serializers.Serializer):
    kind = serializers.ChoiceField(choices=AssetTransaction.Kind.choices, default='opening')
    transaction_date = serializers.DateField()
    quantity = serializers.DecimalField(max_digits=28, decimal_places=12)
    unit_price = serializers.DecimalField(max_digits=28, decimal_places=12, min_value=Decimal('0'), required=False, allow_null=True)
    amount = serializers.DecimalField(max_digits=28, decimal_places=8)
    notes = serializers.CharField(required=False, allow_blank=True)


class ValuationWriteSerializer(StrictInputMixin, serializers.Serializer):
    valued_at = serializers.DateTimeField()
    basis = serializers.ChoiceField(choices=AssetValuation.Basis.choices)
    value = serializers.DecimalField(max_digits=28, decimal_places=8, min_value=Decimal('0'))
    source = serializers.CharField(max_length=120, default='User recorded')
    notes = serializers.CharField(required=False, allow_blank=True)


class ValuationSerializer(serializers.ModelSerializer):
    value = serializers.DecimalField(max_digits=28, decimal_places=8, read_only=True)
    class Meta:
        model = AssetValuation
        fields = ('id', 'asset', 'valued_at', 'revision', 'method', 'basis', 'value', 'currency', 'source', 'notes', 'created_at')
        read_only_fields = fields


class AssetSerializer(StrictInputMixin, serializers.ModelSerializer):
    portfolio = serializers.PrimaryKeyRelatedField(queryset=Portfolio.objects.none())
    category = serializers.PrimaryKeyRelatedField(queryset=AssetCategory.objects.filter(is_active=True))
    category_definition = CategorySerializer(source='category', read_only=True)
    portfolio_name = serializers.CharField(source='portfolio.name', read_only=True)
    portfolio_archived = serializers.BooleanField(source='portfolio.is_archived', read_only=True)
    opening_transaction = OpeningTransactionSerializer(required=False, write_only=True)
    initial_valuation = ValuationWriteSerializer(required=False, write_only=True)

    class Meta:
        model = Asset
        fields = ('id', 'portfolio', 'category', 'name', 'currency', 'unit', 'company', 'market_symbol',
                  'valuation_method', 'metadata', 'is_archived', 'created_at', 'updated_at',
                  'portfolio_name', 'portfolio_archived', 'category_definition', 'opening_transaction', 'initial_valuation')
        read_only_fields = ('id', 'is_archived', 'created_at', 'updated_at')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request:
            self.fields['portfolio'].queryset = Portfolio.objects.for_user(request.user)
        if isinstance(self.instance, Asset):
            # An inactive category remains a valid unchanged identity for its existing asset.
            self.fields['category'].queryset = AssetCategory.objects.filter(
                Q(is_active=True) | Q(pk=self.instance.category_id))

    def create(self, validated_data):
        return services.create_asset(user=self.context['request'].user, data=validated_data)

    def update(self, instance, validated_data):
        if 'opening_transaction' in validated_data or 'initial_valuation' in validated_data:
            raise serializers.ValidationError('Opening records may only be supplied when creating an asset.')
        return services.update_asset(user=self.context['request'].user, asset_id=instance.pk, data=validated_data)


class TransactionSerializer(StrictInputMixin, serializers.ModelSerializer):
    is_reversed = serializers.BooleanField(read_only=True, default=False)
    asset = serializers.PrimaryKeyRelatedField(queryset=Asset.objects.none())
    currency = serializers.ChoiceField(choices=Currency.choices, required=False)
    unit_price = serializers.DecimalField(max_digits=28, decimal_places=12, min_value=Decimal('0'),
                                         allow_null=True, required=False)
    fee = serializers.DecimalField(max_digits=28, decimal_places=8, min_value=Decimal('0'), required=False)

    class Meta:
        model = AssetTransaction
        fields = ('id', 'asset', 'kind', 'transaction_date', 'quantity', 'unit_price', 'amount', 'fee',
                  'currency', 'notes', 'reversal_of', 'correction_of', 'created_at', 'is_reversed')
        read_only_fields = ('id', 'reversal_of', 'correction_of', 'created_at')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request:
            self.fields['asset'].queryset = Asset.objects.for_user(request.user)

    def create(self, validated_data):
        asset = validated_data.pop('asset')
        return services.record_transaction(user=self.context['request'].user, asset_id=asset.pk, data=validated_data)


class TransactionChangesSerializer(StrictInputMixin, serializers.Serializer):
    kind = serializers.ChoiceField(choices=AssetTransaction.Kind.choices, required=False)
    transaction_date = serializers.DateField(required=False)
    quantity = serializers.DecimalField(max_digits=28, decimal_places=12, required=False)
    unit_price = serializers.DecimalField(max_digits=28, decimal_places=12, min_value=Decimal('0'),
                                         allow_null=True, required=False)
    amount = serializers.DecimalField(max_digits=28, decimal_places=8, required=False)
    fee = serializers.DecimalField(max_digits=28, decimal_places=8, min_value=Decimal('0'), required=False)
    notes = serializers.CharField(allow_blank=True, required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError('Provide at least one changed field.')
        return attrs


class ReversalSerializer(StrictInputMixin, serializers.Serializer):
    reason = serializers.CharField(max_length=2000)
    transaction_date = serializers.DateField(required=False)


class CorrectionSerializer(StrictInputMixin, serializers.Serializer):
    reason = serializers.CharField(max_length=2000)
    changes = TransactionChangesSerializer()


class EmptySerializer(StrictInputMixin, serializers.Serializer):
    pass


class HoldingSerializer(serializers.Serializer):
    asset = serializers.IntegerField(source='pk')
    portfolio = serializers.IntegerField(source='portfolio_id')
    category = serializers.IntegerField(source='category_id')
    category_code = serializers.CharField(source='category.code')
    category_name = serializers.CharField(source='category.name')
    portfolio_name = serializers.CharField(source='portfolio.name')
    name = serializers.CharField()
    currency = serializers.CharField()
    unit = serializers.CharField()
    is_archived = serializers.BooleanField()
    as_of = serializers.SerializerMethodField()
    quantity = serializers.SerializerMethodField()
    transaction_count = serializers.SerializerMethodField()
    last_transaction_date = serializers.SerializerMethodField()
    recorded_value = serializers.SerializerMethodField()
    valuation_status = serializers.SerializerMethodField()
    valuation_date = serializers.SerializerMethodField()
    last_updated = serializers.SerializerMethodField()

    def get_recorded_value(self, obj):
        return recorded_value(obj)['recorded_value']

    def get_valuation_status(self, obj):
        return recorded_value(obj)['valuation_status']

    def get_valuation_date(self, obj):
        return recorded_value(obj)['valuation_date']

    def get_last_updated(self, obj):
        return max([obj.updated_at] + [entry.created_at for entry in obj.holding_entries]
                   + [entry.created_at for entry in obj.recorded_valuations])

    def get_as_of(self, obj):
        return self.context['as_of'].isoformat()

    def get_quantity(self, obj):
        with localcontext() as context:
            context.prec = 64
            quantity = sum((entry.quantity for entry in obj.holding_entries), Decimal('0'))
            return format(quantity, '.12f')

    def get_transaction_count(self, obj):
        return len(obj.holding_entries)

    def get_last_transaction_date(self, obj):
        return max((entry.transaction_date for entry in obj.holding_entries), default=None)


class AssetSummarySerializer(HoldingSerializer):
    net_recorded_amount = serializers.SerializerMethodField()
    net_fees = serializers.SerializerMethodField()

    def _sum(self, obj, field):
        with localcontext() as context:
            context.prec = 64
            return format(sum((getattr(entry, field) for entry in obj.holding_entries), Decimal('0')), '.8f')

    def get_net_recorded_amount(self, obj):
        return self._sum(obj, 'amount')

    def get_net_fees(self, obj):
        return self._sum(obj, 'fee')
