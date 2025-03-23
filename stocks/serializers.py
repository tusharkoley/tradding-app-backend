
from rest_framework import serializers
from .models import Company, Price




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
    