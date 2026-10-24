import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from subapps.permissions.constants import CombinedPermissions
from subapps.utils import request_context


class PermissionEvaluationTests(SimpleTestCase):
    def _request(self, token_permissions=None):
        return SimpleNamespace(
            META={"HTTP_AUTHORIZATION": "Bearer compact-token"},
            headers={},
            user=SimpleNamespace(id=7),
            auth=SimpleNamespace(
                payload={
                    "user_id": "7",
                    "profile_id": "4",
                    "platform": "intera_ims",
                    "permissions": token_permissions or [],
                    "wildcards": ["inventory.*"],
                }
            ),
        )

    @patch.dict(
        "os.environ",
        {
            "USER_SERVICE_URL": "http://users",
            "PERMISSION_EVALUATION_SERVICE_KEY": "service-key",
            "KAFKA_SERVICE_NAME": "inventory",
        },
        clear=False,
    )
    @patch("subapps.utils.request_context.cache")
    @patch("subapps.utils.request_context.urlrequest.urlopen")
    def test_compact_token_claim_is_not_an_authorization_bypass(self, urlopen, cache):
        cache.get.return_value = None
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = json.dumps(
            {"grants": {"read_stock_item": False}, "expires_in": 60}
        ).encode()

        allowed = request_context.has_request_permission(
            self._request(["read_stock_item"]), CombinedPermissions.READ_INVENTORY_ITEM
        )

        self.assertFalse(allowed)
        self.assertTrue(urlopen.called)

    @patch.dict(
        "os.environ",
        {
            "USER_SERVICE_URL": "http://users",
            "PERMISSION_EVALUATION_SERVICE_KEY": "service-key",
            "KAFKA_SERVICE_NAME": "inventory",
        },
        clear=False,
    )
    @patch("subapps.utils.request_context.cache")
    @patch("subapps.utils.request_context.urlrequest.urlopen")
    def test_users_evaluator_grant_is_used_for_active_profile(self, urlopen, cache):
        cache.get.return_value = None
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = json.dumps(
            {"grants": {"read_stock_item": True}, "expires_in": 60}
        ).encode()

        allowed = request_context.has_request_permission(
            self._request(), CombinedPermissions.READ_INVENTORY_ITEM
        )

        self.assertTrue(allowed)
        request = urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(body["profile_id"], "4")
        self.assertEqual(body["permissions"], ["read_stock_item"])
        self.assertEqual(request.headers["X-intera-service-key"], "service-key")
