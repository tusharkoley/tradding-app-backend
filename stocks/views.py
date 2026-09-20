from django.shortcuts import render

from django.db.models import OuterRef, Subquery
from django.db.models import Max
from django.db import connection
from django.core.cache import cache
from datetime import timedelta
from .models import Price, Company, TechnicalIndicators
from .serializers import CompanySerializer, PriceSerilizer, CompanyListSerializer,PriceListSerializer, TechnicalIndicatorsSerializer
from rest_framework import generics

from rest_framework.decorators import api_view
from rest_framework.decorators import permission_classes
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework import viewsets
from rest_framework.views import APIView
from rest_framework import status
from rest_framework import mixins
from rest_framework.permissions import AllowAny
from .permissions import IsStaffOrReadOnly


@api_view(['GET'])
@permission_classes([AllowAny])
def api_root(request, format=None):
    return Response({
        'companies': reverse('companies', request=request, format=format),
        'prices_latest': reverse('prices-latest', request=request, format=format),
        'technicals_latest': reverse('technicals-latest', request=request, format=format),
        'industries_performance': reverse('industries-performance', request=request, format=format),

    })


class CompanyList(APIView):
    queryset = Company.objects.all()
    serializer_class = CompanySerializer
    permission_classes = [IsStaffOrReadOnly]
    

    def get(self, request, format=None):
        cache_key = "stocks:companies:v1"
        cached_payload = cache.get(cache_key)
        if cached_payload is not None:
            return Response(cached_payload)

        companies = Company.objects.all()
        serializer = CompanySerializer(companies, many=True)
        payload = serializer.data
        cache.set(cache_key, payload, 5 * 60)
        return Response(payload)
    
    def post(self, request, format=None):
        serializer = CompanyListSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)




class CompanyDetails(generics.RetrieveUpdateDestroyAPIView):
    queryset = Company.objects.all()
    serializer_class = CompanySerializer
    permission_classes = [IsStaffOrReadOnly]


class PriceList(APIView):
    queryset = Company.objects.all()
    serializer_class = PriceSerilizer
    permission_classes = [IsStaffOrReadOnly]

    def get(self, request, ticker, format=None):
        prices = Price.objects.filter(ticker=ticker)
        serializer = PriceSerilizer(prices, many=True)
        return Response(serializer.data)
    
    def post(self, request, format=None):
        serializer = PriceListSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



class PriceDetails(generics.RetrieveUpdateDestroyAPIView):
    queryset = Price.objects.all()
    serializer_class = PriceSerilizer
    permission_classes = [IsStaffOrReadOnly]


class PriceLatestList(APIView):
    queryset = Price.objects.all()

    def get(self, request, format=None):
        cache_key = "stocks:prices:latest:v1"
        cached_payload = cache.get(cache_key)
        if cached_payload is not None:
            return Response(cached_payload)

        if connection.vendor == "postgresql":
            latest_prices = (
                Price.objects
                .order_by('ticker', '-date')
                .distinct('ticker')
                .values('ticker', 'date', 'close')
                .order_by('ticker')
            )
        else:
            latest_pk_subquery = Price.objects.filter(
                ticker=OuterRef('ticker')
            ).order_by('-date').values('pk')[:1]

            latest_prices = Price.objects.filter(
                pk=Subquery(latest_pk_subquery)
            ).order_by('ticker').values('ticker', 'date', 'close')

        payload = list(latest_prices)
        cache.set(cache_key, payload, 5 * 60)
        return Response(payload)


class TechnicalIndicatorsLatestList(APIView):
    queryset = TechnicalIndicators.objects.all()
    serializer_class = TechnicalIndicatorsSerializer

    def get(self, request, format=None):
        rs_min_raw = request.query_params.get('rs_min')
        cache_key = f"stocks:technicals:latest:v1:rs_min={rs_min_raw if rs_min_raw is not None else 'all'}"
        cached_payload = cache.get(cache_key)
        if cached_payload is not None:
            return Response(cached_payload)

        if connection.vendor == "postgresql":
            technicals = (
                TechnicalIndicators.objects
                .order_by('ticker', '-date')
                .distinct('ticker')
                .order_by('ticker')
            )
        else:
            latest_pk_subquery = TechnicalIndicators.objects.filter(
                ticker=OuterRef('ticker')
            ).order_by('-date').values('pk')[:1]

            technicals = TechnicalIndicators.objects.filter(
                pk=Subquery(latest_pk_subquery)
            ).order_by('ticker')

        if rs_min_raw is not None:
            try:
                rs_min = float(rs_min_raw)
                technicals = technicals.filter(rs_industry__gte=rs_min)
            except ValueError:
                return Response(
                    {"detail": "Invalid rs_min. Please provide a numeric value."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        serializer = TechnicalIndicatorsSerializer(technicals, many=True)
        payload = serializer.data
        cache.set(cache_key, payload, 5 * 60)
        return Response(payload)


class TechnicalIndicatorsLatestByTicker(APIView):
    queryset = TechnicalIndicators.objects.all()
    serializer_class = TechnicalIndicatorsSerializer

    def get(self, request, ticker, format=None):
        technical = TechnicalIndicators.objects.filter(
            ticker=ticker
        ).order_by('-date').first()

        if technical is None:
            return Response({}, status=status.HTTP_200_OK)

        serializer = TechnicalIndicatorsSerializer(technical)
        return Response(serializer.data)


class IndustryPerformanceRanking(APIView):
    """
    Rank industries by average stock return over a selected lookback period.
    Query params:
    - months: one of 1, 3, 6, 9, 12 (default: 3)
    - country: optional country name to filter companies
    """

    ALLOWED_MONTHS = {1, 3, 6, 9, 12}
    queryset = Price.objects.all()
    CACHE_TTL_SECONDS = 10 * 60
    COUNTRIES_CACHE_KEY = "industry_perf:countries:v1"
    INDUSTRY_NORMALIZATION_MAP = {
        'information technogoy': 'Information Technology',
        'information technology': 'Information Technology',
        'info technology': 'Information Technology',
    }

    def _normalize_industry(self, industry_name):
        if not industry_name:
            return industry_name

        clean = industry_name.strip()
        mapped = self.INDUSTRY_NORMALIZATION_MAP.get(clean.lower())
        return mapped if mapped else clean

    def get(self, request, format=None):
        months_raw = request.query_params.get('months', '3')
        country = (request.query_params.get('country') or '').strip()

        try:
            months = int(months_raw)
        except ValueError:
            return Response(
                {"detail": "Invalid months value. Allowed values: 1, 3, 6, 9, 12."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if months not in self.ALLOWED_MONTHS:
            return Response(
                {"detail": "Invalid months value. Allowed values: 1, 3, 6, 9, 12."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        latest_date = Price.objects.aggregate(max_date=Max('date')).get('max_date')
        cache_key = (
            f"industry_perf_v2:{months}:{(country or 'all').lower()}:"
            f"{latest_date.isoformat() if latest_date else 'none'}"
        )
        cached_payload = cache.get(cache_key)
        if cached_payload is not None:
            return Response(cached_payload)

        available_countries = cache.get(self.COUNTRIES_CACHE_KEY)
        if available_countries is None:
            available_countries = list(
                Company.objects.exclude(country__isnull=True)
                .exclude(country='')
                .values_list('country', flat=True)
                .distinct()
                .order_by('country')
            )
            cache.set(
                self.COUNTRIES_CACHE_KEY,
                available_countries,
                self.CACHE_TTL_SECONDS,
            )

        if latest_date is None:
            payload = {
                'months': months,
                'country': country or 'All',
                'as_of_date': None,
                'lookback_date': None,
                'top_industry': None,
                'rankings': [],
                'available_countries': available_countries,
            }
            cache.set(cache_key, payload, self.CACHE_TTL_SECONDS)
            return Response(payload)

        lookback_date = latest_date - timedelta(days=30 * months)

        company_queryset = Company.objects.exclude(industry__isnull=True).exclude(industry='')
        if country:
            company_queryset = company_queryset.filter(country=country)

        # Anchor the query on the small, already-filtered company table. The
        # (ticker, -date) price index makes both scalar subqueries index seeks,
        # avoiding two DISTINCT ON scans and the large ticker IN clauses that
        # used to dominate the cold response time on PostgreSQL.
        latest_close_subquery = Price.objects.filter(
            ticker=OuterRef('ticker'),
        ).order_by('-date').values('close')[:1]
        past_close_subquery = Price.objects.filter(
            ticker=OuterRef('ticker'),
            date__lte=lookback_date,
        ).order_by('-date').values('close')[:1]

        latest_with_past_prices = company_queryset.annotate(
            latest_close=Subquery(latest_close_subquery),
            past_close=Subquery(past_close_subquery),
        ).values('ticker', 'industry', 'latest_close', 'past_close')

        performance_by_industry = {}
        for item in latest_with_past_prices:
            industry = self._normalize_industry(item.get('industry'))
            latest_close = item.get('latest_close')
            past_close = item.get('past_close')

            if not industry or latest_close is None or past_close in (None, 0):
                continue

            return_pct = ((latest_close - past_close) / past_close) * 100

            if industry not in performance_by_industry:
                performance_by_industry[industry] = {
                    'industry': industry,
                    'avg_return_pct': 0.0,
                    'company_count': 0,
                    '_sum_return_pct': 0.0,
                }

            performance_by_industry[industry]['company_count'] += 1
            performance_by_industry[industry]['_sum_return_pct'] += return_pct

        rankings = []
        for row in performance_by_industry.values():
            count = row['company_count']
            if count == 0:
                continue
            avg_return = row['_sum_return_pct'] / count
            rankings.append(
                {
                    'industry': row['industry'],
                    'avg_return_pct': round(avg_return, 2),
                    'company_count': count,
                }
            )

        rankings.sort(key=lambda x: x['avg_return_pct'], reverse=True)

        for index, row in enumerate(rankings, start=1):
            row['rank'] = index

        top_industry = rankings[0] if rankings else None

        payload = {
            'months': months,
            'country': country or 'All',
            'as_of_date': latest_date,
            'lookback_date': lookback_date,
            'top_industry': top_industry,
            'rankings': rankings,
            'available_countries': available_countries,
        }

        cache.set(cache_key, payload, self.CACHE_TTL_SECONDS)
        return Response(payload)


@api_view(['GET'])
def get_price_by_ticker(request):
    ticker = request.ticker
    prices = Price.objects.all().filter(ticker==ticker)
    serializer = PriceSerilizer(prices, many=True)

    return  Response(serializer.data)






