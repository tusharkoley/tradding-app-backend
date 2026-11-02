
from rest_framework import serializers
from .models import Company, Price, TechnicalIndicators




class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        # fields = ['id','ticker','company_name','industry','description','country','website','address']
        fields = '__all__'



class CompanyListSerializer(serializers.ListSerializer):
    child = CompanySerializer() 

    def create(self, validated_data):
        records = [Company(**item) for item in validated_data]
        Company.objects.bulk_create(records) 
        return records

    def update(self, instance, validated_data):
        for record, data in zip(instance, validated_data):
            self.child.update(record, data)
        return instance
    


class PriceSerilizer(serializers.ModelSerializer):
    class Meta:
        model = Price
        # fields = ['ticker','date','open','close','high','low', 'volume','dividends','stock_splits','company']
        fields = '__all__'


class PriceListSerializer(serializers.ListSerializer):
    child = PriceSerilizer() 

    def create(self, validated_data):
        records = [Price(**item) for item in validated_data]
        Price.objects.bulk_create(records) 
        return records

    def update(self, instance, validated_data):
        for record, data in zip(instance, validated_data):
            self.child.update(record, data)
        return instance


class HistoricalPriceSerializer(PriceSerilizer):
    """Chart response keeps raw-row metadata and uses split-adjusted OHLC."""

    open = serializers.FloatField(source='split_adjusted_price.open', read_only=True)
    high = serializers.FloatField(source='split_adjusted_price.high', read_only=True)
    low = serializers.FloatField(source='split_adjusted_price.low', read_only=True)
    close = serializers.FloatField(source='split_adjusted_price.close', read_only=True)


class TechnicalIndicatorsSerializer(serializers.ModelSerializer):
    class Meta:
        model = TechnicalIndicators
        fields = '__all__'

