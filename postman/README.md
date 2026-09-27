# TradeZen Postman collection

## Import and use

1. Import `TradeZen.postman_collection.json` and either or both environment JSON files into Postman.
2. Select **TradeZen Remote** for `https://tradezen.up.railway.app`, or **TradeZen Local** for `http://127.0.0.1:8000`. The remote URL comes from the frontend configuration; change `base_url` if your deployment uses a different host. Do not add a trailing slash.
3. Set `email` to your real email and `password` to a strong, unique password. These values are intentionally blank in the exported files. Keep populated environments private.
4. Send **Authentication → Sign up**. It expects **201 Created**. Inspect the response body and test results if it fails.
5. Copy `activation_uid` and `activation_token` from the emailed URL into the environment, then send **Activate account**. Alternatively, open the email link in a browser.
6. Send **Login — save tokens and user ID**. Its response script saves the tokens and your user ID in the selected environment. Protected requests inherit Bearer authentication.
7. Run individual requests in the relevant folder. Set `ticker` to an existing ticker (default `AAPL.US`). Set `company_id` and `price_id` to existing record IDs for detail requests. Creating records captures their IDs when successful.

There are 32 requests covering all explicitly implemented methods on registered application API routes, including buy/sell and deposit/withdraw examples. Django admin browser pages and automatic HEAD/OPTIONS variants are excluded. Public requests explicitly disable inherited authentication. No remote requests were executed when generating this collection.

Use a staff account for user listing and company/price writes. Newly registered accounts are not staff. The collection includes deletes, password changes and balance-changing operations: use disposable records and run requests individually, rather than running the whole collection against live data.

## Diagnosing signup HTTP 400

The sign-up endpoint is **POST `{{base_url}}/users/`**, not `/signup/`. The frontend page is `/signup`; the backend registration route is `/users/`.

Send raw JSON with `Content-Type: application/json`. The collection supplies these settings. The request has first name, last name, email and password; names are optional. Example structure:

```json
{
  "first_name": "Postman",
  "last_name": "Tester",
  "email": "your-real-unused-email@example.com",
  "password": "replace-with-a-strong-unique-password"
}
```

For a 400 response, Postman test results display each returned validation error. The response body is the source of truth:

- `email`: check email format and whether the account already exists. Repeating a successful registration returns a duplicate error; log in instead.
- `password`: current local code requires at least 8 characters and applies Django's common-password, numeric-password and personal-detail checks.
- `phone_number`: omit it if unused; otherwise use a valid international phone number.
- `first_name`, `middle_name`, `last_name`: maximum 50 characters each. Email maximum is 100.
- JSON parse errors: ensure the body is valid JSON and variables are populated. If manually entering a password with quotes or backslashes into a raw JSON template, JSON-escape it appropriately.
- HTML instead of JSON: inspect the body and requested host; the request may have been rejected before reaching the API.

Do not send `is_active` to bypass activation; it is read-only in the current local code. Email delivery errors return 503 locally, while earlier deployed code may return 500 or leave an inactive account behind. A remote deployment may not contain the local signup fixes. This collection does not deploy code, and the reported remote 400 has not been reproduced. Share the response body, with personal information removed, to identify its exact cause.

## Current backend limitations

- No JWT refresh/logout API routes are registered. `refresh_token` is saved for inspection; log in again when access expires.
- Activation returns HTML and may return 200 for invalid tokens. Password-reset confirmation returns JSON and rejects invalid, expired or used tokens with 400. Reset emails point to the frontend form; copy its UID/token into the environment to test confirmation through Postman.
- Profile responses omit IDs. Login extracts your own ID from the JWT payload for convenience; this is not signature verification.
- Profile detail URLs and portfolio performance have **no trailing slash**. Other collection routes preserve the slash in Django's route definitions.
- Profile PUT/PATCH email validation currently rejects an unchanged email. Password updates through the generic profile endpoint do not hash passwords. Use PATCH for name changes and the password-reset endpoints for passwords. PUT is included and labelled for completeness.
- Price creation currently has a handler signature bug: the URL supplies `ticker`, but `PriceList.post` does not accept it. The request is included with its intended 201 expectation; it will fail until the backend is fixed.
- Company creation and price creation expect arrays. Company sector is spelled `soctor` in the current API. Price ticker/date pairs must be unique.
- Transaction handlers currently do not enforce authentication or ownership and directly modify stored balances/holdings. Bearer authentication is inherited in the collection, but that does not add backend enforcement. Transaction and portfolio responses may use an HTML content type, even when the body contains JSON.
- Cached stock lists/rankings may not reflect writes immediately. Portfolio valuation depends on the external price provider.
