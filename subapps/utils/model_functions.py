
from django.utils.crypto import get_random_string

from django.utils.text import slugify
    # def save(self, *args, **kwargs):

    #     if self.generate_sku and not self.sku:

    #         self.sku = self.generate_sku_logic()

    #     if self.generate_upc and not self.upc:

    #         self.upc = self.generate_upc_logic()

    #     super(ProductInventory, self).save(*args, **kwargs)



    # def generate_sku_logic(self):

    #     brand_initials = self.brand.name[:3].upper() if self.brand else "UNK"

    #     random_string = get_random_string(length=5)

    #     return f"{brand_initials}-{random_string}"



    # def generate_upc_logic(self):

    #     brand_initials = self.brand.name[:3].upper() if self.brand else "000"

    #     random_string = get_random_string(length=9, allowed_chars='0123456789')

    #     return f"{brand_initials}{random_string}"

