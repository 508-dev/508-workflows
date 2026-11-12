# ERP finance integration

`five08.clients.erpnext_finance.ERPNextFinanceClient` supplies named methods for
the Books Assistant and Books Health app APIs. Business calculations, permission
checks, document submission and accounting state remain in the owning ERP app.

This client does not register Discord commands or agent tools. Before exposing
finance operations through either surface, verify the Discord user's ERP
identity and finance permissions at the backend. Authenticate the underlying
`ERPNextClient` with that verified ERP user's API key and secret. This client
uses Frappe's `token` authentication; OAuth would need a separate Bearer-token
transport. The existing shared ERP service token alone does not establish the
human's authority. The finance client has no actor override or arbitrary method
path input, and credentials must stay in the backend.

The initial ERP contracts require a single-company USD site and explicitly reject
unsupported currency/scope configurations. Books Health also requires
unrestricted read access to its source records, including Deleted Document;
default Accounts Manager permissions may need an explicit grant. Finance results
are private and should be rendered only to authorized users.

## Read operations

- `get_finance_status(company)` returns pending billing, unmatched bank lines,
  overdue invoices, held bills and failed invoice emails visible to the actor.
- `get_supplier_payment_status(supplier, company)` explains eligible/held bills,
  totals and the client invoices blocking payment.
- `get_books_health(company, include_info=False, limit=50, offset=0)` returns
  findings with stable check/severity IDs, currency, pagination and explicit
  partial-check failures. `limit` is 1–200; `offset` is 0–100000.

These use GET. The client validates the app's `api_version: 1` envelope and
retains `ERPNextAPIError.error_code` for deterministic handling of errors such as
`PREVIEW_STALE`, `PREVIEW_EXPIRED` and `REQUEST_ID_REUSED`. Frappe's ordinary HTTP
status/exception behavior is preserved. Business failures within an operation
must also be read from its structured per-project/per-line results.

## Reviewed actions

1. Call `preview_billing_approval`, `preview_bank_approval`, or
   `preview_supplier_preparation`. Preview creation uses POST because it stores
   approval state. Bank previews require the fingerprint of every selected line
   from `books_assistant.bank_review.get_queue`.
2. Render the returned `snapshot`, including exact document IDs, amounts,
   recipients, credits and hold effects. Keep its `preview_id` and expiry.
3. After explicit human confirmation, call `approve_billing`, `approve_bank`, or
   `prepare_supplier_payment` with the preview ID and a unique request ID.
4. On a timeout/unknown response, retry with **the same preview and request ID**.
   The ERP stores the operation result atomically with the accounting writes and
   replays it. Do not automatically mint a new request ID on transport retries.
5. A stale/expired preview needs another review; the backend must not silently
   replace it and execute changed instructions. A different operation needs a new
   request ID. Results include `operation_id` for tracing.

All write methods use fixed endpoint paths and POST. Empty selections fail
locally and at the ERP boundary; they never expand to all records. Supplier
preparation needs an explicit invoice list or `all_eligible=True`. It applies
reviewed credits and returns an **unsaved** Payment Entry; it does not submit a
payment or transfer money through a bank.

Previews belong to the ERP actor/action and expire in ten minutes. Permission
checks run again on execution and result replay. The ERP also checks current
document state under locks. Billing results distinguish submission, holds and
email queue state; queued mail is not a delivery receipt. Multi-item operations
can succeed partially, so inspect every item before reporting success.

## Rollout and validation

Deploy the Books Assistant API/schema migration and updated ERP UI assets
together, plus the Books Health API, before enabling these client calls. Old
direct bank approval and supplier preparation signatures intentionally no longer
execute without preview/request IDs. Unknown legacy billing outcomes migrate to
`review_required` for System Manager inspection; they do not become payable.

The app repositories own their API documentation and Bench tests:

- [Books Assistant](https://github.com/508-dev/books_assistant)
- [Books Health](https://github.com/508-dev/books_health)

The integration client tests run with:

```bash
uv run --locked pytest tests/unit/test_erpnext_client.py tests/unit/test_erpnext_finance_client.py
```

Standalone app tests exercise boundary/state-machine behavior with isolated
Frappe fakes. The app Bench suites, schema migration and concurrent transaction
tests must additionally run against a disposable real ERP site before deploying
financial execution. Transaction tests require an explicit test-site setting
because their separate connections commit temporary fixtures.
