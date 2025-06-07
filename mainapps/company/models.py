from django.core.validators import RegexValidator
from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from mainapps.content_type_linking_models.models import ProfileMixin, UUIDBaseModel

class Company(ProfileMixin):
    """A Company object represents a company."""

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Companies'
        constraints = [
            models.UniqueConstraint(fields=['name', 'profile'], name='unique_company_name_profile')
        ]

    name = models.CharField(
        max_length=100,
        blank=False,
        verbose_name=_('Company name'),
    )

    description = models.CharField(
        max_length=1000,
        verbose_name=_('Company description'),
        help_text=_('Briefly describe the company'),
        blank=True,
        null=True,
    )

    website = models.URLField(
        blank=True, null=True,
        verbose_name=_('Website'),
        help_text=_('Company website URL (optional)')
    )
    
    short_address = models.CharField(
        max_length=200,
        verbose_name=_('Address'),
        null=True,
        blank=True,
    )
    
    phone = models.CharField(
        max_length=15,
        verbose_name=_('Phone number'),
        blank=True,
        help_text=_('Contact phone number (optional)'),
    )

    email = models.EmailField(
        blank=True,
        null=True,
        verbose_name=_('Email'),
        help_text=_('Contact email address (optional)'),
    )

    link = models.URLField(
        blank=True,
        verbose_name=_('Link/Website'),
        help_text=_('Link to external company information or profile'),
    )

    is_customer = models.BooleanField(
        default=False,
        verbose_name=_('is customer'),
        help_text=_('Do you sell items to this company?'),
    )

    is_supplier = models.BooleanField(
        default=False,
        verbose_name=_('Is supplier'),
        help_text=_('Do you purchase items from this company?'),
    )

    is_manufacturer = models.BooleanField(
        default=False,
        verbose_name=_('is manufacturer'),
        help_text=_('Does this company manufacture parts?'),
    )

    currency = models.CharField(
        max_length=255,  
        blank=True,
        null=True,
    )

    created_by = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name=_('Created By'),
        help_text=_('User  ID of the creator'),
    )

    def __str__(self):
        if self.created_by:
            return f'{self.name} -> {self.created_by}'
        return self.name


class Contact(UUIDBaseModel):
    """A Contact represents a person who works at a particular company."""

    alphanumeric_validator = RegexValidator(
        regex=r'^[a-zA-Z0-9]*$',
        message='Only alphanumeric characters are allowed.',
        code='invalid_alphanumeric'
    )

    company = models.ForeignKey(
        Company, related_name='contacts', on_delete=models.CASCADE, verbose_name='Company'
    )

    name = models.CharField(max_length=100, verbose_name='Name')

    phone = models.CharField(
        max_length=15, blank=True, verbose_name='Phone', 
    )

    email = models.EmailField(blank=True, null=True, verbose_name='Email')

    role = models.CharField(
        max_length=100, blank=True, verbose_name='Role', help_text=_("Contact person role in company"), validators=[alphanumeric_validator]
    )

    class Meta:
        verbose_name_plural = 'Contacts'

    def __str__(self):
        return f"{self.name} - {self.company.name}"


class CompanyAddress(UUIDBaseModel):
    """An address represents a physical location where the company is located."""

    company = models.ForeignKey(
        Company,
        related_name='addresses',
        on_delete=models.CASCADE,
        verbose_name=_('Company'),
        help_text=_('Select company'),
        null=True,
        blank=False,
    )

    title = models.CharField(
        max_length=100,
        verbose_name=_('Address title'),
        help_text=_('Title describing the address entry'),
        blank=False,
    )
    address = models.CharField(
        max_length=300,
        verbose_name=_('Address'),
        blank=False,
    )

    primary = models.BooleanField(
        default=False,
        verbose_name=_('Primary address'),
        help_text=_('Set as primary or main address'),
    )

    shipping_notes = models.CharField(
        max_length=100,
        verbose_name=_('Courier shipping notes'),
        help_text=_('Notes for shipping courier'),
        blank=True,
    )

    internal_shipping_notes = models.CharField(
        max_length=100,
        verbose_name=_('Internal shipping notes'),
        help_text=_('Shipping notes for internal use'),
        blank=True,
    )

    link = models.URLField(
        blank=True,
        null=True,
        verbose_name=_('Link'),
        help_text=_('Link to address information (external)'),
    )

    class Meta:
        verbose_name_plural = 'Addresses'

    def __str__(self):
        return self.title


registerable_models = [Contact, Company]
