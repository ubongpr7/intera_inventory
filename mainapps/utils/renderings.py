from django.shortcuts import render


def render_templete(request,partial:str,full:str,context=None):
    if request.htmx:
        print('htmx')
        return render(request,partial,context)
    print('not htmx')
    return render(request,full,context)
    
