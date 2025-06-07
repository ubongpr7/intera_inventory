# middleware.py
from django.utils import timezone
from pytz import timezone as pytz_timezone

class TimezoneMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                user_tz = pytz_timezone(request.user.userprofile.timezone)
                timezone.activate(user_tz)
            except Exception:
                timezone.deactivate()
        return self.get_response(request)