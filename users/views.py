from django.contrib.auth import login
from django.core.mail import send_mail
from django.http import HttpResponse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import generics, status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, BasePermission, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Profile
from .serializers import PasswordResetRequestSerializer, ProfileSerializer
from .tokens import account_activation_token


class IsSelfOrStaff(BasePermission):
    def has_object_permission(self, request, view, obj):
        user = request.user
        return bool(user and user.is_authenticated and (user.is_staff or obj.pk == user.pk))


class ProfileListCreateView(generics.ListCreateAPIView):
    queryset = Profile.objects.all()
    serializer_class = ProfileSerializer

    def get_permissions(self):
        # Allow public signup, but keep user listing admin-only.
        if self.request.method == "POST":
            return [AllowAny()]
        return [IsAdminUser()]


class ProfileDetails(generics.RetrieveUpdateDestroyAPIView):
    queryset = Profile.objects.all()
    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated, IsSelfOrStaff]


def activate(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = Profile.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, Profile.DoesNotExist):
        user = None

    if user is not None and account_activation_token.check_token(user, token):
        user.is_active = True
        user.save()

        login_url = "http://localhost:8000/login"
        return HttpResponse(
            f""" <HR> <BR><h1>The Acccount activated successfully. Thank you for registering trade zone.<h1>
                            <h2> Please click  <a href=\"{login_url}\">here</a> to login </h2>

                            """
        )

    return HttpResponse("invalid token")


class LoginAPIView(generics.GenericAPIView):
    serializer_class = ProfileSerializer
    permission_classes = (AllowAny,)

    def post(self, request, *args, **kwargs):
        email = request.data.get("email")
        password = request.data.get("password")

        if not email or not password:
            raise AuthenticationFailed("Email and password are required.")

        try:
            user = Profile.objects.get(email=email)
        except Profile.DoesNotExist as exc:
            raise AuthenticationFailed("Invalid credentials.") from exc

        if not user.check_password(password):
            raise AuthenticationFailed("Invalid credentials.")

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
            }
        )


class PasswordResetRequestView(APIView):
    queryset = Profile.objects.all()
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data["email"]

        try:
            user = Profile.objects.get(email=email)
        except Profile.DoesNotExist:
            # Do not leak whether an account exists.
            return Response(
                {"message": "If an account exists, a password reset link has been sent."},
                status=status.HTTP_200_OK,
            )

        token = account_activation_token.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        reset_url = f"users/password-reset-confirm/{uid}/{token}"
        reset_link = f"http://{request.get_host()}/{reset_url}"

        send_mail(
            "Password Reset Request",
            f"Click the link to reset your password: {reset_link}",
            "from@example.com",
            [email],
            fail_silently=False,
        )

        return Response(
            {"message": "If an account exists, a password reset link has been sent."},
            status=status.HTTP_200_OK,
        )


class PasswordResetRequestConfirmView(APIView):
    queryset = Profile.objects.all()
    permission_classes = [AllowAny]

    def post(self, request, uidb64, token):
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = Profile.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, Profile.DoesNotExist):
            user = None

        if user is None or not account_activation_token.check_token(user, token):
            return HttpResponse("invalid token")

        password1 = request.data.get("password1")
        password2 = request.data.get("password2")

        if password1 != password2:
            return Response({"message": "Password don't match"}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(password1)
        user.save()

        return HttpResponse(
            """ <HR> <BR><h1> Your psssword was reset successfully <h1>
                                """
        )
