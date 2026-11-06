from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from django.db.models import Exists, OuterRef, Prefetch
from django.http import Http404
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import services
from .filters import (AsOfQuery, AssetQuery, CategoryQuery, PortfolioQuery,
                      RegistryFilterBackend, TransactionQuery)
from .models import Asset, AssetCategory, AssetTransaction, AssetValuation, Portfolio
from .permissions import IsPortfolioOwner
from .serializers import (AssetSerializer, AssetSummarySerializer, CategorySerializer,
                          CorrectionSerializer, EmptySerializer, HoldingSerializer,
                          PortfolioSerializer, ReversalSerializer, TransactionSerializer,
                          ValuationSerializer, ValuationWriteSerializer)


class RegistryPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100


class RegistryViewSet(viewsets.GenericViewSet):
    permission_classes = (IsAuthenticated, IsPortfolioOwner)
    pagination_class = RegistryPagination
    filter_backends = (RegistryFilterBackend, filters.SearchFilter, filters.OrderingFilter)
    lookup_value_regex = r'\d+'

    def handle_exception(self, exc):
        if isinstance(exc, DjangoValidationError):
            detail = exc.message_dict if hasattr(exc, 'message_dict') else {'non_field_errors': exc.messages}
            if '__all__' in detail:
                detail['non_field_errors'] = detail.pop('__all__')
            exc = ValidationError(detail)
        elif isinstance(exc, ObjectDoesNotExist):
            exc = Http404()
        return super().handle_exception(exc)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response


class PortfolioViewSet(mixins.CreateModelMixin, mixins.ListModelMixin,
                       mixins.RetrieveModelMixin, mixins.UpdateModelMixin, RegistryViewSet):
    serializer_class = PortfolioSerializer
    query_serializer_class = PortfolioQuery
    filter_mapping = {'is_archived': 'is_archived', 'base_currency': 'base_currency'}
    search_fields = ('name',)
    ordering_fields = ('id', 'name', 'created_at', 'updated_at')
    ordering = ('id',)

    def get_queryset(self):
        return Portfolio.objects.for_user(self.request.user)


class CategoryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, RegistryViewSet):
    permission_classes = (IsAuthenticated,)
    serializer_class = CategorySerializer
    query_serializer_class = CategoryQuery
    filter_mapping = {'is_liability': 'is_liability'}
    search_fields = ('name', 'code')
    ordering_fields = ('id', 'name', 'code')
    ordering = ('id',)

    def get_queryset(self):
        return AssetCategory.objects.filter(is_active=True)


class AssetQueryMixin:
    prefetch_ledger = False
    query_serializer_class = AssetQuery
    filter_mapping = {'portfolio': 'portfolio_id', 'category': 'category_id',
                      'category_code': 'category__code', 'currency': 'currency',
                      'is_archived': 'is_archived', 'valuation_method': 'valuation_method'}
    search_fields = ('name', 'market_symbol', 'category__name')
    ordering_fields = ('id', 'name', 'created_at', 'updated_at')
    ordering = ('id',)

    def get_as_of(self):
        if not hasattr(self, '_as_of'):
            params = self.request.query_params
            query = AsOfQuery(data={'as_of': params['as_of']} if 'as_of' in params else {})
            query.is_valid(raise_exception=True)
            self._as_of = query.validated_data['as_of']
        return self._as_of

    def get_queryset(self):
        queryset = Asset.objects.for_user(self.request.user).select_related('category', 'portfolio')
        if self.prefetch_ledger or self.action == 'summary':
            entries = AssetTransaction.objects.filter(transaction_date__lte=self.get_as_of()).order_by('transaction_date', 'id')
            queryset = queryset.prefetch_related(Prefetch('transactions', queryset=entries, to_attr='holding_entries'))
            valuations = AssetValuation.objects.filter(valued_at__date__lte=self.get_as_of()).order_by('-valued_at', '-revision', '-id')
            queryset = queryset.prefetch_related(Prefetch('valuations', queryset=valuations, to_attr='recorded_valuations'))
        return queryset


class AssetViewSet(AssetQueryMixin, mixins.CreateModelMixin, mixins.ListModelMixin,
                   mixins.RetrieveModelMixin, mixins.UpdateModelMixin, RegistryViewSet):
    serializer_class = AssetSerializer

    @action(detail=True, methods=['get', 'post'])
    def valuations(self, request, pk=None):
        asset = self.get_object()
        if request.method == 'POST':
            body = ValuationWriteSerializer(data=request.data)
            body.is_valid(raise_exception=True)
            valuation = services.record_valuation(user=request.user, asset_id=asset.pk, data=body.validated_data)
            return Response(ValuationSerializer(valuation).data, status=status.HTTP_201_CREATED)
        rows = asset.valuations.order_by('-valued_at', '-revision', '-id')
        page = self.paginate_queryset(rows)
        return self.get_paginated_response(ValuationSerializer(page, many=True).data)

    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        asset = self.get_object()
        body = EmptySerializer(data=request.data)
        body.is_valid(raise_exception=True)
        asset = services.archive_asset(user=request.user, asset_id=asset.pk)
        return Response(AssetSerializer(asset, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['get'])
    def summary(self, request, pk=None):
        asset = self.get_object()
        return Response(AssetSummarySerializer(asset, context={'as_of': self.get_as_of()}).data)


class HoldingViewSet(AssetQueryMixin, mixins.ListModelMixin, RegistryViewSet):
    prefetch_ledger = True
    serializer_class = HoldingSerializer

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'as_of': self.get_as_of()}


class TransactionViewSet(mixins.CreateModelMixin, mixins.ListModelMixin,
                         mixins.RetrieveModelMixin, RegistryViewSet):
    serializer_class = TransactionSerializer
    query_serializer_class = TransactionQuery
    filter_mapping = {'asset': 'asset_id', 'portfolio': 'asset__portfolio_id', 'category': 'asset__category_id',
                      'kind': 'kind', 'date_from': 'transaction_date__gte', 'date_to': 'transaction_date__lte'}
    search_fields = ('notes', 'asset__name', 'asset__market_symbol')
    ordering_fields = ('id', 'transaction_date', 'created_at', 'amount', 'quantity')
    ordering = ('-transaction_date', '-id')

    def get_queryset(self):
        return (AssetTransaction.objects.for_user(self.request.user).select_related('asset__portfolio')
                .annotate(is_reversed=Exists(AssetTransaction.objects.filter(reversal_of_id=OuterRef('pk')))))

    @action(detail=True, methods=['post'])
    def reverse(self, request, pk=None):
        original = self.get_object()
        body = ReversalSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        entry = services.reverse_transaction(user=request.user, entry_id=original.pk, **body.validated_data)
        return Response(TransactionSerializer(entry).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def correct(self, request, pk=None):
        original = self.get_object()
        body = CorrectionSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        reversal, replacement = services.correct_transaction(user=request.user, entry_id=original.pk, **body.validated_data)
        return Response({'reversal': TransactionSerializer(reversal).data,
                         'replacement': TransactionSerializer(replacement).data}, status=status.HTTP_201_CREATED)
