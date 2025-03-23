from django.shortcuts import render

from .models import Price, Company
from .serializers import CompanySerializer, PriceSerilizer, CompanyListSerializer,PriceListSerializer
from rest_framework import generics

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework import viewsets
from rest_framework.views import APIView
from rest_framework import status
from rest_framework import mixins


@api_view(['GET'])
def api_root(request, format=None):
    return Response({
        'companies': reverse('companies', request=request, format=format),
        'company/prices': reverse('company/prices', request=request, format=format),

    })


class CompanyList(APIView):
    queryset = Company.objects.all()
    serializer_class = CompanySerializer
    

    def get(self, request, format=None):
        Companies = Company.objects.all()
        serializer = CompanySerializer(Companies, many=True)
        return Response(serializer.data)
    
    def post(self, request, format=None):
        serializer = CompanyListSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)




class CompanyDetails(generics.RetrieveUpdateDestroyAPIView):
    queryset = Company.objects.all()
    serializer_class = CompanySerializer


class PriceList(APIView):
    queryset = Company.objects.all()
    serializer_class = PriceSerilizer

    def get(self, request, ticker, format=None):
        prices = Price.objects.filter(ticker=ticker)
        serializer = PriceSerilizer(prices, many=True)
        return Response(serializer.data)
    
    def post(self, request, format=None):
        serializer = PriceListSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



class PriceDetails(generics.RetrieveUpdateDestroyAPIView):
    queryset = Price.objects.all()
    serializer_class = PriceSerilizer


@api_view(['GET'])
def get_price_by_ticker(request):
    ticker = request.ticker
    prices = Price.objects.all().filter(ticker==ticker)
    serializer = PriceSerilizer(prices, many=True)

    return  Response(serializer.data)







