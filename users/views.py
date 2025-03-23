from django.shortcuts import render
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated, AllowAny
from .serializers import ProfileSerializer
from .models import Profile
from django.utils.encoding import  force_str
from django.utils.http import  urlsafe_base64_decode
from django.contrib.auth import authenticate
from rest_framework import exceptions
from .tokens import account_activation_token
from django.contrib.auth import login
from django.http import HttpResponse
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.response import Response
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.views import APIView
from django.contrib.auth.tokens import default_token_generator
from .serializers import PasswordResetRequestSerializer
from django.urls import reverse
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.core.mail import send_mail
from rest_framework import status
from .tokens import account_activation_token




class ProfileListCreateView(generics.ListCreateAPIView):
    queryset = Profile.objects.all()
    serializer_class = ProfileSerializer
    permission_classes = [AllowAny]



class ProfileDetails(generics.RetrieveUpdateDestroyAPIView):
    queryset = Profile.objects.all()
    serializer_class = ProfileSerializer


def activate(request, uidb64, token):
     try:
        print(f'Inside activate tokken : {uidb64}, {token}')
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = Profile.objects.get(pk=uid)
     except(TypeError, ValueError, OverflowError, Profile.DoesNotExist):
        user = None
     if user is not None and account_activation_token.check_token(user, token):
        user.is_active = True
        user.save()

        login_url = 'http://localhost:8000/login'
        return HttpResponse(f""" <HR> <BR><h1>The Acccount activated successfully. Thank you for registering trade zone.<h1>
                            <h2> Please click  <a href="{login_url}">here</a> to login </h2>
                     
                            """)
     
     else:
        return HttpResponse('invalid token')
     


class LoginAPIView(generics.GenericAPIView):
    serializer_class = ProfileSerializer
    permission_classes = (AllowAny,)

    def post(self, request, *args, **kwargs):
        print('***** Inside the login method ******')

        print('***** Inside the login method serializer ******', request.data)

        email = request.data['email']
      
        print('***** Inside the login method email ******', email)
        user = Profile.objects.get(email=email)
        print('***** Inside the login method Uzser ******')

        if not user.check_password(request.data.get('password')):
                raise AuthenticationFailed('Incorrect password.') 

     
        refresh = RefreshToken.for_user(user) 

        return Response({
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        })
  

     
class PasswordResetRequestView(APIView):
    queryset = Profile.objects.all()
    def post(self, request):
        print('*** Inside password reset ****')
        serializer = PasswordResetRequestSerializer(data=request.data)
        if serializer.is_valid():
            email = serializer.validated_data['email']
            user = Profile.objects.get(email=email)
            
            # Generate token and uid
            token = account_activation_token.make_token(user)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            
            # Create reset link
            reset_url = f'users/password-reset-confirm/{uid}/{token}'
            # reset_url = reverse('password-reset-confirm', kwargs={'uid': uid, 'token': token})
            reset_link = f"http://{request.get_host()}/{reset_url}"

            print(f' reset_link : {reset_link}')
            
            # Send email
            send_mail(
                'Password Reset Request',
                f'Click the link to reset your password: {reset_link}',
                'from@example.com',
                [email],
                fail_silently=False,
            )
            
            return Response({"message": "Password reset link sent to your email."}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    

class PasswordResetRequestConfirmView(APIView):
    queryset = Profile.objects.all()
    def post(self, request, uidb64, token):
        try:
            print(f'Inside activate tokken : {uidb64}, {token}')
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = Profile.objects.get(pk=uid)
        except(TypeError, ValueError, OverflowError, Profile.DoesNotExist):
            user = None
        if user is not None and account_activation_token.check_token(user, token):
            password1 = request.data.get('password1')
            password2 = request.data.get('password2')

            if password1!=password2:
                return Response({"message": "Password don't match"}, status=status.HTTP_400_BAD_REQUEST)

            user.password = password1
            user.save()

            return HttpResponse(f""" <HR> <BR><h1> Your psssword was reset successfully <h1>
                                        
                                """)
        
        else:
            return HttpResponse('invalid token')
    

# def password_reset_confirm(request, uidb64, token):
#      try:
#         print(f'Inside activate tokken : {uidb64}, {token}')
#         uid = force_str(urlsafe_base64_decode(uidb64))
#         user = Profile.objects.get(pk=uid)
#      except(TypeError, ValueError, OverflowError, Profile.DoesNotExist):
#         user = None
#      if user is not None and account_activation_token.check_token(user, token):
#         password1 = request.data.get('password1')
#         password2 = request.data.get('password2')

#         if password1!=password2:
#             return Response({"message": "Password don't match"}, status=status.HTTP_400_BAD_REQUEST)

#         user.password = password1
#         user.save()

#         return HttpResponse(f""" <HR> <BR><h1> Your psssword was reset successfully <h1>
                                     
#                             """)
     
#      else:
#         return HttpResponse('invalid token')
        