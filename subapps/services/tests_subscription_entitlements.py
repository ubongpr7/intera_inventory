import io
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import PermissionDenied

from .subscription_entitlements import enforce_subscription_limit


class SubscriptionEntitlementClientTests(SimpleTestCase):
    @override_settings()
    @patch.dict(
        'os.environ',
        {
            'SUBSCRIPTION_ENFORCEMENT_MODE': 'enforce',
            'SUBSCRIPTION_SERVICE_URL': 'http://subscriptions:8550',
            'SUBSCRIPTION_SERVICE_KEY': 'service-key',
            'SUBSCRIPTION_APPLICATION_SLUG': 'intera-ims',
        },
        clear=False,
    )
    @patch('subapps.services.subscription_entitlements.cache')
    @patch('subapps.services.subscription_entitlements.request.urlopen')
    def test_entitlement_request_is_application_aware_and_cached(self, mocked_urlopen, mocked_cache):
        response = io.BytesIO(b'{"allowed": true, "limit": 10, "remaining": 9}')
        response.__enter__ = lambda self: self
        response.__exit__ = lambda self, *args: None
        mocked_urlopen.return_value = response
        mocked_cache.get.return_value = None

        decision = enforce_subscription_limit(
            profile_id='profile-4',
            application='intera-ims',
            feature='structural-locations',
            usage=1,
            requested=1,
        )

        self.assertTrue(decision['allowed'])
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.headers['X-intera-service-key'], 'service-key')
        self.assertIn(b'"application": "intera-ims"', request.data)
        self.assertIn(b'"profile_id": "profile-4"', request.data)
        self.assertEqual(mocked_cache.set.call_count, 2)
        self.assertEqual(mocked_cache.set.call_args.args[2], 30)

    @patch.dict('os.environ', {'SUBSCRIPTION_ENFORCEMENT_MODE': 'enforce'}, clear=False)
    @patch('subapps.services.subscription_entitlements.request.urlopen', side_effect=OSError('offline'))
    def test_service_failure_fails_closed(self, _mocked_urlopen):
        with self.assertRaises(PermissionDenied) as raised:
            enforce_subscription_limit(
                profile_id='profile-4',
                application='intera-ims',
                feature='products',
                usage=0,
            )

        self.assertEqual(raised.exception.detail['code'], 'subscription_service_unavailable')
