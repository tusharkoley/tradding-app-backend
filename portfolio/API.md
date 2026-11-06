# Portfolio registry API — asset management contract

Base path: `/portfolio/`. All endpoints require the existing JWT Bearer token
from `POST /users/login/`; existing session authentication is also supported,
with CSRF checks for session-authenticated writes. No new authentication is added.
All user-owned lookups are scoped server-side. A foreign detail ID returns 404;
foreign IDs in submitted relations are rejected with 400. Staff has no bypass.

| Endpoint | Methods | Purpose |
| --- | --- | --- |
| `portfolios/` | GET, POST | Paginated list; create owned portfolio |
| `portfolios/{id}/` | GET, PUT, PATCH | Read/update portfolio; owner and base currency immutable |
| `categories/` | GET | Active database category definitions |
| `categories/{id}/` | GET | Active category definition |
| `assets/` | GET, POST | List/create assets |
| `assets/{id}/` | GET, PUT, PATCH | Read/update descriptive fields and valuation preference |
| `assets/{id}/archive/` | POST | Idempotent archival, preserving ledger/history |
| `assets/{id}/summary/` | GET | As-of holdings and ledger statistics |
| `transactions/` | GET, POST | Append supported entries; full paginated history |
| `transactions/{id}/` | GET | Immutable recorded entry |
| `transactions/{id}/reverse/` | POST | Exact audited negation; requires reason |
| `transactions/{id}/correct/` | POST | Atomic reversal + linked replacement; requires reason and changes |
| `holdings/` | GET | Paginated as-of quantities, including archived assets unless filtered |

List responses use `{count, next, previous, results}`. Default page size is 25;
`page` and `page_size` (maximum 100) control pagination. Decimal values are JSON
strings: quantities/prices have 12 decimal places; amounts/fees have 8.
Financial input should also use decimal strings. Unknown or read-only body fields
are errors, not silently ignored. Deletion and transaction PUT/PATCH are unavailable.

Common list parameters: `search`, `ordering`, `page`, `page_size`.
Portfolios filter by `is_archived`, `base_currency`; assets/holdings by `portfolio`,
`category`, `category_code`, `currency`, `is_archived`, `valuation_method`.
Transactions filter by `asset`, `portfolio`, `category`, `kind`, `date_from`, `date_to`.
Holdings and asset summaries accept `as_of=YYYY-MM-DD` (default today).
Invalid filter values return 400. Foreign ownership filters produce an empty list.
Categories search code/name and filter `is_liability`.

Categories expose `metadata_schema` (applicable category-specific fields),
`supported_transaction_types`, `transaction_policy` and `valuation_strategy`.
The strategy is `manual`, `market`, or `manual_or_market`; this only configures
allowed asset valuation preferences. No pricing is performed. Financial fields
remain relational. No runtime behaviour branches on hardcoded category names.

Transactions use signed quantity and signed amount. Buy/deposit/opening quantities
are positive; sell/withdrawal quantities negative. Adjustments are nonzero and need
notes. Prices cannot be negative. A supplied unit price must reconcile with amount
(half-even rounding to 8 places). Cash quantities equal their currency-unit amount.
Currency must match the asset. Ordinary records on archived assets/portfolios are
rejected; reversals/corrections remain available to repair historical errors.

All ledger writes lock the asset, then validate the complete dated quantity history.
No date may end with negative holdings, including after a backdated correction or
reversal. Because effective dates have day precision, same-date entries are evaluated
together. Future entries do not affect today's holdings but must also pass this rule.
Short selling is not supported. PostgreSQL row locking serializes concurrent writes.
Correction batches validate the final combined ledger, not their temporary reversal
state. Fees do not automatically create cash ledger entries.

Success codes: 200 reads/updates/archive, 201 creates/reversals/corrections.
Errors follow existing DRF conventions: 400 field errors or `non_field_errors`,
401 missing/invalid JWT, 403 session CSRF/permission failures, 404 missing/foreign IDs,
405 unsupported methods, 429 throttling. A failed write leaves ledger and audit
unchanged. Ordinary POST creation is not idempotent: do not blindly retry an unknown
outcome. Reversals can occur only once and corrections are explicitly linked.

Holdings/summaries contain ledger quantities and recorded valuations, without live
market pricing, profit calculations or net-worth dashboards.
Archived positions are included by default so archival cannot hide held quantities.

## Frontend support (Phase 3)

`metadata_schema.x-entry` contains database-configured entry labels, units, optional
symbol input, balance mode and valuation basis. Migration `0005` adds this configuration
and optional category fields (exchange, metal type, property type/location and pension
fund name). Existing metadata remains valid. No production fixture data is installed.

Asset creation accepts optional `opening_transaction` and `initial_valuation` objects.
They are saved in the same database transaction as the asset, including audit records.
Any validation failure rolls back the entire registration. These fields are create-only.

```json
{
  "portfolio": 1, "category": 1, "name": "My investment", "currency": "GBP", "unit": "share",
  "opening_transaction": {"transaction_date": "2026-11-05", "quantity": "10", "unit_price": "20", "amount": "200"},
  "initial_valuation": {"valued_at": "2026-11-05T00:00:00Z", "basis": "unit", "value": "25", "source": "Statement"}
}
```

`GET /portfolio/assets/{id}/valuations/` returns paginated immutable valuation history.
`POST` to the same endpoint appends a manual valuation, using the `initial_valuation`
shape above. Currency, method, asset and revision are assigned server-side. Repeating
an effective timestamp creates a higher revision; old records remain unchanged.
Future timestamps and negative values are rejected. The same owner and archive
rules apply as for other private records.

Holdings and summaries add `portfolio_name`, `category_name`, `recorded_value`,
`valuation_status`, `valuation_date` and `last_updated`. A recorded unit price is
multiplied by as-of holdings using Decimal arithmetic. A total valuation returns
`needs_review` and a null current recorded value when quantity no longer matches
the holdings known when that valuation was recorded. No valuation yields `unvalued`.
Dates and sources remain visible; these values are not presented as live prices.
Transaction history adds `is_reversed` to help clients disable duplicate corrections.

## Examples

Use `Authorization: Bearer <existing-access-token>` and `Content-Type: application/json`
on writes. Fetch category IDs from `GET /portfolio/categories/`; example IDs below
are illustrative and are not guaranteed across installations.

Create a portfolio with `POST /portfolio/portfolios/`:

```json
{"name": "Personal", "base_currency": "GBP"}
```

201 response:

```json
{
  "id": 1, "owner": 7, "name": "Personal", "base_currency": "GBP",
  "is_archived": false, "created_at": "2026-11-05T12:00:00Z",
  "updated_at": "2026-11-05T12:00:00Z"
}
```

Create an asset with `POST /portfolio/assets/`:

```json
{
  "portfolio": 1, "category": 1, "name": "Example shares", "currency": "GBP",
  "unit": "share", "valuation_method": "manual", "metadata": {"isin": "GB1234567890"}
}
```

201 response includes the submitted fields plus `id`, `company: null`,
`market_symbol: ""`, `is_archived: false`, `created_at` and `updated_at`.
Update descriptive data with `PATCH /portfolio/assets/1/`:

```json
{"name": "Renamed shares", "metadata": {"isin": "GB1234567890"}}
```

`metadata` is replaced as a complete object, not deep-merged. Asset portfolio,
category, currency, unit and market references cannot be changed after creation.
An unchanged identity may be supplied on PUT. Asset archival uses
`POST /portfolio/assets/1/archive/` with `{}` and returns the asset with
`is_archived: true`; repeated requests do not create additional archive audit events.
Portfolio `is_archived` can be updated through PATCH.

Record a purchase with `POST /portfolio/transactions/`:

```json
{
  "asset": 1, "kind": "buy", "transaction_date": "2026-11-05",
  "quantity": "10", "unit_price": "2", "amount": "20", "fee": "0.25"
}
```

201 response:

```json
{
  "id": 1, "asset": 1, "kind": "buy", "transaction_date": "2026-11-05",
  "quantity": "10.000000000000", "unit_price": "2.000000000000",
  "amount": "20.00000000", "fee": "0.25000000", "currency": "GBP",
  "notes": "", "reversal_of": null, "correction_of": null,
  "created_at": "2026-11-05T12:00:00Z"
}
```

Currency defaults to the asset currency; fee defaults to zero. Amount is required;
unit price is optional or null. A sale uses negative quantity and amount:

```json
{"asset": 1, "kind": "sell", "transaction_date": "2026-11-06", "quantity": "-2", "unit_price": "2", "amount": "-4"}
```

An excessive sale returns 400, with no ledger or audit changes:

```json
{"quantity": ["Insufficient holdings on 2026-11-06; short selling is not supported."]}
```

To correct transaction 1, `POST /portfolio/transactions/1/correct/`:

```json
{"reason": "Quantity typo", "changes": {"quantity": "12", "amount": "24"}}
```

201 returns `{"reversal": {...}, "replacement": {...}}`. Both have the transaction
shape above. The reversal has `reversal_of: 1`, quantity `-10`, amount `-20` and fee
`-0.25`. The replacement has `correction_of: 1`, quantity `12`, amount `24` and fee
`0.25`. The original remains unchanged. `changes` supports `kind`,
`transaction_date`, `quantity`, `unit_price`, `amount`, `fee`, `notes`.
Changing quantity or unit price does not silently recompute amount; submit a
consistent amount. To correct again, target the replacement's ID.

To cancel an independent erroneous entry, `POST /portfolio/transactions/1/reverse/`:

```json
{"reason": "Duplicate import"}
```

201 returns the negating transaction. The default effective date is the original
date. Optional `transaction_date` may specify a later cancellation date. Reversing
an entry already reversed, reversing a reversal, or creating negative dated holdings
returns 400. A purchase already consumed by sales must be corrected coherently;
it cannot simply be removed from the ledger.

`GET /portfolio/holdings/?portfolio=1&as_of=2026-11-05&page_size=25`:

```json
{
  "count": 1, "next": null, "previous": null,
  "results": [{
    "asset": 1, "portfolio": 1, "category": 1, "category_code": "stocks",
    "name": "Example shares", "currency": "GBP", "unit": "share",
    "is_archived": false, "as_of": "2026-11-05", "quantity": "10.000000000000",
    "transaction_count": 1, "last_transaction_date": "2026-11-05"
  }]
}
```

`GET /portfolio/assets/1/summary/?as_of=2026-11-05` returns that holding object plus
`net_recorded_amount: "20.00000000"` and `net_fees: "0.25000000"`. These are signed
sums of the recorded ledger, including reversals, rather than market value or
remaining cost basis. Transaction counts include original and corrective records.

Search/ordering fields:

| Resource | Search | Allowed ordering |
| --- | --- | --- |
| Portfolios | name | id, name, created_at, updated_at |
| Categories | code, name | id, code, name |
| Assets/holdings | name, market_symbol, category name | id, name, created_at, updated_at |
| Transactions | notes, asset name, asset market_symbol | id, transaction_date, created_at, amount, quantity |

Defaults are `id` ascending, except transactions (`-transaction_date,-id`).
For a stable custom order, include `id` as a final tie-breaker. Search is
case-insensitive and follows DRF SearchFilter semantics. Page numbers can shift
when new records arrive; do not use pagination position as a financial identifier.

## Contract and verification

`openapi.json` is the machine-readable OpenAPI 3.1 contract, with request/response
schemas and examples for all 22 operations. This document explains financial rules
that schemas alone cannot express. Contract tests validate examples and actual
API responses against those schemas, and ensure documented operations resolve.

Run local model, API and contract tests:

```sh
../.venv/bin/python manage.py test portfolio --settings=portfolio.test_settings --noinput
```

For concurrency checks, explicitly configure `PORTFOLIO_TEST_DATABASE_URL` to a
disposable PostgreSQL test server and run:

```sh
../.venv/bin/python manage.py test portfolio.test_concurrency --settings=portfolio.postgres_test_settings --noinput
```

The SQLite suite skips these two row-lock tests. The configured application database
is never used by default test settings. No market-provider dependencies are called.
