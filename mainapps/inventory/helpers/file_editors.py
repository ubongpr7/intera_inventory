

import os
import uuid
from django.utils import timezone

from django.utils.deconstruct import deconstructible

def generate_unique_filename(instance, filename):
    # Split the filename and extension
    root, ext = os.path.splitext(filename)

    # Generate a unique filename using a combination of timestamp and random string
    unique_filename = f"{timezone.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex}{ext}"

    # Return the unique filename
    return unique_filename




@deconstructible
class UniqueFilename:
    def __init__(self, path):
        self.path = os.path.join(path, "%s%s")

    def __call__(self, instance, filename):
        extension = filename.split('.')[-1]
        return self.path % (instance.pk, f".{extension}")