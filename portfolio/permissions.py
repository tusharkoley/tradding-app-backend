from rest_framework.permissions import BasePermission


class IsPortfolioOwner(BasePermission):
    """Defense in depth in addition to owner-scoped queryset/relation lookups."""
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_active)

    def has_object_permission(self, request, view, obj):
        return obj.audit_portfolio().owner_id == request.user.pk
