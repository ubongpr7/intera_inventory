# Cross-Service Model Redesign

Backlog tracker: [redesign-backlog.md](/Users/ubongpr7/dev/pr7/inventory/intera_inventory/docs/redesign-backlog.md)

Kafka environment matrix: [kafka-environment-matrix.md](/Users/ubongpr7/dev/pr7/inventory/intera_inventory/docs/kafka-environment-matrix.md)

## Scope

This redesign covers:

- `intera_users` as the identity authority only
- `product_service` as catalog ownership
- `intera_inventory` as inventory, procurement, stock, and stock ledger ownership
- `pos_backend_service` as POS runtime ownership

Views are out of scope. This document focuses on data ownership, relationships, and the migration target for Kafka-based service integration.

## Core Principles

1. One concept has one write owner.
2. `profile_id` is the internal tenant key across all services.
3. `company_code` is external only.
4. JWT claims are request-time authorization context.
5. Kafka projections are local relational context.
6. Internal relations use immutable IDs, not business codes like SKU or barcode.
7. Costs, stock quantities, batches, and serials belong to inventory, not catalog or POS.
8. POS stores transactional snapshots for receipts, but not the source of truth for catalog or stock.

## Identity Contract

`intera_users` remains unchanged and owns:

- `User`
- `CompanyProfile`
- `CompanyMembership`
- permissions

Each downstream service should project the minimum identity tables below.

### `identity_company_profile`

- `profile_id` bigint primary key
- `company_code` varchar unique
- `display_name` varchar
- `owner_user_id` bigint null
- `is_active` bool
- `synced_at` datetime

### `identity_user`

- `user_id` bigint primary key
- `email` varchar unique
- `full_name` varchar null
- `is_active` bool
- `synced_at` datetime

### `identity_membership`

- `profile_id` bigint
- `user_id` bigint
- `role` varchar
- `permissions_json` jsonb
- `is_active` bool
- unique (`profile_id`, `user_id`)

### Shared base fields for all domain tables

Every service-owned table should use:

- `id`
- `profile_id`
- `created_at`
- `updated_at`
- `created_by_user_id` null
- `updated_by_user_id` null

Optional display snapshots:

- `created_by_name`
- `updated_by_name`

Do not use:

- free-form `profile` strings
- free-form `user` strings
- custom tenant headers

## Service Ownership

### `product_service` owns

- product templates
- sellable variants
- product categories
- attribute definitions and variant attribute assignments
- sales pricing and pricing rules
- product media and merchandising metadata

### `intera_inventory` owns

- stockable item policies
- warehouses and locations
- procurement
- receipts
- lots and serials
- stock balances
- stock reservations
- stock movements and valuation
- supplier and customer business partners

### `pos_backend_service` owns

- POS configuration
- terminals and sessions
- POS orders
- payments
- receipts
- cash drawer and end-of-day reconciliation

## Target Cross-Service Boundaries

### Product to Inventory

Current issue:

- `Product.inventory` is a raw string
- `StockItem.product_variant` is a barcode string

Target:

- product service owns `product_variant.id`
- inventory stores a durable foreign reference `product_variant_id`
- inventory may also keep a local product projection for querying

Inventory must not join to product by:

- barcode
- SKU
- product name

### Inventory to POS

POS needs:

- variant display data
- current sell price
- stock availability summary

POS should not own:

- lots
- serials
- stock ledger
- procurement cost

POS order lines should store:

- source identifiers
- pricing snapshots
- tax snapshots
- item name snapshots

## Target Product Service Model

### 1. `product_category`

- `id` uuid
- `profile_id` bigint
- `parent_id` uuid null
- `name`
- `slug`
- `description`
- `is_active`
- unique (`profile_id`, `parent_id`, `name`)

This restores relational categories and replaces the current category `CharField`.

### 2. `product_template`

- `id` uuid
- `profile_id` bigint
- `name`
- `slug`
- `description`
- `short_description`
- `category_id` uuid null
- `brand`
- `status` enum: `draft`, `active`, `archived`, `discontinued`
- `product_kind` enum: `stocked`, `service`, `bundle`, `non_stock`
- `default_uom_code`
- `track_stock` bool
- `allow_backorder` bool
- `tax_class_code` null
- `pos_group` null
- `is_featured`
- `launch_date`
- `discontinue_date`

Remove from template ownership:

- `cost_price`
- inventory IDs
- batch data

### 3. `product_variant`

- `id` uuid
- `profile_id` bigint
- `product_template_id` uuid
- `variant_number`
- `display_name`
- `sku`
- `barcode`
- `status` enum
- `is_default`
- `is_sellable`
- `is_purchasable`
- `weight`
- `dimensions_json`
- `sales_price_override` null
- unique (`profile_id`, `sku`)
- unique (`profile_id`, `barcode`) where barcode is not null
- unique (`product_template_id`, `variant_number`)

Variant SKU is catalog identity only. It is not a stock-row identifier.

### 4. `attribute_definition`

- `id` uuid
- `profile_id` bigint
- `name`
- `code`
- `input_type`
- `is_variant_axis`
- `is_required`
- `sort_order`

### 5. `attribute_option`

- `id` uuid
- `attribute_definition_id`
- `value`
- `display_value`
- `sort_order`
- `price_modifier` null

### 6. `variant_attribute_value`

- `variant_id`
- `attribute_definition_id`
- `attribute_option_id` null
- `custom_value` null
- unique (`variant_id`, `attribute_definition_id`)

### 7. `price_list`

- `id` uuid
- `profile_id` bigint
- `name`
- `currency_code`
- `channel` enum: `default`, `pos`, `wholesale`, `online`
- `is_active`
- `starts_at`
- `ends_at`

### 8. `price_list_entry`

- `id` uuid
- `price_list_id`
- `product_template_id` null
- `product_variant_id` null
- `price`
- `min_quantity`
- `max_quantity` null
- `priority`

Exactly one of `product_template_id` or `product_variant_id` must be set.

### Remove or move out of product service

- `Product.inventory`
- template-level stock quantities
- batch and lot data
- duplicated `POSConfiguration`
- cost history as source of truth

## Target Inventory Service Model

Inventory becomes the source of truth for stockable items, procurement, balances, lots, reservations, and the stock ledger.

### 1. `business_partner`

Rename or refactor current `company.Company` into a clearer trading-party model.

- `id` uuid
- `profile_id` bigint
- `partner_type` enum: `supplier`, `customer`, `both`
- `name`
- `external_code`
- `email`
- `phone`
- `status`

This remains different from identity `CompanyProfile`.

### 2. `warehouse`

- `id` uuid
- `profile_id` bigint
- `code`
- `name`
- `is_active`

### 3. `stock_location`

- `id` uuid
- `profile_id` bigint
- `warehouse_id`
- `parent_id` null
- `code`
- `name`
- `location_type`
- `is_active`
- unique (`profile_id`, `warehouse_id`, `code`)

### 4. `inventory_category`

- `id` uuid
- `profile_id` bigint
- `parent_id` uuid null
- `name`
- `slug`
- `description`
- `default_location_id` null
- `is_active`
- `is_structural`
- unique (`profile_id`, `parent_id`, `name`)

Remove the current global `name` uniqueness.

### 5. `inventory_item`

This replaces the overloaded current `Inventory` model.

- `id` uuid
- `profile_id` bigint
- `product_variant_id` uuid null
- `product_template_id` uuid null
- `name_snapshot`
- `sku_snapshot`
- `barcode_snapshot`
- `inventory_category_id` null
- `inventory_type`
- `default_uom_code`
- `stock_uom_code`
- `track_lot` bool
- `track_serial` bool
- `track_expiry` bool
- `allow_negative_stock` bool
- `reorder_point`
- `reorder_quantity`
- `minimum_stock_level`
- `safety_stock_level`
- `default_supplier_id` null
- `status`
- unique (`profile_id`, `product_variant_id`) where `product_variant_id` is not null

Use this model for stock policy and procurement settings. It is the inventory-side representation of what is stocked.

### 6. `purchase_order`

- `id` uuid
- `profile_id` bigint
- `supplier_id`
- `reference`
- `status`
- `workflow_state`
- `currency_code`
- `ordered_at`
- `expected_delivery_at`
- `received_at` null
- `approved_by_user_id` null
- `notes`

### 7. `purchase_order_line`

- `id` uuid
- `purchase_order_id`
- `inventory_item_id`
- `product_variant_id` uuid null
- `description_snapshot`
- `ordered_quantity`
- `received_quantity`
- `unit_cost`
- `discount_rate`
- `tax_rate`
- `expected_expiry_date` null
- `batch_number_hint` null

The line points to `inventory_item`, not `stock_item`.

### 8. `goods_receipt`

- `id` uuid
- `profile_id` bigint
- `purchase_order_id` null
- `supplier_id`
- `reference`
- `received_at`
- `received_by_user_id`
- `notes`

### 9. `goods_receipt_line`

- `id` uuid
- `goods_receipt_id`
- `purchase_order_line_id` null
- `inventory_item_id`
- `stock_location_id`
- `received_quantity`
- `unit_cost`
- `lot_number` null
- `manufactured_date` null
- `expiry_date` null

Receipt lines create lots and stock movements.

### 10. `stock_lot`

- `id` uuid
- `profile_id` bigint
- `inventory_item_id`
- `supplier_id` null
- `purchase_order_line_id` null
- `goods_receipt_line_id` null
- `lot_number` null
- `manufactured_date` null
- `expiry_date` null
- `unit_cost`
- `currency_code`
- `received_quantity`
- `remaining_quantity`
- `status`

Every lot is immutable except for remaining quantity and status.

### 11. `stock_serial`

- `id` uuid
- `profile_id` bigint
- `inventory_item_id`
- `stock_lot_id` null
- `serial_number`
- `status`
- unique (`profile_id`, `serial_number`)

Only use this table for serial-tracked items. Do not overload `stock_item.serial`.

### 12. `stock_balance`

Materialized balance for fast queries.

- `id` uuid
- `profile_id` bigint
- `inventory_item_id`
- `stock_location_id`
- `stock_lot_id` null
- `quantity_on_hand`
- `quantity_reserved`
- `quantity_available`
- unique (`inventory_item_id`, `stock_location_id`, `stock_lot_id`)

### 13. `stock_movement`

Immutable stock ledger.

- `id` uuid
- `profile_id` bigint
- `inventory_item_id`
- `stock_lot_id` null
- `stock_serial_id` null
- `from_location_id` null
- `to_location_id` null
- `movement_type` enum: `receipt`, `issue`, `transfer`, `adjustment`, `reservation`, `release`, `return_in`, `return_out`
- `quantity`
- `unit_cost` null
- `reference_type`
- `reference_id`
- `actor_user_id` null
- `occurred_at`
- `notes`

This replaces mixed transaction tracking across `InventoryTransaction`, `StockMovement`, and implicit quantity edits.

### 14. `stock_reservation`

- `id` uuid
- `profile_id` bigint
- `inventory_item_id`
- `stock_lot_id` null
- `stock_location_id`
- `external_order_type`
- `external_order_id`
- `external_order_line_id`
- `reserved_quantity`
- `fulfilled_quantity`
- `status`
- `expires_at` null

POS and future sales services reserve stock through this model.

### 15. `stock_adjustment`

- `id` uuid
- `profile_id` bigint
- `inventory_item_id`
- `stock_location_id`
- `stock_lot_id` null
- `adjustment_type`
- `quantity_delta`
- `reason_code`
- `actor_user_id`
- `approved_by_user_id` null
- `approved_at` null

### Remove or replace in inventory

Replace:

- `Inventory` with `inventory_item`
- `StockItem` with `stock_lot`, `stock_serial`, `stock_balance`
- `InventoryBatch` with `stock_lot`
- `InventoryTransaction` with `stock_movement`
- `PurchaseOrderLineItem.stock_item` with `purchase_order_line.inventory_item_id`
- `StockItem.purchase_order` and `StockItem.sales_order`

## Target POS Service Model

POS should focus on selling workflow and accounting snapshots, not catalog or stock truth.

### 1. `pos_configuration`

Keep this here only.

- `id` uuid
- `profile_id` bigint
- `name`
- `currency_code`
- `tax_inclusive`
- `default_tax_rate`
- `allow_negative_stock`
- `require_customer`
- `auto_print_receipt`
- `allow_split_payment`
- `max_discount_percent`

Delete the duplicated product-service `POSConfiguration`.

### 2. `pos_terminal`

- `id` uuid
- `profile_id` bigint
- `pos_configuration_id`
- `code`
- `name`
- `location_name`
- `is_active`

### 3. `pos_session`

- `id` uuid
- `profile_id` bigint
- `pos_terminal_id`
- `opened_by_user_id`
- `closed_by_user_id` null
- `status`
- `opened_at`
- `closed_at` null
- `opening_balance`
- `closing_balance` null
- `expected_balance`

### 4. `pos_customer`

Keep only if POS-specific customers are required. Otherwise move customer ownership elsewhere and use projection.

### 5. `pos_order`

- `id` uuid
- `profile_id` bigint
- `session_id`
- `customer_id` null
- `order_number`
- `status`
- `subtotal`
- `tax_amount`
- `discount_amount`
- `tip_amount`
- `total_amount`
- `notes`
- `completed_at` null

### 6. `pos_order_line`

- `id` uuid
- `profile_id` bigint
- `pos_order_id`
- `product_variant_id` uuid
- `inventory_item_id` uuid null
- `sku_snapshot`
- `barcode_snapshot`
- `product_name_snapshot`
- `variant_name_snapshot`
- `quantity`
- `unit_price`
- `tax_rate`
- `discount_percent`
- `discount_amount`
- `tax_amount`
- `line_total`
- `customizations_json`
- `special_instructions`

This line stores snapshots for audit and receipt rendering.

### 7. `pos_payment`

- `id` uuid
- `profile_id` bigint
- `pos_order_id`
- `payment_method`
- `amount`
- `reference`
- `paid_at`
- `status`

### 8. `pos_receipt`

- `id` uuid
- `profile_id` bigint
- `pos_order_id`
- `receipt_number`
- `printed_at` null
- `payload_json`

### 9. `cash_movement`

- `id` uuid
- `profile_id` bigint
- `session_id`
- `movement_type`
- `amount`
- `reason`
- `actor_user_id`
- `occurred_at`

## Required Local Projections

### Inventory service needs product projections

#### `catalog_product_variant`

- `product_variant_id` uuid primary key
- `profile_id` bigint
- `product_template_id` uuid
- `sku`
- `barcode`
- `product_name`
- `variant_name`
- `default_uom_code`
- `track_stock`
- `is_active`
- `updated_at`

### POS service needs product projections

#### `catalog_variant_projection`

- `product_variant_id` uuid primary key
- `profile_id` bigint
- `sku`
- `barcode`
- `product_name`
- `variant_name`
- `display_name`
- `default_price`
- `tax_class_code`
- `pos_group`
- `is_active`

### POS service may need stock availability projections

#### `inventory_availability_projection`

- `inventory_item_id` uuid primary key
- `profile_id` bigint
- `product_variant_id` uuid
- `available_quantity`
- `reserved_quantity`
- `status`
- `updated_at`

## Kafka Event Model

### Identity topics

- `identity.user.upserted`
- `identity.user.deleted`
- `identity.company_profile.upserted`
- `identity.company_profile.deleted`
- `identity.membership.upserted`
- `identity.membership.deleted`

### Product topics

- `catalog.product.upserted`
- `catalog.product.deleted`
- `catalog.variant.upserted`
- `catalog.variant.deleted`

### Inventory topics

- `inventory.availability.upserted`
- `inventory.reservation.upserted`
- `inventory.reservation.released`
- `inventory.fulfillment.completed`

### POS topics

- `pos.order.created`
- `pos.order.cancelled`
- `pos.order.paid`
- `pos.inventory.reservation.requested`
- `pos.inventory.reservation.released`
- `pos.inventory.reservation.confirmed`
- `pos.inventory.fulfillment.confirmed`

## Invariants

1. `profile_id` is present on every business row.
2. Every unique code is tenant-scoped unless explicitly global.
3. Every movement of stock creates a `stock_movement`.
4. Every balance is derived from movements and updated transactionally.
5. Lots and serials are inventory concerns only.
6. Costs come from receipts and valuation, not product templates.
7. POS order lines keep immutable commercial snapshots.
8. Services never query each other synchronously for identity or catalog truth during write operations.

## Current to Target Mapping

### Product service

- `Product` -> `product_template`
- `ProductVariant` -> `product_variant`
- category `CharField` -> `product_category` relation
- `PricingRule` -> `price_list` and `price_list_entry`
- remove product-level inventory references
- remove product-level batch and stock truth

### Inventory service

- `InventoryCategory` -> keep but fix uniqueness and tenant key
- `Inventory` -> `inventory_item`
- `InventoryBatch` -> `stock_lot`
- `StockItem` -> split into `stock_lot`, `stock_serial`, `stock_balance`
- `InventoryTransaction` + `StockMovement` -> `stock_movement`
- `PurchaseOrderLineItem` -> `purchase_order_line`

### POS service

- keep `POSSession`, `POSOrder`, `POSPayment` conceptually
- refactor them to reference identity projections and catalog/inventory IDs
- remove duplicated config ownership from product service

## Migration Order

### Phase 1: Identity normalization

- add local identity projection tables to product, inventory, and POS
- replace free-form profile and user string usage in new schema with `profile_id` and `user_id`

### Phase 2: Product normalization

- restore relational categories
- remove inventory references from product
- make variant IDs the only cross-service product identity
- move pricing into explicit price-list tables

### Phase 3: Inventory core rebuild

- introduce `inventory_item`
- introduce `stock_lot`, `stock_serial`, `stock_balance`, `stock_movement`
- rebuild procurement around item and receipt lines
- stop linking orders to `StockItem`

### Phase 4: POS normalization

- keep POS config only in POS
- change order lines to snapshot variant and inventory references
- consume product and stock availability projections through Kafka

### Phase 5: Decommission legacy structures

- remove HTTP user-service clients
- remove custom tenant-header assumptions
- retire old stock, batch, and duplicated POS config tables

## Immediate First Implementation Slice

The first schema slice to implement should be:

1. identity projections in all downstream services
2. product category repair and stable `product_variant` ownership
3. `inventory_item` introduction in inventory
4. purchase order lines referencing `inventory_item`

That slice fixes the core identity and catalog-to-stock boundary that the rest of the redesign depends on.
