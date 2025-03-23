from django.urls import path, include
from .views import  PriceList, CompanyList, CompanyDetails, api_root, PriceDetails
from rest_framework.urlpatterns import format_suffix_patterns



urlpatterns = [
    path('', api_root),
    path('companies/',CompanyList.as_view()),
    path('companies/<int:pk>/', CompanyDetails.as_view()),
    path('company/<ticker>/prices/',PriceList.as_view()),
    path('company/prices/<int:pk>/', PriceDetails.as_view()),
    
]

urlpatterns = format_suffix_patterns(urlpatterns)

