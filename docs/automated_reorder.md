# Automated Reorder

Automated reorder is structural-location scoped and approval-gated.

1. Set `InventoryItem.automate_reorder=true` and configure `reorder_point` and `reorder_quantity` for the default policy.
2. Use `PATCH /inventory_api/items/{inventory_item_id}/reorder-policy/` with a `structural_location_id` to override those values for one structural location. Set `automate_reorder_override` to `true`, `false`, or `null` to inherit the item policy.
3. The `evaluate_automated_reorders` worker checks available stock across the selected structural location's descendants.
4. When stock is at or below the effective reorder point, it creates one pending, system-curated purchase order for the effective reorder quantity. The PO line records the target structural location.
5. The worker resolves the latest supplier used for that item in the workspace, falling back to the inventory item's default supplier. It never sends or issues the order automatically.
6. An open idempotent decision prevents duplicate purchase orders until the existing order is completed or cancelled.

Run one evaluation locally with:

```sh
uv run python manage.py evaluate_automated_reorders --once --profile-id 4
```

The compose runtimes run the shared operational automation worker every five hours by default. It evaluates reorder policies, expiry windows, purchase-order lifecycle exceptions, stock-integrity exceptions, and POS availability reconciliation. Override the interval with `AUTOMATED_REORDER_INTERVAL_SECONDS`.
## POS availability self-healing

The shared `evaluate_automated_reorders` worker runs every five hours by default.
In addition to reorder and operational-alert checks, it republishes the current
availability snapshot for every active inventory item in the workspace. This
repairs POS projections when an inventory event was missed during a broker,
consumer, or deployment interruption; users do not need to run a repair command.

The replay is profile-scoped and idempotent on the POS consumer side. Inventory
mutations still publish immediately, so the five-hour replay is a safety net rather
than the normal update path. The inventory consumer and POS availability consumer
must remain running, and production should use the durable Kafka outbox for event
delivery so transient broker failures are retried without losing the event.
