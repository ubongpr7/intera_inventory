def inventory_creation_logic(self,form):
    if self.request.user.company:
        form.instance.profile_id = getattr(self.request.user.company, "id", self.request.user.company)
    elif self.request.user.profile:
        form.instance.profile_id = getattr(self.request.user.profile, "id", self.request.user.profile)

    
