from django.urls import path, include
from .views import  PriceList, CompanyList, CompanyDetails, api_root, PriceDetails, PriceLatestList, TechnicalIndicatorsLatestList, TechnicalIndicatorsLatestByTicker, IndustryPerformanceRanking
from rest_framework.urlpatterns import format_suffix_patterns



urlpatterns = [
    path('', api_root, name='stocks-root'),
    path('companies/',CompanyList.as_view(), name='companies'),
    path('companies/<int:pk>/', CompanyDetails.as_view(), name='company-details'),
    path('company/<ticker>/prices/',PriceList.as_view(), name='company-prices'),
    path('company/prices/<int:pk>/', PriceDetails.as_view(), name='price-details'),
    path('prices/latest/', PriceLatestList.as_view(), name='prices-latest'),
    path('technicals/latest/', TechnicalIndicatorsLatestList.as_view(), name='technicals-latest'),
    path('company/<ticker>/technicals/latest/', TechnicalIndicatorsLatestByTicker.as_view(), name='ticker-technicals-latest'),
    path('industries/performance/', IndustryPerformanceRanking.as_view(), name='industries-performance'),
    
]

urlpatterns = format_suffix_patterns(urlpatterns)

