from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect 
class UserAccessMixins(PermissionRequiredMixin):

    def dispatch(self, request,*args,**kwargs):
        if (not request.user.is_authenticated):
            return redirect_to_login(
                self.request.get_full_path(),
                self.get_login_url(),
                self.get_redirect_field_name()
            )
        if not self.has_permission():
            return redirect('/') 
        return super(UserAccessMixins,self).dispatch(request,*args,**kwargs)

class CompanyPermissionMiddleWare:
    def __init__(self,get_response):
        self.get_response=get_response
    def __call__(self,request)    :
        staff_company_id=request.user.staff.company.pk
        # inventory_company_id=
        