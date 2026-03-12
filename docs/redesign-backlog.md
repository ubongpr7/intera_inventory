# Redesign Backlog

Last updated: 2026-03-12

This document is the current handoff for the four-service redesign:

- `intera_users`
- `product_service`
- `intera_inventory`
- `pos_backend_service`

It records what is already in place, what is still open, and the recommended order for the next execution passes.

## Working Rule

- Agents should not create or edit migration files.
- The owner will regenerate migrations from the final model code when bootstrapping the system.
- Backlog items below refer to model/code state, runtime behavior, and rollout work, not generated migration artifacts.

## Architecture Snapshot

Current state in the local repos:

- Service boundaries are mostly correct now.
- Runtime cross-service communication is Kafka-first with local projections, not synchronous service-to-service HTTP.
- The system is usable enough for frontend integration now.
- Remaining work is mostly cleanup, ownership decisions, rollout, and runtime hardening, not a fresh redesign.

Service flow summary:

- `intera_users` owns identity and publishes identity events.
- `product_service` owns catalog and publishes catalog product and catalog variant events.
- `intera_inventory` owns stock truth and publishes inventory availability, reservation, and fulfillment events.
- `pos_backend_service` owns POS workflow and publishes POS order and reservation workflow events.
- `product_service`, `intera_inventory`, and `pos_backend_service` all rely on local Kafka-fed projections for shared data they do not own.

## What Has Been Achieved

### 1. Core Kafka wiring is in place

- Identity Kafka flow is live from `intera_users`.
- Catalog Kafka flow is live from `product_service`.
- Inventory availability, reservation, release, and fulfillment Kafka flow is live from `intera_inventory`.
- POS workflow Kafka flow is live from `pos_backend_service`.
- Inventory reacts to POS reservation, release, fulfillment, and cancellation flow through Kafka.
- POS projects reservation and fulfillment results back into local order workflow state.
- Product and POS both consume inventory events into local projection tables.

### 2. Cross-service runtime behavior is projection-based

- Product stock lookups now use local `InventoryVariantProjection`, not direct runtime calls to inventory.
- Inventory stock and order flows now use local catalog projections instead of synchronous catalog fetches.
- Identity data is projected locally into downstream services.
- Runtime code no longer depends on direct inter-service HTTP for the main identity, catalog, inventory, and POS flows.

### 3. Inventory redesign cleanup already completed

- Purchase and sales order lines no longer auto-link through legacy bridge ID generation.
- `StockItem.save()` no longer auto-populates `inventory_item` through bridge logic.
- Stock views now resolve legacy inventory compatibility through `metadata__legacy_inventory_id` instead of `InventoryItem.legacy_bridge_id(...)`.
- Inventory summaries, location summaries, and profile analytics now use normalized `InventoryItem` and `StockBalance` data instead of falling back to legacy `StockItem` rows.
- `StockDomainService.ensure_inventory_item()` now resolves existing normalized items by metadata rather than bridge-ID synthesis.
- New locked balances are zero-initialized instead of being seeded from legacy stock rows.
- Inventory current stock and total stock value now go through the shared summary path instead of bridge-ID balance queries.

### 4. Model and API cleanup already completed

- `InventoryCategory` uniqueness was corrected in model code so tenant scope is the real uniqueness boundary.
- Non-identity services had stale downstream JWT/simplejwt imports removed from URL modules.
- POS now treats `catalog_variant_id` as the canonical variant reference.
- POS `product_variant_id` is now a compatibility mirror, not the primary identifier.
- Generic relation object IDs were updated in code to be UUID-safe instead of integer-only.
- `StockDomainService` no longer reads `StockItem.product_variant` to resolve catalog variants.
- `StockItemViewSet` no longer resolves legacy `StockItem` IDs on retrieve; normalized inventory item IDs are the active runtime path.
- `StockItem.product_variant` has been removed from active model code.
- `StockItem` no longer stores a persisted `inventory_item` bridge; compatibility stock flows resolve normalized inventory items lazily when they need them.
- New normalized inventory items no longer seed `sku_snapshot` from `Inventory.external_system_id`.
- `Inventory.external_system_id` now remains only as a read-only alias/search key; low-stock helper rows no longer treat it as a SKU.
- `product_service` no longer exposes POS configuration API routes; POS is now the active API owner for POS configuration.
- `product_service` no longer carries the dormant `POSConfiguration` model/view/serializer layer.
- `product_service` variant creation no longer requires the legacy `Product.inventory` string to be populated.
- Product bulk creation no longer writes catalog-side inventory references into newly created products.
- `product_service` no longer keeps `Product.inventory` or `BulkTask.inventory` in active model code.
- `product_service` runtime category filtering and analytics now prefer `category_ref` while keeping the legacy category string only as a compatibility fallback.
- POS runtime event payloads now derive compatibility `product_variant_id` from `catalog_variant_id` instead of treating it as a separate active runtime source.
- `pos_backend_service` no longer stores a separate `product_variant_id` field on `POSOrderItem`; that compatibility slot is now payload-derived only.

### 5. Reliability foundation is already implemented in code

- Producer-side outbox code exists across all four services.
- Consumer idempotency persistence code exists across all four services.
- Dead-letter persistence and replay command support exists across all four services.
- Kafka config and environment shape are already documented in `kafka-environment-matrix.md`.

## What Is Still Left

### Priority 1. Finish inventory legacy retirement

Inventory runtime cleanup is mostly complete.

- Keep the compatibility `StockItem` API surface as a thin facade for frontend transition, or retire it later after frontend contracts are frozen.
- Remove or reduce remaining compatibility-only helpers and commands that still target legacy stock records.

### Priority 2. Resolve identifier and ownership cleanup

- Regenerate final schema from the cleaned model code once the owner bootstraps the environment.
- `Inventory.external_system_id` should now be treated as a read-only alias/search key unless the owner chooses to remove it entirely later.
- POS configuration ownership is fully with `pos_backend_service`.
- POS customer ownership should be treated as POS-owned for now:
  - `pos_backend_service` keeps the writable `CustomerViewSet`

### Priority 3. Product service cleanup

- Freeze the initial POS-facing catalog contract for frontend consumption:
  - `/products/`
  - `/variants/`
  - `/pos/products/`
  - `/pos/products/search/`
  - `/pos/products/featured/`
  - `/pos/products/categories/`
  - `/pos/variants/`
  - `/pos/variants/search/`
  - `/pos/variants/barcode/`

### Priority 4. Reliability rollout in real environments

The code exists, but rollout is still pending.

- Generate owner-managed migrations from the current model code in each service.
- Apply those migrations in each environment.
- Enable `KAFKA_USE_OUTBOX` service by service after schema rollout.
- Run inventory projection backfill with `publish_inventory_events`.
- Validate:
  - dead-letter capture
  - replay workflow
  - consumer idempotency behavior
  - event versioning and recovery rules

### Priority 5. Runtime verification and environment hardening

- Fix missing native WeasyPrint dependencies where PDF services are imported at runtime.
- Verify product runtime requirements for Modal-backed bulk creation paths if those flows are still required.
- Run clean startup validation for all four services with the real environment variables in place.
- Run targeted API verification against the endpoints the frontend will consume first.
- Confirm docker/local startup parity for:
  - consumer processes
  - web processes
  - Kafka bootstrap settings
  - JWT key configuration

## Frontend Readiness Notes

Frontend work can start now.

What is ready enough:

- identity-driven authentication and projection flow
- catalog list/detail and variant surfaces
- inventory-backed stock availability surfaces
- POS order and inventory reservation workflow surfaces

What is not fully frozen yet:

- some compatibility endpoint shapes around stock
- POS ownership-related endpoints
- final runtime verification of the frontend-facing service contracts

Practical meaning:

- Next.js integration can begin now.
- Kotlin cross-platform integration can begin now.
- Expect some endpoint and field cleanup while the remaining backlog items are completed.

## Recommended Next Execution Order

1. Run targeted API verification against the frontend-facing contract endpoints.
2. Roll out outbox and replay infrastructure in real environments.
3. Run full runtime verification and lock down the first frontend contracts.

## Concrete Next Tasks

These are the next tasks another agent should pick up immediately.

1. Keep the compatibility `stock-items` endpoint as a frontend transition facade unless and until the frontend contract is frozen.
2. Treat POS customer data as POS-owned unless the owner explicitly moves it elsewhere.
3. Move to rollout work: owner-managed migrations, outbox enablement, backfill, and runtime verification.

## Handoff Notes

- Do not spend more agent time generating migrations.
- Treat current large local changes in `product_service`, `intera_users`, and `pos_backend_service` as in-progress work unless the owner says otherwise.
- When working on frontend-facing code, prefer stabilizing endpoint contracts over adding more compatibility layers.
- When choosing between another compatibility patch and deleting legacy behavior, prefer removing runtime legacy dependence first, then letting the owner regenerate schema later.
