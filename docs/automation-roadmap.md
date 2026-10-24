# Operational Automation Roadmap

The system should automate decisions and reminders, but it must not silently
issue financial or stock-changing transactions. The default rule is:

- workers may detect, draft, reserve, and notify;
- an authorised person approves actions that commit money or stock;
- every worker operation is workspace-scoped and idempotent.

## Implemented foundation

### Structural-location reorder

`evaluate_automated_reorders` evaluates active inventory placements. It uses
the effective item/location policy, sums available stock for the structural
location and descendants, and creates one pending, approval-gated purchase
order when stock is at or below the reorder point.

An open decision is unique per inventory item and structural location. While
its purchase order is pending, approved, issued, or being received, later
worker runs skip it. The initial creation sends one workspace notification;
polling does not send duplicate reminders. A cancelled or otherwise terminal
order resolves the decision so a future low-stock evaluation may create a new
one.

## Required next automations

1. **Expiry and near-expiry monitoring**
   - scan tracked lots by workspace and structural location;
   - notify at configurable windows, such as 30, 14, and 7 days;
   - create one alert per lot/window and resolve it when the lot is sold,
     disposed, returned, or the window changes;
   - never alter stock automatically without an explicit policy and approval.

2. **Purchase-order lifecycle monitoring**
   - remind approvers about stale pending approvals;
   - flag overdue expected delivery dates;
   - notify on partial receipts and unresolved discrepancies;
   - stop reminders after approval, cancellation, completion, or acknowledgement.

3. **Receiving and stock integrity checks**
   - detect received quantities above ordered quantities;
   - detect missing batch, manufacture, or expiry data when the item policy
     requires it;
   - detect negative or unexpectedly divergent balances;
   - create an exception for review rather than silently correcting records.

4. **Notification and outbox reliability**
   - monitor pending, retrying, and dead-letter events;
   - apply bounded exponential backoff and a visible terminal failure state;
   - make each notification idempotent by event key and workspace;
   - expose worker health and last successful run to administrators.

5. **Sales and payment reconciliation**
   - reconcile local POS submissions with the server order/payment record;
   - flag payments that remain pending or fail repeatedly;
   - prevent duplicate financial posting using the local sale id and server
     idempotency key;
   - notify without changing a payment state automatically.

6. **Approval escalation**
   - route pending purchase orders, returns, valuation captures, and journal
     entries to authorised workspace users;
   - escalate after configurable age thresholds;
   - respect workspace permissions, not only membership or owner status.

## After the operational release

- manufacturing work-order material availability and shortage alerts;
- quality inspection and nonconformance follow-up;
- automatic demand forecasting and suggested reorder quantities;
- margin and price anomaly alerts;
- scheduled profitability and valuation snapshots;
- audit retention and export jobs.

## Deferred platform automations

These are intentionally documented for a later phase and are not part of the
current inventory-reorder release:

- POS payment and offline-sync reconciliation, including persistent handling
  for pending, retrying, and repeatedly failed submissions;
- notification and Kafka outbox health monitoring, dead-letter review, and
  operator-facing retry controls;
- approval escalation for purchase orders, customer returns, valuation
  captures, and accounting journals;
- manufacturing material-shortage, production-progress, quality, and
  nonconformance workflows;
- demand forecasting, suggested reorder quantities, and supplier-performance
  recommendations;
- margin, pricing, profitability, and valuation anomaly alerts;
- scheduled reporting, audit-retention, and export jobs.

The current worker must not silently perform any of these deferred actions.
They require their own contracts, permissions, idempotency rules, and focused
tests before being enabled.

## Worker contract

Each automation should have a management command or worker entry point,
workspace/profile filtering, a durable decision or outbox record, a stable
idempotency key, structured logs, retry limits, and focused tests for duplicate
runs, two workspaces, permission boundaries, cancellation, and recovery after
failure. Redis may be used for locks or short-lived coordination, but the
database remains the source of truth for decisions and financial state.
