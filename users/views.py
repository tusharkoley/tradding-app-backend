from django.contrib.auth.tokens import default_token_generator
import smtplib
import logging
from urllib.parse import quote
from django.conf import settings
from django.utils.html import format_html

from django.contrib.auth import login
from django.core.mail import send_mail
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import generics, status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, BasePermission, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Profile
from .serializers import PasswordResetRequestSerializer, PasswordResetConfirmSerializer, ProfileSerializer
from .tokens import account_activation_token


logger = logging.getLogger(__name__)


def log_email_failure(operation, exc):
    # Do not log provider messages: they may contain recipients or email content.
    logger.error(
        'Email delivery failed: operation=%s error=%s smtp_code=%s errno=%s '
        'host=%s port=%s username_configured=%s password_configured=%s',
        operation, type(exc).__name__, getattr(exc, 'smtp_code', None),
        getattr(exc, 'errno', None), settings.EMAIL_HOST, settings.EMAIL_PORT,
        bool(settings.EMAIL_HOST_USER), bool(settings.EMAIL_HOST_PASSWORD),
    )


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

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except (smtplib.SMTPException, OSError) as exc:
            log_email_failure('signup', exc)
            return Response(
                {'detail': 'We could not send your activation email. Please try again later.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


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

        return HttpResponse(format_html(
            '<h1>Your account is activated.</h1><p><a href="{}">Return to TradeZen to log in</a></p>',
            settings.FRONTEND_URL,
        ))

    return HttpResponse("invalid token")


class ResendActivationView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        users = Profile.objects.filter(
            email__iexact=serializer.validated_data['email'], is_active=False,
        )
        for user in users:
            path = reverse('users:activate', kwargs={
                'uidb64': urlsafe_base64_encode(force_bytes(user.pk)),
                'token': account_activation_token.make_token(user),
            })
            try:
                send_mail(
                    'Activate your TradeZen account',
                    f'Activate your account: {request.build_absolute_uri(path)}',
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=False,
                )
            except (smtplib.SMTPException, OSError) as exc:
                log_email_failure('resend_activation', exc)
                return Response(
                    {'detail': 'Unable to send the activation email. Please try again later.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
        return Response({'message': 'If this email belongs to an inactive account, a new activation link has been sent. Check your inbox and spam folder.'})


class LoginAPIView(generics.GenericAPIView):
    serializer_class = ProfileSerializer
    permission_classes = (AllowAny,)

    def post(self, request, *args, **kwargs):
        email = request.data.get("email")
        password = request.data.get("password")

        if not email or not password:
            raise AuthenticationFailed("Email and password are required.")

        try:
            user = Profile.objects.get(email__iexact=email.strip())
        except Profile.DoesNotExist as exc:
            raise AuthenticationFailed("Invalid credentials.") from exc

        if not user.check_password(password):
            raise AuthenticationFailed("Invalid credentials.")

        if not user.is_active:
            raise AuthenticationFailed("Please activate your account using the link in your email before logging in.")

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
            user = Profile.objects.get(email__iexact=email)
        except Profile.DoesNotExist:
            # Do not leak whether an account exists.
            return Response(
                {"message": "If an account exists, a password reset link has been sent."},
                status=status.HTTP_200_OK,
            )

        token = default_token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        reset_link = f"{settings.FRONTEND_URL.rstrip('/')}/reset-password/{uid}/{token}"

        try:
            send_mail(
                "Reset your TradeZen password",
                f"Open this link to choose a new password: {reset_link}",
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
        except (smtplib.SMTPException, OSError) as exc:
            log_email_failure('password_reset', exc)
            return Response(
                {'detail': 'Unable to send the reset email. Please try again later.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {"message": "If an account exists, a password reset link has been sent."},
            status=status.HTTP_200_OK,
        )


class PasswordResetRequestConfirmView(APIView):
    queryset = Profile.objects.all()
    permission_classes = [AllowAny]

    def get(self, request, uidb64, token):
        # Older emails linked directly to this API instead of the frontend form.
        response = HttpResponseRedirect(
            f"{settings.FRONTEND_URL.rstrip('/')}/reset-password/"
            f"{quote(uidb64, safe='')}/{quote(token, safe='')}"
        )
        response['Cache-Control'] = 'no-store'
        response['Referrer-Policy'] = 'no-referrer'
        return response

    def post(self, request, uidb64, token):
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = Profile.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, Profile.DoesNotExist):
            user = None

        if user is None or not default_token_generator.check_token(user, token):
            return Response(
                {'detail': 'This reset link is invalid or expired. Request a new link.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = PasswordResetConfirmSerializer(data=request.data, context={'user': user})
        serializer.is_valid(raise_exception=True)
        user.set_password(serializer.validated_data['password1'])
        user.save(update_fields=['password'])
        return Response({'message': 'Password updated. You can now log in with your new password.'})
