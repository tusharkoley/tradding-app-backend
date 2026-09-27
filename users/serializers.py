from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.core.mail import send_mail
from django.conf import settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from .tokens import account_activation_token



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
        read_only_fields = ['is_active']
        extra_kwargs = {'password': {'write_only': True, 'min_length': 8, 'trim_whitespace': False}}

    def validate_email(self, value):
        value = value.strip().lower()
        if Profile.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate(self, attrs):
        if 'password' in attrs:
            try:
                validate_password(attrs['password'], Profile(
                    email=attrs.get('email', ''),
                    first_name=attrs.get('first_name', ''),
                    last_name=attrs.get('last_name', ''),
                ))
            except ValidationError as exc:
                raise serializers.ValidationError({'password': exc.messages})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user = Profile.objects.create_user(**validated_data, is_active=False, is_staff=False)
        path = reverse('users:activate', kwargs={
            'uidb64': urlsafe_base64_encode(force_bytes(user.pk)),
            'token': account_activation_token.make_token(user),
        })
        activation_url = self.context['request'].build_absolute_uri(path)
        send_mail(
            'Activate your TradeZen account',
            f'Welcome to TradeZen! Activate your account: {activation_url}',
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
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
        return value.strip().lower()


class PasswordResetConfirmSerializer(serializers.Serializer):
    password1 = serializers.CharField(trim_whitespace=False)
    password2 = serializers.CharField(trim_whitespace=False)

    def validate(self, attrs):
        if attrs['password1'] != attrs['password2']:
            raise serializers.ValidationError({'password2': 'Passwords do not match.'})
        try:
            validate_password(attrs['password1'], self.context['user'])
        except ValidationError as exc:
            raise serializers.ValidationError({'password1': exc.messages})
        return attrs
