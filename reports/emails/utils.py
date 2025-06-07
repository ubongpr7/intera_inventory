# utils/pdf.py
from django.template.loader import render_to_string
from weasyprint import HTML,CSS
from io import BytesIO
from django.conf import settings

def generate_purchase_order_pdf(po):
    tax = sum(line_item.tax_amount for line_item in po.line_items.all())
    discount = sum(line_item.discount for line_item in po.line_items.all())
    
    context = {
        'po': po,
        'tax': tax,
        'company_profile': po.profile,
        'discount': discount,
        'line_items': po.line_items.all(),
        'static_path': settings.STATIC_ROOT  
    }
    
    html_string = render_to_string('pdf/purchase_order.html', context)
    
    # Create PDF with appropriate sizing
    pdf_file = BytesIO()
    HTML(string=html_string, base_url=settings.STATIC_ROOT).write_pdf(
        pdf_file,
        stylesheets=[CSS(string='@page { size: A4 landscape; margin: 1cm; }')]
    )
    pdf_file.seek(0)
    return pdf_file



# utils/pdf.py
def generate_return_order_pdf(return_order):
    context = {
        'return_order': return_order,
        'company_profile': return_order.profile,
        'line_items': return_order.line_items.select_related('original_line_item'),
        'static_path': settings.STATIC_ROOT
    }
    
    html_string = render_to_string('pdf/return_order.html', context)
    
    pdf_file = BytesIO()
    HTML(string=html_string).write_pdf(
        pdf_file,
        stylesheets=[CSS(string='@page { size: A4; margin: 1cm; }')]
    )
    pdf_file.seek(0)
    return pdf_file