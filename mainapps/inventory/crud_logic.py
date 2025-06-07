
from .models import Inventory

def inventory_creation_logic(self,form):
    if self.request.user.company:
        form.instance.profile= self.request.user.company
    elif self.request.user.profile:
        form.instance.profile= self.request.user.profile

    