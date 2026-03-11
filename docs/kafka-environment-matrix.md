# Kafka Environment Matrix

Last updated: 2026-03-10

This document defines the supported Kafka connection profiles across:

- `intera_users`
- `intera_inventory`
- `product_service`
- `pos_backend_service`

The Kafka client code in each service supports both unsecured and secured brokers through the same `.env` contract.

## Important Rule

Kafka uses `host:port`, not `http://...` or `https://...`.

Use:

```env
KAFKA_BOOTSTRAP_SERVERS=kafka.example.com:9094
```

Do not use:

```env
KAFKA_BOOTSTRAP_SERVERS=https://kafka.example.com:9094
```

## Supported Security Modes

The current setup supports:

- `PLAINTEXT`
- `SASL_PLAINTEXT`
- `SSL`
- `SASL_SSL`

These are handled in the Kafka config loaders in each service:

- [intera_inventory kafka config](/Users/ubongpr7/dev/pr7/inventory/intera_inventory/subapps/kafka/config.py)
- [product_service kafka config](/Users/ubongpr7/dev/pr7/inventory/product_service/subapps/kafka/config.py)
- [intera_users kafka config](/Users/ubongpr7/dev/pr7/inventory/intera_users/subapps/kafka/config.py)
- [pos_backend_service kafka config](/Users/ubongpr7/dev/pr7/inventory/pos_backend_service/subapps/kafka/config.py)

## Shared Environment Variables

All four services now support the same Kafka env keys:

```env
KAFKA_SERVICE_NAME=
KAFKA_BOOTSTRAP_SERVERS=
KAFKA_SECURITY_PROTOCOL=
KAFKA_SASL_MECHANISM=
KAFKA_SASL_USERNAME=
KAFKA_SASL_PASSWORD=
KAFKA_SSL_CA_FILE=
KAFKA_SSL_CERT_FILE=
KAFKA_SSL_KEY_FILE=
KAFKA_SSL_KEY_PASSWORD=
KAFKA_CONSUMER_GROUP=
KAFKA_CONSUMER_TOPICS=
KAFKA_AUTO_OFFSET_RESET=
KAFKA_POLL_INTERVAL=
KAFKA_REQUEST_TIMEOUT_MS=
KAFKA_MESSAGE_TIMEOUT_MS=
KAFKA_SESSION_TIMEOUT_MS=
KAFKA_HEARTBEAT_INTERVAL_MS=
KAFKA_PRODUCER_LINGER_MS=
KAFKA_PRODUCER_ACKS=
KAFKA_USE_OUTBOX=
KAFKA_ENABLE_CONSUMER_IDEMPOTENCY=
KAFKA_ENABLE_DLQ=
KAFKA_COMMIT_FAILED_MESSAGES=
KAFKA_DLQ_SUFFIX=
KAFKA_OUTBOX_BATCH_SIZE=
KAFKA_OUTBOX_POLL_INTERVAL=
KAFKA_OUTBOX_RETRY_DELAY_SECONDS=
```

## Recommended Profiles

### 1. Local developer machine, plain Kafka

Use this when Kafka is running locally without auth or TLS.

```env
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_SECURITY_PROTOCOL=PLAINTEXT
KAFKA_CONSUMER_GROUP=inventory-consumer
KAFKA_CONSUMER_TOPICS=identity.user,identity.company_profile,identity.membership,catalog.product,catalog.variant,pos.order
```

No SASL or SSL variables are needed.

### 2. Docker local development, plain Kafka

Use this when the services are in Docker and Kafka is either:

- another container on the same Docker network
- exposed from the host machine

If Kafka is another container:

```env
KAFKA_BOOTSTRAP_SERVERS=kafka:9092
KAFKA_SECURITY_PROTOCOL=PLAINTEXT
```

If Kafka is running on the host and the containers connect through Docker host networking on macOS:

```env
KAFKA_BOOTSTRAP_SERVERS=host.docker.internal:9092
KAFKA_SECURITY_PROTOCOL=PLAINTEXT
```

### 3. Production broker with SASL only

Use this only if the production broker requires SASL authentication but does not use TLS.

```env
KAFKA_BOOTSTRAP_SERVERS=kafka.example.com:9092
KAFKA_SECURITY_PROTOCOL=SASL_PLAINTEXT
KAFKA_SASL_MECHANISM=PLAIN
KAFKA_SASL_USERNAME=inventory-app
KAFKA_SASL_PASSWORD=replace-me
```

This authenticates, but traffic is not encrypted.

### 4. Production broker with TLS only

Use this when the broker requires encrypted transport but not SASL username/password authentication.

```env
KAFKA_BOOTSTRAP_SERVERS=kafka.example.com:9094
KAFKA_SECURITY_PROTOCOL=SSL
KAFKA_SSL_CA_FILE=/app/certs/ca.pem
```

If mutual TLS is required:

```env
KAFKA_BOOTSTRAP_SERVERS=kafka.example.com:9094
KAFKA_SECURITY_PROTOCOL=SSL
KAFKA_SSL_CA_FILE=/app/certs/ca.pem
KAFKA_SSL_CERT_FILE=/app/certs/client.crt
KAFKA_SSL_KEY_FILE=/app/certs/client.key
KAFKA_SSL_KEY_PASSWORD=
```

### 5. Production broker with SASL over TLS

This is the recommended production setup when using username/password auth and TLS.

```env
KAFKA_BOOTSTRAP_SERVERS=kafka.example.com:9094
KAFKA_SECURITY_PROTOCOL=SASL_SSL
KAFKA_SASL_MECHANISM=PLAIN
KAFKA_SASL_USERNAME=inventory-app
KAFKA_SASL_PASSWORD=replace-me
KAFKA_SSL_CA_FILE=/app/certs/ca.pem
```

If the broker also requires client certs:

```env
KAFKA_BOOTSTRAP_SERVERS=kafka.example.com:9094
KAFKA_SECURITY_PROTOCOL=SASL_SSL
KAFKA_SASL_MECHANISM=PLAIN
KAFKA_SASL_USERNAME=inventory-app
KAFKA_SASL_PASSWORD=replace-me
KAFKA_SSL_CA_FILE=/app/certs/ca.pem
KAFKA_SSL_CERT_FILE=/app/certs/client.crt
KAFKA_SSL_KEY_FILE=/app/certs/client.key
KAFKA_SSL_KEY_PASSWORD=
```

## Per-Service Defaults

Recommended `KAFKA_SERVICE_NAME` values:

- `intera_users`: `identity`
- `intera_inventory`: `inventory`
- `product_service`: `product`
- `pos_backend_service`: `pos`

Recommended default consumer groups:

- `identity-consumer`
- `inventory-consumer`
- `product-consumer`
- `pos-consumer`

Current default consumer topics:

- `intera_users`
  - no consumer topics by default unless you explicitly run a consumer there for another flow
- `intera_inventory`
  - `identity.user,identity.company_profile,identity.membership,catalog.product,catalog.variant,pos.order`
- `product_service`
  - `identity.user,identity.company_profile,identity.membership,inventory.availability,inventory.reservation,inventory.fulfillment`
- `pos_backend_service`
  - `identity.user,identity.company_profile,identity.membership,catalog.product,catalog.variant,inventory.availability,inventory.reservation,inventory.fulfillment`

## Current Live Event Flow

The Kafka paths now implemented are:

1. `intera_users` publishes:
   - `identity.user`
   - `identity.company_profile`
   - `identity.membership`
2. `intera_inventory`, `product_service`, and `pos_backend_service` consume those topics.
3. Each downstream service upserts its local identity projection tables.
4. `product_service` publishes:
   - `catalog.product`
   - `catalog.variant`
5. `intera_inventory` and `pos_backend_service` consume those topics.
6. Each downstream service upserts its local catalog projection tables.
7. `intera_inventory` publishes:
   - `inventory.availability`
   - `inventory.reservation`
   - `inventory.fulfillment`
8. `product_service` and `pos_backend_service` consume those topics.
9. Each downstream service upserts its local inventory availability projections.
10. `pos_backend_service` publishes:
   - `pos.order`
11. `intera_inventory` consumes `pos.order` for reservation, release, fulfillment, and cancellation-driven stock actions.
12. `pos_backend_service` also uses inventory reservation and fulfillment events to update local order workflow state.

## Next Kafka Tasks

The next implementation priority after identity, catalog, inventory, and POS workflow events is:

1. Inventory compatibility cleanup
   - remove remaining legacy stock bridges
   - reduce `StockItem` compatibility reliance further
2. Reliability rollout
   - generate/apply migrations for the reliability tables
   - enable outbox where desired
   - validate DLQ and replay flow

## Reliability Layer

The current codebase now includes:

- DB-backed Kafka outbox models in each service
- a `publish_outbox_events` command in each service
- a `replay_dead_letter_events` command in each service
- consumer-side idempotency persistence keyed by `event_id` and consumer group
- dead-letter persistence plus optional Kafka DLQ publish using `KAFKA_DLQ_SUFFIX`

Recommended initial rollout:

```env
KAFKA_USE_OUTBOX=false
KAFKA_ENABLE_CONSUMER_IDEMPOTENCY=true
KAFKA_ENABLE_DLQ=true
KAFKA_COMMIT_FAILED_MESSAGES=true
KAFKA_DLQ_SUFFIX=.dlq
KAFKA_OUTBOX_BATCH_SIZE=100
KAFKA_OUTBOX_POLL_INTERVAL=2.0
KAFKA_OUTBOX_RETRY_DELAY_SECONDS=30
```

Keep `KAFKA_USE_OUTBOX=false` until the reliability migrations are generated and applied in that environment.

## Operational Notes

- Consumer containers already exist in each service `docker-compose.yml`.
- The UV-based services install Kafka support through `confluent-kafka` plus `librdkafka-dev`.
- POS uses `requirements.txt` and now also includes `confluent-kafka`.
- The current setup is broker-adaptive through env only. No code changes are needed to switch from local plain Kafka to a production secured broker.
