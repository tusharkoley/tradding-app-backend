from rest_framework.routers import SimpleRouter

from .views import AssetViewSet, CategoryViewSet, HoldingViewSet, PortfolioViewSet, TransactionViewSet

app_name = 'portfolio'
router = SimpleRouter()
router.register('portfolios', PortfolioViewSet, basename='portfolio')
router.register('categories', CategoryViewSet, basename='category')
router.register('assets', AssetViewSet, basename='asset')
router.register('transactions', TransactionViewSet, basename='transaction')
router.register('holdings', HoldingViewSet, basename='holding')
urlpatterns = router.urls
