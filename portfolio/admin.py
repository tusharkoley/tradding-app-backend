from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from .models import (Asset, AssetCategory, AssetTransaction, AssetValuation, AuditLog,
                     CurrencyExchangeRate, Portfolio, PortfolioSnapshot)
from .services import reverse_transaction


class HistoricalAdmin(admin.ModelAdmin):
    """Admin is also tenant-scoped, including superusers inspecting personal data."""
    def get_queryset(self, request):
        return super().get_queryset(request).for_user(request.user)

    def _owns(self, request, obj):
        return obj is None or obj.audit_portfolio().owner_id == request.user.pk

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) and self._owns(request, obj)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and self._owns(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.save(actor=request.user, reason='Django admin change')

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        related = db_field.remote_field.model
        if related in (Portfolio, Asset, AssetTransaction):
            kwargs['queryset'] = related.objects.for_user(request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Portfolio)
class PortfolioAdmin(HistoricalAdmin):
    list_display = ('name', 'base_currency', 'is_archived', 'created_at')
    list_filter = ('base_currency', 'is_archived')

    def get_readonly_fields(self, request, obj=None):
        return ('owner', 'base_currency', 'created_at', 'updated_at') if obj else ('owner', 'created_at', 'updated_at')

    def save_model(self, request, obj, form, change):
        if not change:
            obj.owner = request.user
        super().save_model(request, obj, form, change)


@admin.register(Asset)
class AssetAdmin(HistoricalAdmin):
    list_display = ('name', 'portfolio', 'category', 'currency', 'valuation_method', 'is_archived')
    list_filter = ('currency', 'valuation_method', 'is_archived')

    def get_readonly_fields(self, request, obj=None):
        fields = ('created_at', 'updated_at')
        return fields + ('portfolio', 'category', 'currency', 'unit', 'company', 'market_symbol') if obj else fields


class AppendOnlyAdmin(HistoricalAdmin):
    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AssetTransaction)
class AssetTransactionAdmin(AppendOnlyAdmin):
    list_display = ('id', 'asset', 'kind', 'transaction_date', 'quantity', 'amount', 'currency', 'reversal_of', 'correction_of')
    list_filter = ('kind', 'currency')
    readonly_fields = ('created_at', 'reversal_of', 'correction_of')
    actions = ('reverse_entries',)

    def has_reverse_permission(self, request):
        return self.has_add_permission(request)

    @admin.action(description='Reverse selected entries (original effective dates)', permissions=['reverse'])
    def reverse_entries(self, request, queryset):
        for entry in queryset:
            try:
                reverse_transaction(user=request.user, entry_id=entry.pk,
                                    reason='Reversed through Django admin')
            except ValidationError as exc:
                self.message_user(request, f'Entry {entry.pk}: {exc}', level=messages.ERROR)


@admin.register(AssetValuation)
class AssetValuationAdmin(AppendOnlyAdmin):
    list_display = ('asset', 'valued_at', 'revision', 'value', 'currency', 'method', 'basis')
    readonly_fields = ('created_at',)


@admin.register(PortfolioSnapshot)
class PortfolioSnapshotAdmin(AppendOnlyAdmin):
    list_display = ('portfolio', 'snapshot_date', 'revision', 'net_value', 'currency')

    def has_add_permission(self, request):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'portfolio', 'actor', 'model_label', 'object_id', 'action')
    readonly_fields = tuple(field.name for field in AuditLog._meta.fields)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Shared category/FX events are visible only to superusers.
        owned = qs.for_user(request.user)
        return owned | qs.filter(portfolio__isnull=True) if request.user.is_superuser else owned

    def has_view_permission(self, request, obj=None):
        allowed = super().has_view_permission(request, obj)
        if obj is None:
            return allowed
        return allowed and ((obj.portfolio_id and obj.portfolio.owner_id == request.user.pk)
                            or (obj.portfolio_id is None and request.user.is_superuser))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReferenceAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.save(actor=request.user, reason='Django admin reference data change')


@admin.register(AssetCategory)
class AssetCategoryAdmin(ReferenceAdmin):
    list_display = ('code', 'name', 'is_liability', 'is_active')
    readonly_fields = ('created_at',)

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields + ('code', 'is_liability', 'transaction_policy', 'metadata_schema',
                                     'supported_transaction_types', 'valuation_strategy') if obj and obj.assets.exists() else self.readonly_fields


@admin.register(CurrencyExchangeRate)
class CurrencyExchangeRateAdmin(ReferenceAdmin):
    list_display = ('base_currency', 'quote_currency', 'rate_date', 'rate', 'source', 'revision')
    readonly_fields = ('created_at',)

    def has_change_permission(self, request, obj=None):
        return False
