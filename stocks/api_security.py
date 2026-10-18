"""Stock API origin enforcement and a shared, atomic rolling-minute budget."""
import hashlib
import ipaddress
import logging
import math
import time

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.http import JsonResponse

from .models import StockApiRateWindow

logger = logging.getLogger(__name__)
LIMIT = 20
WINDOW_SECONDS = 60


def client_ip(request):
    # Trust a forwarded header only when the deployment explicitly opts in and
    # the public edge replaces it. Never trust arbitrary X-Forwarded-For values.
    address = request.META.get("REMOTE_ADDR", "")
    if settings.STOCK_API_TRUST_RAILWAY_PROXY:
        address = request.META.get("HTTP_X_REAL_IP", address)
    try:
        return str(ipaddress.ip_address(address))
    except ValueError:
        return "unknown"


def consume_request(address):
    key = hashlib.sha256(address.encode()).hexdigest()
    # PostgreSQL row locks serialize requests across workers and replicas.
    # Fail closed on engines without row locking rather than promise strictness.
    if not connection.features.has_select_for_update:
        raise DatabaseError("Stock API rate limiting requires database row locking")
    with transaction.atomic():
        StockApiRateWindow.objects.get_or_create(client_key=key)
        window = StockApiRateWindow.objects.select_for_update().get(client_key=key)
        now = time.time()
        history = [timestamp for timestamp in window.requests if timestamp > now - WINDOW_SECONDS]
        if len(history) >= LIMIT:
            return False, max(1, math.ceil(history[0] + WINDOW_SECONDS - now))
        history.append(now)
        window.requests = history
        window.save(update_fields=["requests", "updated_at"])
    return True, 0


def is_stock_request(request):
    return request.path_info == "/stocks" or request.path_info.startswith("/stocks/")


class StockApiOriginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if is_stock_request(request) and request.headers.get("Origin") != settings.STOCK_API_ALLOWED_ORIGIN:
            response = JsonResponse({"detail": "Stock API requests must originate from the approved frontend."}, status=403)
            response["Cache-Control"] = "no-store"
            return response
        return self.get_response(request)


class StockApiSecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not is_stock_request(request) or request.method == "OPTIONS":
            return self.get_response(request)
        try:
            allowed, retry_after = consume_request(client_ip(request))
        except DatabaseError:
            logger.exception("Stock API rate limit storage unavailable")
            response = JsonResponse({"detail": "Stock API temporarily unavailable."}, status=503)
            response["Retry-After"] = "60"
        else:
            if allowed:
                return self.get_response(request)
            response = JsonResponse({"detail": "Stock API limit exceeded: 20 requests per minute."}, status=429)
            response["Retry-After"] = str(retry_after)
        response["Cache-Control"] = "no-store"
        return response
