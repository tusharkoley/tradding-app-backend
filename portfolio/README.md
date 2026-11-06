# Portfolio tracker foundation

This app records personal assets and liabilities independently of trading execution.
It reuses `settings.AUTH_USER_MODEL` (`users.Profile`), the existing Django admin
and shared `stocks.Company` records. It never reads or mutates the legacy
`transactions.Portfolio`, `transactions.Transactions` or `Profile.cash_position`.
Authenticated registry APIs are mounted at `/portfolio/`; see [API.md](API.md) and
[openapi.json](openapi.json) for endpoints and examples. No provider calls, frontend,
dashboard or scheduled jobs are added.

## Relationships

```text
Existing user --< Portfolio --< Asset >-- AssetCategory
                             |   |
                             |   +-- optional stocks.Company
                             +--< AssetTransaction -- reversal_of / correction_of --> original transaction
                             +--< AssetValuation
                 Portfolio --< PortfolioSnapshot
                 Portfolio --< AuditLog >-- actor (existing user, optional for system writes)

CurrencyExchangeRate: shared dated reference data
AssetCategory: shared extensible reference data
```

### Ownership and access

Portfolios require an active existing user; multiple portfolios per user are allowed.
Owner and base currency cannot be reassigned after creation. Every asset belongs
to exactly one portfolio. Asset identity (portfolio, category, currency, unit and
market reference) is immutable; name, metadata, valuation preference and archive
status may be edited with audit logging.

Private querysets expose `objects.for_user(user)`; anonymous and inactive users
receive an empty queryset. Admin lists, detail permissions and related-object
choices are owner-scoped, including for superusers. Shared category/rate management
is restricted to superusers. Audit access follows the same ownership rule; only
superusers can inspect shared-reference events.

Python code and database credentials are trusted infrastructure. The ORM's default
manager is not row-level security. Future API views **must** require authentication,
scope all queries with `for_user(request.user)`, scope related-field querysets and
pass `actor=request.user` on writes. A staff flag is not a portfolio-access bypass.
The API uses these restrictions for all private resources. Existing unsafe trading
endpoints are unchanged.

### Categories and metadata

The second migration seeds stocks, ETFs, gold, silver, property, pensions, cash,
other assets and liabilities. Phase 2 adds mutual funds and explicit database-backed
`supported_transaction_types` and `valuation_strategy` configuration. Categories can
be extended through admin or trusted application code. `transaction_policy`
distinguishes currency-unit cash balances from quantity-based positions. The category's
supported transaction list determines which ledger kinds may be recorded.
`is_liability` separates amounts owed from positive asset values.

Asset metadata must be an object validated against the category's inline JSON
Schema (Draft 2020-12). No schema references or remote resolution are permitted.
Seed schemas allow only their declared optional attributes. Create a new category
or extend an unused category to add attributes. Category code, liability flag,
transaction policy, supported types, valuation strategy and schema cannot change once assets use them; create a new
category version instead. Historical entries therefore retain their interpretation.
Currencies, units, quantities, amounts and valuation methods use relational fields.
JSON never substitutes for essential financial fields.

### Financial ledger

- GBP, USD and INR are explicit supported currencies. Amounts and fees use
  `Decimal(28, 8)`; quantities, unit prices and FX rates use `Decimal(28, 12)`.
  PostgreSQL NUMERIC is the intended production storage. SQLite is only an offline
  test convenience and cannot establish production precision or locking guarantees.
- Transaction effective dates are separate from immutable system-created timestamps.
  Buy/deposit/opening entries increase units; sell/withdrawal entries decrease them.
  Signed adjustments require a reason. Zero-value gifts can have positive quantities.
- `amount` is signed book consideration excluding fees, in the asset's currency.
  If a unit price is supplied, amount must equal quantity × price, rounded half-even
  to eight decimals. Original fees are nonnegative; reversals negate them.
  Foreign-currency settlements are not modelled yet: use an asset-currency amount.
- Cash is an asset measured in currency units: quantity must equal amount and its
  optional unit price must be 1. Record both a cash withdrawal and a
  security purchase when tracking both legs; a purchase does not silently modify
  cash. Linked transfer groups, import idempotency, lot accounting and return
  calculations belong to later phases.
- `asset.quantity_as_of(date)` sums effective signed quantities; `current_quantity`
  uses today's date and excludes future entries. Omit the date to include all entries.
  Archived assets retain their full history. No mutable current-balance field exists.
  Historical quantities are restated when backdated corrections are recorded;
  existing snapshots remain intact. Every supported ledger writer locks the portfolio
  then asset and rejects a negative end-of-day balance anywhere in the dated history.
  Corrections validate the final reversal/replacement batch atomically. Short selling
  is not supported. Existing legacy negative histories must be reconciled before new
  writes can pass validation. No order execution is implied.
- For jointly owned property, record the owned fraction as held units (e.g. 0.5)
  and a whole-property unit valuation, or use a total valuation of the owned interest.
  Physical metals must use an explicit unit consistently; no automatic unit conversion
  or provider quote-unit conversion is performed.

### Reversals, corrections and retention

Transactions, valuations, exchange rates, snapshots and audit events are append-only
through supported application paths. Ordinary edits, deletes, queryset updates,
bulk writes and explicit-primary-key overwrites are rejected. Financial relationships
use `PROTECT`, including owner references, so deleting a user or shared company
cannot cascade into tracker history. Portfolios/assets can be archived instead.

Use `reverse_transaction(user=..., entry_id=..., reason=...)` to append an exact
negation. The original is locked and a one-to-one constraint prevents two reversals.
The default effective date is the original date (restatement); an explicit later
date records a later cancellation. A reversal cannot predate or reverse a reversal.

Use `correct_transaction(..., changes={...})` to reverse and replace in one database
transaction. The replacement links to the original through `correction_of`; failures
roll back both financial and audit writes. Correct a replacement to form a new link
in the chain. Neither operation can move entries to another asset or user.

Each supported model save validates and records before/after values atomically.
User-driven code must pass an actor; trusted jobs may omit it to record a system
write. Reasons are required for reversal/correction and adjustment entries. Seed
definitions are recorded by migration history rather than runtime audit events.
Audit records are application-level history, not tamper-proof database security:
raw SQL, `_base_manager`, privileged database operators and Django migration models
can bypass guards. Database constraints independently enforce key numeric,
currency, sign, uniqueness and snapshot-total invariants. Cross-table ownership,
schema validation and exact reversal semantics require the application layer.

### Valuations, FX and snapshots

Assets select a preferred manual or market valuation method. Market assets need a
company reference or provider symbol. Valuations record the actual method, source,
effective timestamp, currency and basis: per-unit price or total owned holding value.
Values are nonnegative; liabilities are classified separately. Correct a valuation
by adding a higher revision for the same asset/time; readers select the highest
revision. Phase 3 exposes owned manual valuation history and selects the latest
effective recorded revision for holdings and asset summaries. Total valuations are
flagged for review when held quantities change. Provider adapters and automatic
pricing remain deferred. Reuse stored market data in those future services;
do not treat split-adjusted chart history as raw valuation prices without reconciling
holdings and corporate actions. Do not count pension statement balances and their
underlying assets together.

FX rates mean **one base-currency unit equals `rate` quote-currency units**. They
are positive, dated and source-specific; corrections append a higher revision.
Same-currency pairs are unnecessary. Source selection, inversion and dated fallback
rules must be defined before implementing valuation aggregation.

Snapshots preserve assets, liabilities and net value in portfolio base currency,
with a unique portfolio/date/revision, calculation version and nonempty JSON input
provenance. Net value must equal assets minus liabilities. Input provenance should
include the exact valuation/rate IDs, quantities, effective dates and missing-data
decisions. Snapshot generation and provenance-completeness validation are deferred
to the calculation service. Existing snapshots must never be rewritten when prices,
FX history or ledger entries are corrected.

## Indexes and migration deployment

Indexes cover owner/archive filters, portfolio/category assets, dated asset ledgers,
latest valuation revisions, dated FX pairs, snapshots and object/portfolio audit trails.
Uniqueness covers category codes, reversals, correction links and valuation/FX/snapshot
revisions. Database checks enforce supported currencies, nonnegative prices/valuations,
positive FX rates/revisions, transaction directions/fees and snapshot totals.

`0001_initial` creates only new portfolio tables and references existing users/stocks.
`0002_default_categories` seeds reference definitions without deleting history on reverse.
`0003` adds transaction-type and valuation-strategy category fields; `0004` configures
existing definitions and adds mutual funds while retaining existing market preferences.
No data is copied from the trading models. Inspect and apply migrations using the
normal deployment process; this change does not migrate the configured database:

```sh
python manage.py migrate --plan
python manage.py migrate portfolio
```

## Verification

Run the isolated offline model/admin tests:

```sh
../.venv/bin/python manage.py test portfolio --settings=portfolio.test_settings --noinput
../.venv/bin/python manage.py makemigrations portfolio --check --dry-run --settings=portfolio.test_settings
```

Test settings use an in-memory database and local email, independent of `DATABASE_URL`.
They run portfolio/users/stocks migrations. Legacy `transactions.0002` contains a
`CharField` without `max_length` that SQLite cannot replay; only that app's migration
chain is disabled in these settings, creating its current tables directly instead.
Existing trading models and migrations are not changed. Run PostgreSQL migration
and concurrent-reversal verification against a disposable PostgreSQL database before
production rollout; the SQLite suite does not test row-lock behaviour.
