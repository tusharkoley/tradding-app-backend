# Account registration

The frontend `/signup` page posts to `/users/`. Accounts start inactive and without staff access. Users activate their account through the emailed link before logging in.

Configure these backend environment variables:

- `EMAIL_HOST_USER` and `EMAIL_HOST_PASSWORD`: Gmail SMTP credentials (use an app password where required).
- `EMAIL_PORT`: defaults to `587` with TLS.
- `DEFAULT_FROM_EMAIL`: defaults to `EMAIL_HOST_USER`.
- `FRONTEND_URL`: public frontend URL used after activation; defaults to the first configured frontend origin.

For local development without email delivery, set `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend` and open the activation link printed by the backend. Production uses the SMTP backend by default. Activation links use the incoming API request's scheme and host.

Registration returns validation errors for invalid or duplicate emails and weak passwords. Mail delivery failures roll back account creation so users can retry.

Email failures in signup, activation resend, and password reset log `Email delivery failed` with the operation, exception type, SMTP status code, host/port, and whether credentials are configured. Credentials, recipient addresses, tokens, and provider error text are omitted. Check backend deployment logs after retrying: `SMTPAuthenticationError`/535 indicates rejected authentication; a timeout or network error indicates a connectivity problem. Confirm both SMTP variables are set on the backend service. Gmail requires an app password for this password-based SMTP flow.

Railway currently permits outbound SMTP only on Pro and above; Free, Trial, and Hobby deployments need an email provider's HTTPS API instead. See [Railway outbound networking](https://docs.railway.com/networking/outbound-networking). Changing the frontend URL does not resolve email delivery failures.

Run `python manage.py test users` against the configured test database. Existing historical migrations currently fail on SQLite; an isolated SQLite check can instead disable migrations for `users`, `stocks`, and `transactions` and create tables from their current models. This verifies registration behavior, not migration compatibility.

## Password reset

For migrated or unactivated accounts, use the login dialog's **Resend activation email** link (`/resend-activation` on the frontend). It posts an email address to `/users/resend-activation/`, which sends inactive accounts a fresh activation link on the current API host. Active and unknown accounts receive the same success message without an email. This preserves the existing account and its data; password resets do not activate accounts. Deploy both frontend and backend to enable this flow.

Browser GET requests to `/users/password-reset-confirm/<uid>/<token>/` redirect to the frontend reset form, supporting emails that linked directly to the API. Password changes still require POST with matching passwords and a valid reset token. Configure `FRONTEND_URL` to the running frontend before deploying; request a fresh email if an older token is rejected.

The login dialog links to `/forgot-password`. Reset emails open `/reset-password/<uid>/<token>` on `FRONTEND_URL`; configure this to your deployed frontend origin and ensure the host serves the SPA for nested routes. The form posts matching passwords to `/users/password-reset-confirm/<uid>/<token>/`. Reset tokens expire according to Django’s `PASSWORD_RESET_TIMEOUT` and become invalid after a password change. Request a fresh link after deploying this fix; old activation-style reset tokens are no longer accepted. SMTP credentials are also required for reset emails.
