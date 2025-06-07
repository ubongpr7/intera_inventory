from .models import *
from django.forms import ModelForm, inlineformset_factory

class AttachmentForm(ModelForm):
    class Meta:
        model=Attachment
        fields='__all__'
        

class FileForm(ModelForm):
    class Meta:
        fields=('file',)
        model=File 


FileFormSet = inlineformset_factory(Attachment,File, form=FileForm, extra=1)
 
