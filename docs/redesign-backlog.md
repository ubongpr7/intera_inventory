# Redesign Backlog

Last updated: 2026-03-10

This document tracks the remaining cross-service redesign work so it is not lost between implementation passes.

## Current Priority

The next thing to prioritize is the remaining inventory-domain cleanup, followed by rollout and verification of the new Kafka reliability layer.

Why this comes next:

- identity projection Kafka is already live
- catalog projection Kafka is now also live
- inventory availability, reservation, and fulfillment events are now live from `intera_inventory`
- `product_service` and `pos_backend_service` now consume inventory projections locally
- POS workflow events are now live and inventory reacts to reservation, release, fulfillment, and cancellation requests
- POS now projects inventory reservation and fulfillment results back into local order workflow state
- outbox, consumer idempotency, dead-letter persistence, and dead-letter replay infrastructure now exist across all four services

The immediate next implementation slice should be:

- remove more of the remaining inventory compatibility paths
- reduce `StockItem` and legacy bridge dependence further
- then roll out the reliability layer in real environments with generated migrations and env enablement

## Priority Order

1. Inventory domain redesign completion
2. API and ownership cleanup
3. Data integrity and generic relation cleanup
4. Legacy structure removal
5. Reliability rollout and verification
6. Full runtime verification

## Backlog

### 1. Inventory availability Kafka wiring

- `intera_inventory` now publishes:
  - `inventory.availability.upserted`
  - `inventory.reservation.upserted`
  - `inventory.reservation.released`
  - `inventory.fulfillment.completed`
- `product_service` now consumes those topics into `InventoryVariantProjection`.
- `pos_backend_service` now consumes those topics into `InventoryAvailabilityProjection`.
- Backfill still needs to be run in each environment with `publish_inventory_events`.

### 2. POS workflow Kafka wiring

- `pos_backend_service` now publishes:
  - `pos.order.created`
  - `pos.order.cancelled`
  - `pos.order.paid`
  - `pos.inventory.reservation.requested`
  - `pos.inventory.reservation.released`
  - `pos.inventory.reservation.confirmed`
  - `pos.inventory.fulfillment.confirmed`
- `intera_inventory` now consumes `pos.order` and executes:
  - reservation requests
  - reservation releases
  - fulfillment confirmations
  - cancellation-driven release
- `pos_backend_service` now consumes inventory reservation and fulfillment events to update local order item workflow state.

### 3. Event reliability and delivery guarantees

- Producer-side outbox infrastructure now exists across all four services.
- Consumer idempotency persistence now exists across all four services.
- Dead-letter persistence and replay commands now exist across all four services.
- Remaining work:
  - generate and apply migrations for the new reliability tables
  - decide service-by-service rollout of `KAFKA_USE_OUTBOX`
  - validate DLQ topics and replay workflow in real environments
  - formalize event versioning and recovery rules

### 4. Inventory domain redesign completion

- Finish removing legacy outbound assumptions still tied to `StockItem`.
- Continue moving read/write flows fully onto:
  - `stock_lot`
  - `stock_serial`
  - `stock_balance`
  - `stock_movement`
  - `stock_reservation`
- Expose more of the new inventory model directly where needed instead of relying on compatibility summaries.

### 5. Cross-service reference normalization

- Replace remaining string-based cross-service references with local projection IDs or local FKs.
- Remove catalog-side inventory references such as `BulkTask.inventory` and `Product.inventory`.
- Retire `StockItem.product_variant` string usage in favor of normalized inventory/catalog links.
- Retire `POSOrderItem.product_variant_id` once `catalog_variant_id` and `inventory_item_id` are the only identifiers needed.

### 6. Ownership cleanup

- Remove duplicated `POSConfiguration` ownership and choose a single write owner.
- Confirm whether POS `Customer` stays POS-owned or becomes a projected/shared concept.
- Finish catalog category normalization so string category fields become compatibility-only or are removed.
- Decide the long-term role of `Inventory.external_system_id` as alias-only vs. active legacy key.

### 7. Legacy bridge and compatibility removal

- Remove `InventoryItem.legacy_bridge_id(...)` usage after migration completes.
- Remove `legacy_stock_items` bridging references.
- Remove old dual-write or compatibility-only save hooks.
- Remove compatibility-only comments and helper paths in bulk product commands and stock bridging code.

### 8. Generic relation cleanup

- Fix integer-based generic relation infrastructure in inventory/shared models so it is compatible with UUID-heavy domains.
- Review content-type based attachment/linking structures and normalize object ID types where needed.

### 9. API cleanup

- Simplify [product urls](/Users/ubongpr7/dev/pr7/inventory/product_service/mainapps/product/urls.py) so the large manual `as_view(...)` alias layer is reduced to cleaner router-native actions.
- Decide whether analytics endpoints should remain aggregate viewsets or move to model-backed read models.
- Remove stale downstream JWT minting imports and dead auth-route remnants from non-identity services.

### 10. Data integrity fixes

- Fix `InventoryCategory` uniqueness so tenant-scoped uniqueness is not undermined by a global `unique=True`.
- Review other constraints and indexes that still assume legacy identifiers or tenant strings.
- Revisit SKU, barcode, and external ID invariants after the new inventory structures are in place.

### 11. Runtime verification

- Install missing runtime dependencies:
  - WeasyPrint libs in inventory
  - `modal` in product
  - `tinymce` in POS
- Provide the full env needed for startup, including JWT public-key settings.
- Run:
  - `manage.py check`
  - migration validation
  - targeted API tests
  - schema/data backfill verification

## Notes

- HTTP-based interservice calls have already been removed from runtime code.
- App-level `api/` folders have already been flattened into app-root `views.py`, `serializers.py`, `urls.py`, and related files.
- Identity Kafka infrastructure is already in place across all four services.
- Catalog Kafka infrastructure is now also live from `product_service` into inventory and POS.
- The Kafka environment matrix is documented in [kafka-environment-matrix.md](/Users/ubongpr7/dev/pr7/inventory/intera_inventory/docs/kafka-environment-matrix.md).
