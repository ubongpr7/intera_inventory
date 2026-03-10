# Redesign Backlog

Last updated: 2026-03-10

This document tracks the remaining cross-service redesign work so it is not lost between implementation passes.

## Current Priority

The next thing to prioritize is identity and tenant field normalization across the three downstream services.

Why this comes next:

- the transport layer is already removed
- local projection models already exist
- the current model base classes still store tenant and actor data as free-form strings
- if Kafka consumers are built before this normalization, they will target a schema that is still in transition

That means the next schema pass should replace legacy `profile`, `created_by`, `modified_by`, `approved_by`, `official`, `stocktaker`, and similar string fields with explicit internal IDs and projection relationships where appropriate.

## Priority Order

1. Identity and tenant field normalization
2. Inventory domain redesign completion
3. Kafka projection wiring
4. API and ownership cleanup
5. Legacy structure removal
6. Full runtime verification

## Backlog

### 1. Identity and tenant field normalization

- Replace legacy `ProfileMixin` and `UUIDBaseModel` string identity fields in product, POS, and old inventory/shared models.
- Move domain models toward `profile_id`, `created_by_user_id`, `updated_by_user_id`, and optional display snapshots instead of free-form strings.
- Replace remaining actor strings such as:
  - `approved_by`
  - `official`
  - `stocktaker`
  - `user_id`
  - `created_by`
  - `modified_by`
- Update affected write paths, serializers, filters, and permission assumptions to use the normalized fields.

### 2. Inventory domain redesign completion

- Implement `stock_lot`.
- Implement `stock_serial`.
- Implement `stock_balance`.
- Implement `stock_movement`.
- Implement reservation, receipt, allocation, and return flows on top of the new inventory structures.
- Stop straddling both old and new inventory relations in procurement and stock flows.
- Fully move purchase-order and receipt logic to `inventory_item`-based ownership.

### 3. Cross-service reference normalization

- Replace remaining string-based cross-service references with local projection IDs or local FKs.
- Remove catalog-side inventory references such as `BulkTask.inventory` and `Product.inventory`.
- Retire `StockItem.product_variant` string usage in favor of normalized inventory/catalog links.
- Retire `POSOrderItem.product_variant_id` once `catalog_variant_id` and `inventory_item_id` are the only identifiers needed.

### 4. Kafka projection wiring

- Implement producers and consumers for:
  - identity projections
  - catalog projections
  - inventory availability projections
  - reference-data projections
- Add idempotency, version checks, delete handling, replay/backfill support, and failure recovery.
- Add outbox-style reliable event publishing where needed.
- Backfill projection tables so the system has usable local data after deployment.

### 5. Ownership cleanup

- Remove duplicated `POSConfiguration` ownership and choose a single write owner.
- Confirm whether POS `Customer` stays POS-owned or becomes a projected/shared concept.
- Finish catalog category normalization so string category fields become compatibility-only or are removed.
- Decide the long-term role of `Inventory.external_system_id` as alias-only vs. active legacy key.

### 6. Legacy bridge and compatibility removal

- Remove `InventoryItem.legacy_bridge_id(...)` usage after migration completes.
- Remove `legacy_stock_items` bridging references.
- Remove old dual-write or compatibility-only save hooks.
- Remove compatibility-only comments and helper paths in bulk product commands and stock bridging code.

### 7. Generic relation cleanup

- Fix integer-based generic relation infrastructure in inventory/shared models so it is compatible with UUID-heavy domains.
- Review content-type based attachment/linking structures and normalize object ID types where needed.

### 8. API cleanup

- Simplify [product urls](/Users/ubongpr7/dev/pr7/inventory/product_service/mainapps/product/urls.py) so the large manual `as_view(...)` alias layer is reduced to cleaner router-native actions.
- Decide whether analytics endpoints should remain aggregate viewsets or move to model-backed read models.
- Remove stale downstream JWT minting imports and dead auth-route remnants from non-identity services.

### 9. Data integrity fixes

- Fix `InventoryCategory` uniqueness so tenant-scoped uniqueness is not undermined by a global `unique=True`.
- Review other constraints and indexes that still assume legacy identifiers or tenant strings.
- Revisit SKU, barcode, and external ID invariants after the new inventory structures are in place.

### 10. Runtime verification

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
- Projection lookup helpers exist already, but they are still waiting for Kafka population.
