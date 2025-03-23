from django.db import models
from django.utils import timezone
from users.models import Profile

transaction_types = (
    ('Buy','buy'),
    ('Sell','sell'),
    ('Deposite','deposite'),
    ('Withdraw','withdraw'),
    ('Divident','divident'),
    ('Interest','interest'),
    ('Brokarage','brokarage'),
    ('Fees','fees')
)

security_types = (
    ('Stocks','stocks'),
    ('MF','mf'),
    ('Bond','bond'),
    ('ETF','etf')

)

# Create your models here.
class Transactions(models.Model):
    transaction_type = models.TextField(choices=transaction_types, default='Deposite')
    transaction_date = models.DateTimeField(default=timezone.now)
    amount = models.FloatField()
    user = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='transactions') 
    ticker = models.CharField(max_length=50, null=True)


class Portfolio(models.Model):

    user = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='portfolios') 
    security_type = models.CharField(choices=security_types, default='Stocks')
    ticker = models.CharField(max_length=50)
    quantity = models.FloatField(null=True)

    



    



