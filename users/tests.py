import re
from unittest.mock import patch
from smtplib import SMTPException, SMTPAuthenticationError
from django.core import mail
from django.test import override_settings
from rest_framework.test import APITestCase
from .models import Profile


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SignupTests(APITestCase):
    def setUp(self):
        self.payload = {'email': 'NewUser@example.com', 'password': 'River!Orbit7259', 'first_name': 'New'}

    def test_signup_activation_and_login(self):
        response = self.client.post('/users/', {**self.payload, 'is_active': True}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertNotIn('password', response.data)
        user = Profile.objects.get(email='newuser@example.com')
        self.assertTrue(user.check_password(self.payload['password']))
        self.assertFalse(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.client.post('/users/login/', self.payload).status_code, 401)
        activation_url = re.search(r'http://testserver(/users/activate/\S+)', mail.outbox[0].body).group(1)
        self.assertEqual(self.client.get(activation_url).status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        response = self.client.post('/users/login/', self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.data)

    def test_duplicate_email_is_case_insensitive(self):
        self.client.post('/users/', self.payload)
        response = self.client.post('/users/', {**self.payload, 'email': 'NEWUSER@example.com'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('email', response.data)
        self.assertEqual(Profile.objects.count(), 1)

    def test_invalid_email_and_weak_password(self):
        for changes, field in [({'email': 'invalid'}, 'email'), ({'password': '12345678'}, 'password')]:
            response = self.client.post('/users/', {**self.payload, **changes})
            self.assertEqual(response.status_code, 400)
            self.assertIn(field, response.data)
        self.assertEqual(Profile.objects.count(), 0)

    @patch('users.serializers.send_mail', side_effect=SMTPException('Unavailable'))
    def test_email_failure_allows_retry(self, mocked_send):
        response = self.client.post('/users/', self.payload)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(Profile.objects.count(), 0)
        mocked_send.assert_called_once()


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FRONTEND_URL='https://app.example.com')
class ResendActivationTests(APITestCase):
    def setUp(self):
        self.user = Profile.objects.create_user(
            email='migrated@example.com', password='River!Orbit7259',
            is_active=False, cash_position=123,
        )

    def test_fresh_link_activates_existing_account_and_allows_login(self):
        response = self.client.post('/users/resend-activation/', {'email': 'MIGRATED@example.com'}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        path = re.search(r'https://testserver(/users/activate/\S+)', mail.outbox[0].body).group(1)
        self.assertEqual(self.client.get(path).status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertEqual(self.user.cash_position, 123)
        self.assertEqual(Profile.objects.count(), 1)
        self.assertEqual(self.client.post('/users/login/', {
            'email': self.user.email, 'password': 'River!Orbit7259',
        }).status_code, 200)

    def test_active_and_unknown_accounts_receive_same_response_without_email(self):
        known = self.client.post('/users/resend-activation/', {'email': self.user.email})
        mail.outbox.clear()
        self.user.is_active = True
        self.user.save(update_fields=['is_active'])
        for email in [self.user.email, 'unknown@example.com']:
            response = self.client.post('/users/resend-activation/', {'email': email})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, known.data)
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_email(self):
        for payload in [{}, {'email': 'invalid'}]:
            self.assertEqual(self.client.post('/users/resend-activation/', payload).status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    @patch('users.views.send_mail', side_effect=SMTPException('Unavailable'))
    def test_delivery_failure_keeps_account_inactive(self, mocked_send):
        response = self.client.post('/users/resend-activation/', {'email': self.user.email})
        self.assertEqual(response.status_code, 503)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertTrue(self.user.check_password('River!Orbit7259'))

    def test_delivery_errors_log_diagnostics_without_provider_message(self):
        for exc in [SMTPAuthenticationError(535, b'private provider message'), TimeoutError('private timeout message')]:
            with self.subTest(error=type(exc).__name__), patch('users.views.send_mail', side_effect=exc):
                with self.assertLogs('users.views', level='ERROR') as logs:
                    response = self.client.post('/users/resend-activation/', {'email': self.user.email})
                self.assertEqual(response.status_code, 503)
                output = ' '.join(logs.output)
                self.assertIn('operation=resend_activation', output)
                self.assertIn(type(exc).__name__, output)
                self.assertNotIn('private', output)
                self.assertNotIn(self.user.email, output)
                if isinstance(exc, SMTPAuthenticationError):
                    self.assertIn('smtp_code=535', output)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FRONTEND_URL='https://app.example.com')
class PasswordResetTests(APITestCase):
    def setUp(self):
        self.user = Profile.objects.create_user(email='reset@example.com', password='Old!Orbit7259', is_active=True)

    def reset_url(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        return f'/users/password-reset-confirm/{urlsafe_base64_encode(force_bytes(self.user.pk))}/{default_token_generator.make_token(self.user)}/'

    def test_email_link_reset_and_login(self):
        response = self.client.post('/users/password-reset/', {'email': 'RESET@example.com'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        path = re.search(r'https://app.example.com/reset-password/(\S+)', mail.outbox[0].body).group(1)
        url = f'/users/password-reset-confirm/{path}/'
        payload = {'password1': 'New!River8264', 'password2': 'New!River8264'}
        self.assertEqual(self.client.post(url, payload).status_code, 200)
        self.assertEqual(self.client.post(url, payload).status_code, 400)
        self.assertEqual(self.client.post('/users/login/', {'email': self.user.email, 'password': 'Old!Orbit7259'}).status_code, 401)
        self.assertEqual(self.client.post('/users/login/', {'email': self.user.email, 'password': payload['password1']}).status_code, 200)

    @override_settings(FRONTEND_URL='https://app.example.com/')
    def test_api_email_link_opens_form_without_changing_password(self):
        url = self.reset_url()
        response = self.client.get(url)
        path = url.removeprefix('/users/password-reset-confirm/').rstrip('/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], f'https://app.example.com/reset-password/{path}')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Old!Orbit7259'))
        payload = {'password1': 'New!River8264', 'password2': 'New!River8264'}
        self.assertEqual(self.client.post(url, payload).status_code, 200)

    def test_unknown_email_has_same_response(self):
        known = self.client.post('/users/password-reset/', {'email': self.user.email})
        unknown = self.client.post('/users/password-reset/', {'email': 'unknown@example.com'})
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(known.data, unknown.data)
        self.assertEqual(len(mail.outbox), 1)

    def test_missing_mismatched_and_weak_passwords(self):
        url = self.reset_url()
        for payload in [{}, {'password1': 'Strong!River7259', 'password2': 'different'}, {'password1': '12345678', 'password2': '12345678'}]:
            self.assertEqual(self.client.post(url, payload).status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Old!Orbit7259'))

    def test_invalid_and_expired_tokens(self):
        url = self.reset_url()
        payload = {'password1': 'New!River8264', 'password2': 'New!River8264'}
        self.assertEqual(self.client.post('/users/password-reset-confirm/invalid/invalid/', payload).status_code, 400)
        self.assertEqual(self.client.post(url[:-1] + 'invalid/', payload).status_code, 400)
        with override_settings(PASSWORD_RESET_TIMEOUT=-1):
            self.assertEqual(self.client.post(url, payload).status_code, 400)

    @patch('users.views.send_mail', side_effect=SMTPException('Unavailable'))
    def test_mail_failure(self, mocked_send):
        self.assertEqual(self.client.post('/users/password-reset/', {'email': self.user.email}).status_code, 503)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Old!Orbit7259'))
