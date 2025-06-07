import logging
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
import os
from io import BytesIO
from django.core.mail import EmailMessage

logger = logging.getLogger(__name__)

def send_purchase_order_email(po, pdf_file):
    try:
        if not po.contact or not po.contact.email:
            logger.error(f"No contact or email for Purchase Order #{po.reference}")
            return False

        contact_email = po.contact.email
        subject = f"Purchase Order #{po.reference} from {po.profile.name}"
        from_email = settings.DEFAULT_FROM_EMAIL
        to = [contact_email]

        # Render email content
        try:
            html_content = render_to_string("emails/purchase_order_email.html", {
                "purchase_order": po,
                "company_name": po.profile.name,
                "contact_name": po.contact.name or "Supplier",
                "line_items": po.line_items.all()
            })
        except Exception as e:
            logger.exception("Failed to render email template.")
            return False

        email = EmailMultiAlternatives(subject, "", from_email, to)
        email.attach_alternative(html_content, "text/html")

        # Attach PDF — check if it's a file-like object or a file path
        if isinstance(pdf_file, BytesIO):
            pdf_file.seek(0)  # Ensure it's at the beginning
            email.attach(f"PurchaseOrder_{po.reference}.pdf", pdf_file.read(), 'application/pdf')
        elif isinstance(pdf_file, (str, bytes, os.PathLike)) and os.path.exists(pdf_file):
            with open(pdf_file, 'rb') as f:
                email.attach(f"PurchaseOrder_{po.reference}.pdf", f.read(), 'application/pdf')
        else:
            logger.error("Invalid PDF file type passed.")
            return False

        email.send()
        logger.info(f"Email sent to {contact_email} for Purchase Order #{po.reference}")
        return True

    except Exception as e:
        logger.exception(f"Unexpected error sending email for PO #{po.reference}: {e}")
        return False


def send_return_order_email(return_order, po_pdf, return_pdf):
    subject = f"Return Request for Order {return_order.purchase_order.reference}"
    
    context = {
        'supplier': return_order.purchase_order.supplier,
        'return_order': return_order,
        'contact': return_order.contact
    }
    
    html_content = render_to_string('emails/return_order.html', context)
    
    email = EmailMessage(
        subject,
        html_content,
        settings.DEFAULT_FROM_EMAIL,
        [return_order.purchase_order.supplier.email, return_order.contact.email],
    )
    email.content_subtype = "html"
    
    # Attach PDFs
    email.attach(
        f'Return_{return_order.reference}.pdf',
        return_pdf.getvalue(),
        'application/pdf'
    )
    
    email.attach(
        f'Original_PO_{return_order.purchase_order.reference}.pdf',
        po_pdf.getvalue(),
        'application/pdf'
    )
    
    email.send()