from django.db import models

# Create your models here.

class Company(models.Model):
    id = models.IntegerField(primary_key=True, auto_created=True)
    ticker = models.CharField(max_length=20)
    company_name = models.CharField(max_length=200)
    soctor = models.CharField(max_length=200, null=True),
    industry = models.CharField(max_length=200,null=True)
    description = models.CharField(max_length=2000)
    country = models.CharField(max_length=100)
    website = models.URLField(max_length=200,null=True)
    address = models.CharField(max_length=300)
    


class Price(models.Model):
    id = models.AutoField(primary_key=True)
    ticker = models.CharField(max_length=20)
    date = models.DateField()
    open = models.FloatField()
    close = models.FloatField()
    high = models.FloatField()
    low = models.FloatField()
    volume = models.IntegerField(),
    stock_splits = models.FloatField(),
    dividends = models.FloatField(),

    company = models.ForeignKey('Company',  related_name='company', on_delete=models.CASCADE)




