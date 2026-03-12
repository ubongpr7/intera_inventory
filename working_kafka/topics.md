docker compose -f docker-compose.yml -f docker-compose.server.yml exec -T kafka bash -lc '
for t in \
  identity.user \
  identity.company_profile \
  identity.membership \
  catalog.product \
  catalog.variant \
  pos.order \
  inventory.availability \
  inventory.reservation \
  inventory.fulfillment
do
  kafka-topics --bootstrap-server localhost:9092 \
    --command-config /etc/kafka/secrets/admin.properties \
    --create --if-not-exists \
    --replication-factor 1 --partitions 1 \
    --topic "$t"
done
'
