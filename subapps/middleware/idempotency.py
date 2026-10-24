"""Global replay guard for mutation requests carrying Idempotency-Key."""

import json

from django.db import transaction
from django.http import JsonResponse

from mainapps.identity.idempotency import (
    claim_request,
    complete_request,
    fail_request,
    raw_request_hash,
)
from subapps.utils.request_context import get_request_profile_id


class IdempotencyMiddleware:
    """Protect keyed mutations that do not have endpoint-specific replay logic."""

    _excluded_paths = (
        "/customer-returns/",
        "/process_payment/",
    )
    _required_key_paths = (
        "/purchase-orders/",
        "/return-orders/dispatch/",
        "/production-orders/",
        "/adjust_stock/",
        "/transfer_stock/",
        "/valuation-snapshots/capture/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return self.get_response(request)
        if any(path in request.path for path in self._excluded_paths):
            return self.get_response(request)

        key = str(request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key") or "").strip()
        if not key:
            if any(path in request.path for path in self._required_key_paths):
                return JsonResponse(
                    {"detail": "Idempotency-Key is required for this mutation."},
                    status=400,
                )
            return self.get_response(request)
        profile_id = get_request_profile_id(request, required=False, as_str=False)
        if profile_id in (None, ""):
            return self.get_response(request)

        operation = f"{request.method}:{request.path}"
        claim = claim_request(
            profile_id=profile_id,
            operation=operation,
            key=key,
            request_hash_value=raw_request_hash(request),
        )
        if claim.replay is not None:
            return claim.replay

        try:
            # Keep the replay record completion in the same transaction as the
            # view's database mutation. A crash cannot commit the business
            # write while leaving a permanently processing replay record.
            with transaction.atomic():
                response = self.get_response(request)
                if 200 <= response.status_code < 300:
                    if hasattr(response, "data"):
                        complete_request(claim.record, response)
                    else:
                        body = json.loads(response.content.decode("utf-8"))
                        response.data = body
                        complete_request(claim.record, response)
                else:
                    fail_request(claim.record)
                return response
        except Exception:
            fail_request(claim.record)
            raise
