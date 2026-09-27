from django.db import models
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from phonenumber_field.modelfields import PhoneNumberField
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin


class ProfileManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')

        user = self.model(email=email, **extra_fields) 
        user.set_password(password)
        user.save(using=self._db)

        return user

    def create_superuser(self, email, password=None, **extra_fields):
     
        extra_fields.setdefault('is_superuser', True)

        return self.create_user(email, password, **extra_fields)
                                

# Create your models here.
class Profile(AbstractBaseUser, PermissionsMixin):
    
    first_name = models.CharField(max_length=50, blank=True)
    middle_name = models.CharField(max_length=50, blank=True)
    last_name = models.CharField(max_length=50, blank=True)
    email = models.EmailField(max_length=100, unique=True)  # Make email mandatory and unique
    phone_number = PhoneNumberField(blank=True)
    is_active = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=True)
    cash_position = models.FloatField(default=0)
    objects = ProfileManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    def __str__(self):
        return f'{self.email} Profile'

    def clean_email(self):
        validate_email(self.email) 
        try:
            validate_email(self.email)
        except ValidationError:
            raise ValidationError('Invalid email address')
        return self.email

   
class Address(models.Model):
    house_number = models.IntegerField(null=True)
    address_line_1 = models.CharField(max_length=200)
    address_line_2 = models.CharField(max_length=200, null=True)
    address_line_3 = models.CharField(max_length=200, null=True)
    uesr = models.ForeignKey('Profile', on_delete=models.CASCADE)


class Accounts(models.Model):
    user = models.ForeignKey('Profile', on_delete=models.CASCADE)
    account_number = models.IntegerField()
    sort_code = models.IntegerField(null=True)
    swift_code = models.IntegerField(null=True)
    account_type = models.CharField(max_length=100)
    ifsc_code = models.IntegerField(null=True)

class Card(models.Model):
    user = models.ForeignKey('Profile', on_delete=models.CASCADE)
    card_number = models.IntegerField()
    card_expiry = models.CharField(max_length=50)
    card_cvp = models.IntegerField()



    



   




   
