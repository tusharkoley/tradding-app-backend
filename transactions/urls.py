from django.urls import path

from .views import trade_security, money_transfer, calculate_portfolio_performance
from rest_framework.urlpatterns import format_suffix_patterns

app_name = 'transactions'

urlpatterns = [
    path('trade/<int:userId>/<str:ticker>/',trade_security, name='trade'),
    path('transfer/<int:userId>/',money_transfer, name='transfer'),
    path('portfolio_ret/<int:userId>',calculate_portfolio_performance,name='portfolio_ret')
]

urlpatterns = format_suffix_patterns(urlpatterns)