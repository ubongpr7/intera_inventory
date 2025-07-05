from django.shortcuts import render


def render_templete(request,partial:str,full:str,context=None):
    if request.htmx:
        return render(request,partial,context)
    return render(request,full,context)
    
