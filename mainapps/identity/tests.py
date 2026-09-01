from django.test import TestCase

from mainapps.identity.models import IdentityCompanyProfile
from subapps.services.pdf.pdf_service import PDFService
from subapps.kafka.consumers.identity import handle_identity_company_profile_event


class IdentityCompanyProfileProjectionTests(TestCase):
    def test_profile_event_projects_the_optional_logo_url(self):
        handle_identity_company_profile_event(
            {
                "event_name": "identity.company_profile.upserted",
                "payload": {
                    "profile_id": 77,
                    "company_code": "QA77",
                    "display_name": "QA Workspace",
                    "logo_url": "https://assets.example.test/company-logo.png",
                    "headquarters_address": {"street": "First Avenue", "city": "Lagos", "country": "Nigeria"},
                    "headquarters_address_id": "052ccf7b-2276-490b-a4e9-2a7ae02f54c1",
                    "owner_user_id": 1,
                    "is_active": True,
                },
            }
        )

        profile = IdentityCompanyProfile.objects.get(profile_id=77)

        self.assertEqual(profile.display_name, "QA Workspace")
        self.assertEqual(profile.logo_url, "https://assets.example.test/company-logo.png")
        self.assertEqual(profile.headquarters_address["city"], "Lagos")
        self.assertEqual(str(profile.headquarters_address_id), "052ccf7b-2276-490b-a4e9-2a7ae02f54c1")

    def test_workspace_address_formats_the_projected_headquarters(self):
        IdentityCompanyProfile.objects.create(
            profile_id=78,
            company_code="QA78",
            display_name="QA Workspace",
            headquarters_address={
                "street_number": 10,
                "street": "First Avenue",
                "apt_number": 1,
                "city": "Ebute Ikorodu",
                "subregion": "Ikorodu",
                "region": "Lagos",
                "country": "Nigeria",
                "postal_code": "104102",
            },
        )

        self.assertEqual(
            PDFService._workspace_address(78),
            "10 First Avenue\nApt 1\nEbute Ikorodu, Ikorodu, Lagos\nNigeria\n104102",
        )
