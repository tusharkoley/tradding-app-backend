from django.urls import path, include
from .views import  PriceList, CompanyList, CompanyDetails, api_root, PriceDetails, PriceLatestList, TechnicalIndicatorsLatestList, TechnicalIndicatorsLatestByTicker, IndustryPerformanceRanking
from rest_framework.urlpatterns import format_suffix_patterns



urlpatterns = [
    path('', api_root),
    path('companies/',CompanyList.as_view()),
    path('companies/<int:pk>/', CompanyDetails.as_view()),
    path('company/<ticker>/prices/',PriceList.as_view()),
    path('company/prices/<int:pk>/', PriceDetails.as_view()),
    path('prices/latest/', PriceLatestList.as_view()),
    path('technicals/latest/', TechnicalIndicatorsLatestList.as_view()),
    path('company/<ticker>/technicals/latest/', TechnicalIndicatorsLatestByTicker.as_view()),
    path('industries/performance/', IndustryPerformanceRanking.as_view()),
    
]

urlpatterns = format_suffix_patterns(urlpatterns)

