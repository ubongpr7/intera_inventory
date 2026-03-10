from datetime import timezone
from django.contrib.contenttypes.fields import GenericForeignKey,GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models
from mptt.models import MPTTModel, TreeForeignKey
from django.utils.translation import gettext_lazy as _

import uuid


def _coerce_identity_id(value):
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _sync_identity_fields(instance, *, canonical_field, legacy_field):
    if not hasattr(instance, canonical_field) or not hasattr(instance, legacy_field):
        return

    canonical_value = _coerce_identity_id(getattr(instance, canonical_field, None))
    legacy_value = getattr(instance, legacy_field, None)

    if canonical_value is None:
        canonical_value = _coerce_identity_id(legacy_value)
        if canonical_value is not None:
            setattr(instance, canonical_field, canonical_value)

    if canonical_value is not None and legacy_value in (None, ""):
        setattr(instance, legacy_field, str(canonical_value))


class UUIDBaseModel(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
        help_text=_("Unique identifier for the model instance.")
    )
    created_by = models.CharField(
        max_length=400, 
        null=True,
        blank=True,
        verbose_name=_('Created By'),
        help_text=_('User who created this model instance.')
    )
    created_by_user_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    modified_by = models.CharField(
        max_length=400,
        null=True,
        blank=True,
        verbose_name=_('Modified By'),
        help_text=_('User who last modified this model instance.')
    )
    updated_by_user_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('Created At'),
        help_text=_('Timestamp when this model instance was created.')
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_('Updated At'),
        help_text=_('Timestamp when this model instance was last updated.')
    )

    class Meta:
        abstract=True

    def save(self, *args, **kwargs):
        _sync_identity_fields(self, canonical_field='created_by_user_id', legacy_field='created_by')
        _sync_identity_fields(self, canonical_field='updated_by_user_id', legacy_field='modified_by')
        super().save(*args, **kwargs)


class ProfileMixin(UUIDBaseModel):

    profile=models.CharField(
        max_length=400,
        null=False,
        blank=True,
        verbose_name=_('Profile'),
        help_text=_('Profile of the user or entity associated with this model.'),
        editable=False
    )
    profile_id = models.BigIntegerField(blank=True, null=True, db_index=True, editable=False)
    created_by = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name=_('Created By'),
        help_text=_('User  ID of the creator'),
        editable=False
    )


    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        _sync_identity_fields(self, canonical_field='profile_id', legacy_field='profile')
        super().save(*args, **kwargs)


class TenantStampedUUIDModel(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
        help_text=_("Unique identifier for the model instance."),
    )
    profile_id = models.BigIntegerField(
        db_index=True,
        verbose_name=_("Profile ID"),
        help_text=_("Identity service CompanyProfile ID."),
    )
    created_by_user_id = models.BigIntegerField(
        blank=True,
        null=True,
        db_index=True,
        verbose_name=_("Created By User ID"),
    )
    updated_by_user_id = models.BigIntegerField(
        blank=True,
        null=True,
        db_index=True,
        verbose_name=_("Updated By User ID"),
    )
    created_by_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("Created By Name"),
    )
    updated_by_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("Updated By Name"),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("Created At"),
        help_text=_("Timestamp when this model instance was created."),
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_("Updated At"),
        help_text=_("Timestamp when this model instance was last updated."),
    )

    class Meta:
        abstract = True

class GenericModel(models.Model):
    """
    Abstract base class for models with GenericForeignKey.

    This abstract base class provides fields to link to any other model using a GenericForeignKey.
    It includes fields for content type, object ID, and a field to specify the target model.

    Fields:
    - content_type: ForeignKey to ContentType model representing the type of the linked object.
    - object_id: PositiveIntegerField representing the ID of the linked object.
    - content_object: GenericForeignKey to represent the linked object.
    - created_at: DateTime field representing the timestamp when the like was created.
    - updated_at: DateTime field representing the timestamp when the comment was last updated.

    Methods:
    - __str__: Returns a string representation of the model instance.
    """

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        verbose_name='Content Type',
        help_text='The content type of the linked object.'
    )
    object_id = models.PositiveIntegerField(
        verbose_name='Object ID',
        help_text='The ID of the linked object.'
    )
    content_object = GenericForeignKey('content_type', 'object_id')
    
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True

    def __str__(self):
        """
        String representation of the model instance.
        
        Returns:
            str: A string representing the linked object using content type and object ID.
        """
        return f"Instance for {self.content_type.model} ({self.object_id})"

def attachment_upload_path(instance, filename):
    return f'attachments/{instance.attachment.content_type.model}/{instance.attachment.object_id}/{instance.attachment.id}/{instance.id}/{filename}'


class ContentTypeLink(models.Model):
    """
    Represents a link between two ContentType instances.

    Attributes:
        - content_type_1 (ForeignKey): The first content type in the link.
        - object_id_1 (PositiveIntegerField): The ID of the related object for the first content type.
        - content_object_1 (GenericForeignKey): Generic relation to the related object for the first content type.
        - content_type_2 (ForeignKey): The second content type in the link.
        - object_id_2 (PositiveIntegerField): The ID of the related object for the second content type.
        - content_object_2 (GenericForeignKey): Generic relation to the related object for the second content type.
    """
    content_type_1 = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name='link_type_1')
    object_id_1 = models.PositiveIntegerField()
    content_object_1 = GenericForeignKey('content_type_1', 'object_id_1')

    content_type_2 = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name='link_type_2')
    object_id_2 = models.PositiveIntegerField()
    content_object_2 = GenericForeignKey('content_type_2', 'object_id_2')

    def __str__(self):
        return f"{self.content_type_1} - {self.object_id_1} to {self.content_type_2} - {self.object_id_2}"

    class Meta:
        verbose_name = "Content Type Link"
        verbose_name_plural = "Content Type Links"
