from django.urls import path
from .views import ProfileDetails, ProfileListCreateView, activate, \
            LoginAPIView, PasswordResetRequestView, PasswordResetRequestConfirmView
from rest_framework.urlpatterns import format_suffix_patterns

app_name = 'users' 
urlpatterns = [
    path('',ProfileListCreateView.as_view()),
    path('<int:pk>',ProfileDetails.as_view()),
    path('activate/<str:uidb64>/<str:token>/', activate, name='activate'),
    path('login/', LoginAPIView.as_view(), name='login'),
    path('password-reset/', PasswordResetRequestView.as_view(), name='password-reset'),
    path('password-reset-confirm/<str:uidb64>/<str:token>/', 
         PasswordResetRequestConfirmView.as_view(), name='password_reset_confirm'),

]

urlpatterns = format_suffix_patterns(urlpatterns)