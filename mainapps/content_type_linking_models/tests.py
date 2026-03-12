import uuid

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from mainapps.company.models import Company
from mainapps.content_type_linking_models.models import ContentTypeLink, _coerce_generic_object_id


class GenericObjectIdCompatibilityTests(TestCase):
    def test_generic_object_id_coercion_normalizes_uuid_and_integer_values(self):
        self.assertEqual(_coerce_generic_object_id(uuid.UUID("12345678-1234-5678-1234-567812345678")), "12345678-1234-5678-1234-567812345678")
        self.assertEqual(_coerce_generic_object_id(42), "42")
        self.assertEqual(_coerce_generic_object_id(""), "")

    def test_content_type_link_resolves_integer_and_uuid_targets(self):
        content_type_type = ContentType.objects.get_for_model(ContentType)
        company_type = ContentType.objects.get_for_model(Company)
        company = Company.objects.create(
            name="Acme Supplies",
            profile="1",
            profile_id=1,
        )

        link = ContentTypeLink.objects.create(
            content_type_1=content_type_type,
            object_id_1=company_type.id,
            content_type_2=company_type,
            object_id_2=company.id,
        )

        link.refresh_from_db()

        self.assertEqual(link.object_id_1, str(company_type.id))
        self.assertEqual(link.object_id_2, str(company.id))
        self.assertEqual(link.content_object_1, company_type)
        self.assertEqual(link.content_object_2, company)
