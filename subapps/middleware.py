from django.utils.deprecation import MiddlewareMixin

from subapps.utils.request_context import (
    frontend_origin_from_request,
    reset_frontend_origin_context,
    set_frontend_origin_context,
)


class FrontendOriginMiddleware(MiddlewareMixin):
    """Expose the validated frontend origin to events emitted during an HTTP request."""

    def process_request(self, request):
        request._intera_frontend_origin_token = set_frontend_origin_context(
            frontend_origin_from_request(request)
        )

    def process_response(self, request, response):
        token = getattr(request, "_intera_frontend_origin_token", None)
        if token is not None:
            reset_frontend_origin_context(token)
            request._intera_frontend_origin_token = None
        return response

    def process_exception(self, request, exception):
        token = getattr(request, "_intera_frontend_origin_token", None)
        if token is not None:
            reset_frontend_origin_context(token)
            request._intera_frontend_origin_token = None
        return None
