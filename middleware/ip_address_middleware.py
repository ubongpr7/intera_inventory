from django.core.exceptions import PermissionDenied
from django.conf import settings


class IPBlackListMiddleware:

    def __init__(self, get_response):
        self.get_response=get_response

    def __call__(self,request):
        if hasattr(settings,'BANNED_IPS') and settings.BANNED_IPS is not None:
            if request.META['REMOTE_ADDR'] in settings.BANNED_IPS:
                raise PermissionDenied() 
            
        response= self.get_response(request)

        return response

# class IPBlackListMiddleware:

#     def __init__(self, get_response):
#         self.get_response=get_response

#     def __call__(self,request):
#         if hasattr(settings,'BANNED_IPS') and settings.BANNED_IPS is not None:
#             if request.META['REMOTE_ADDR'] in settings.BANNED_IPS:
#                 raise PermissionDenied() 
            
#         response= self.get_response(request)

#         return response