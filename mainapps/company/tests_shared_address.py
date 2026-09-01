from types import SimpleNamespace

from django.test import SimpleTestCase

from mainapps.company.management.commands.migrate_inventory_addresses_to_shared import Command


class SharedAddressMigrationPayloadTests(SimpleTestCase):
    def test_company_address_payload_uses_stable_source_reference(self):
        source = SimpleNamespace(
            pk="address-1",
            address="1 Example Street",
            title="Headquarters",
            company=SimpleNamespace(profile_id=17),
        )

        payload, error = Command._payload("company_address", source)

        self.assertIsNone(error)
        self.assertEqual(payload["profile_id"], "17")
        self.assertEqual(payload["address_line_1"], "1 Example Street")
        self.assertEqual(payload["external_reference"], "inventory:company_address:address-1")

    def test_missing_address_is_reconciled_as_invalid(self):
        payload, error = Command._payload(
            "stock_location",
            SimpleNamespace(pk="location-1", profile_id=17, physical_address="", name="Warehouse"),
        )

        self.assertIsNone(payload)
        self.assertEqual(error, "Missing address text.")
