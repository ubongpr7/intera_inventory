from django.test import TestCase
from django.test.client import RequestFactory
from django.http import JsonResponse
from rest_framework.response import Response
from unittest.mock import patch

from .idempotency import claim_request, complete_request
from .models import IdempotencyRecord
from subapps.middleware.idempotency import IdempotencyMiddleware


class IdempotencyLedgerTests(TestCase):
    def test_completed_request_replays_same_response(self):
        first = claim_request(
            profile_id=101,
            operation="POST:/inventory_api/items/",
            key="create-1",
            request_hash_value="a" * 64,
        )
        complete_request(first.record, Response({"id": "item-1"}, status=201))

        replay = claim_request(
            profile_id=101,
            operation="POST:/inventory_api/items/",
            key="create-1",
            request_hash_value="a" * 64,
        )

        self.assertIsNotNone(replay.replay)
        self.assertEqual(replay.replay.status_code, 201)
        self.assertEqual(replay.replay.data, {"id": "item-1"})
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_reusing_key_with_different_payload_is_rejected(self):
        claim_request(
            profile_id=101,
            operation="POST:/inventory_api/items/",
            key="create-1",
            request_hash_value="a" * 64,
        )

        conflict = claim_request(
            profile_id=101,
            operation="POST:/inventory_api/items/",
            key="create-1",
            request_hash_value="b" * 64,
        )

        self.assertEqual(conflict.replay.status_code, 409)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_required_mutation_key_is_rejected_before_handler_runs(self):
        calls = []

        def handler(request):
            calls.append(True)
            return JsonResponse({"ok": True}, status=201)

        request = RequestFactory().post(
            "/inventory_api/items/1/adjust_stock/",
            data="{}",
            content_type="application/json",
        )
        response = IdempotencyMiddleware(handler)(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(calls, [])

    def test_required_mutation_key_replays_after_handler_success(self):
        calls = []

        def handler(request):
            calls.append(True)
            return JsonResponse({"id": "movement-1"}, status=201)

        middleware = IdempotencyMiddleware(handler)
        factory = RequestFactory()
        with patch("subapps.middleware.idempotency.get_request_profile_id", return_value=101):
            first = middleware(
                factory.post(
                    "/inventory_api/items/1/adjust_stock/",
                    data='{"quantity": 2}',
                    content_type="application/json",
                    HTTP_IDEMPOTENCY_KEY="adjust-1",
                )
            )
            replay = middleware(
                factory.post(
                    "/inventory_api/items/1/adjust_stock/",
                    data='{"quantity": 2}',
                    content_type="application/json",
                    HTTP_IDEMPOTENCY_KEY="adjust-1",
                )
            )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(len(calls), 1)
