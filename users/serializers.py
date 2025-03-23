

from .models import Profile
from rest_framework.serializers import ModelSerializer, Serializer
from rest_framework import serializers
from django.contrib.auth import get_user_model


from django.core.validators import validate_email
from django.core.exceptions import ValidationError


class ProfileSerializer(ModelSerializer):

    class Meta:
        model=Profile
        fields = ['first_name','middle_name','last_name','email','password','phone_number','is_active']
        extra_kwargs = {'password': {'write_only': True, 'min_length': 5}}

    def create(self, validated_data):
        user = Profile.objects.create_user(**validated_data)
        return user
    
    def to_representation(self, instance):
        """Overriding to remove Password Field when returning Data"""
        ret = super().to_representation(instance)
        ret.pop('password', None)
        return ret
    
    from rest_framework import serializers


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    def validate_email(self, value):
        try:
            print('*****Inside Validate email ****')
            validate_email(value)
        except ValidationError:
            raise serializers.ValidationError("Invalid email address")
        
        if not Profile.objects.filter(email=value).exists():
            raise serializers.ValidationError("No user found with this email address.")
        
        return value
