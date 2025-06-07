import logging
from django.core.mail import EmailMultiAlternatives, EmailMessage
from django.template.loader import render_to_string
from django.conf import settings
import os
from io import BytesIO

logger = logging.getLogger(__name__)

class EmailService:
    """Enhanced email service matching your existing implementation"""
    
    @classmethod
    def send_purchase_order_email(cls, purchase_order, pdf_file):
        """
        Send purchase order email to supplier contact
        Args:
            purchase_order: PurchaseOrder instance
            pdf_file: BytesIO object or file path containing PDF
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        try:
            # Check if contact exists and has email
            if not purchase_order.contact or not purchase_order.contact.email:
                logger.error(f"No contact or email for Purchase Order #{purchase_order.reference}")
                return False

            contact_email = purchase_order.contact.email
            subject = f"Purchase Order #{purchase_order.reference} from {purchase_order.profile.name}"
            from_email = settings.DEFAULT_FROM_EMAIL
            to = [contact_email]

            # Add CC recipients if needed
            cc_emails = []
            if purchase_order.supplier and hasattr(purchase_order.supplier, 'email') and purchase_order.supplier.email:
                cc_emails.append(purchase_order.supplier.email)
            
            # Render email content using your template
            try:
                html_content = render_to_string("emails/purchase_order_email.html", {
                    "purchase_order": purchase_order,
                    "company_name": purchase_order.profile.name,
                    "contact_name": purchase_order.contact.name or "Supplier",
                    "line_items": purchase_order.line_items.all()
                })
            except Exception as e:
                logger.exception("Failed to render email template.")
                return False

            # Create email with HTML content
            email = EmailMultiAlternatives(subject, "", from_email, to, cc=cc_emails)
            email.attach_alternative(html_content, "text/html")

            # Attach PDF — check if it's a file-like object or a file path
            try:
                if isinstance(pdf_file, BytesIO):
                    pdf_file.seek(0)  # Ensure it's at the beginning
                    email.attach(f"PurchaseOrder_{purchase_order.reference}.pdf", pdf_file.read(), 'application/pdf')
                elif isinstance(pdf_file, (str, bytes, os.PathLike)) and os.path.exists(pdf_file):
                    with open(pdf_file, 'rb') as f:
                        email.attach(f"PurchaseOrder_{purchase_order.reference}.pdf", f.read(), 'application/pdf')
                else:
                    logger.error("Invalid PDF file type passed.")
                    return False
            except Exception as e:
                logger.error(f"Failed to attach PDF to email: {str(e)}")
                return False

            # Send email
            email.send()
            logger.info(f"Email sent to {contact_email} for Purchase Order #{purchase_order.reference}")
            return True

        except Exception as e:
            logger.exception(f"Unexpected error sending email for PO #{purchase_order.reference}: {e}")
            return False

    @classmethod
    def send_return_order_email(cls, return_order, po_pdf, return_pdf):
        """
        Send return order email with both original PO and return order PDFs
        Args:
            return_order: ReturnOrder instance
            po_pdf: BytesIO object containing original purchase order PDF
            return_pdf: BytesIO object containing return order PDF
        """
        try:
            purchase_order = return_order.purchase_order
            
            # Validate email recipients
            recipients = []
            if purchase_order.supplier and hasattr(purchase_order.supplier, 'email') and purchase_order.supplier.email:
                recipients.append(purchase_order.supplier.email)
            
            if return_order.contact and hasattr(return_order.contact, 'email') and return_order.contact.email:
                recipients.append(return_order.contact.email)
            
            if not recipients:
                logger.error(f"No email recipients for return order {return_order.reference}")
                return False

            subject = f"Return Request for Order {purchase_order.reference}"
            
            # Prepare template context
            context = {
                'supplier': purchase_order.supplier,
                'return_order': return_order,
                'purchase_order': purchase_order,
                'contact': return_order.contact,
                'company_name': return_order.profile.name if return_order.profile else 'Company'
            }
            
            # Render email template
            html_content = render_to_string('emails/return_order_email.html', context)
            
            # Create email
            email = EmailMessage(
                subject,
                html_content,
                settings.DEFAULT_FROM_EMAIL,
                recipients,
            )
            email.content_subtype = "html"
            
            # Attach PDFs
            try:
                if return_pdf:
                    if isinstance(return_pdf, BytesIO):
                        return_pdf.seek(0)
                        email.attach(
                            f'Return_{return_order.reference}.pdf',
                            return_pdf.read(),
                            'application/pdf'
                        )
                    else:
                        email.attach(
                            f'Return_{return_order.reference}.pdf',
                            return_pdf.getvalue(),
                            'application/pdf'
                        )
                
                if po_pdf:
                    if isinstance(po_pdf, BytesIO):
                        po_pdf.seek(0)
                        email.attach(
                            f'Original_PO_{purchase_order.reference}.pdf',
                            po_pdf.read(),
                            'application/pdf'
                        )
                    else:
                        email.attach(
                            f'Original_PO_{purchase_order.reference}.pdf',
                            po_pdf.getvalue(),
                            'application/pdf'
                        )
            except Exception as e:
                logger.error(f"Failed to attach PDFs to return order email: {str(e)}")
                return False
            
            email.send()
            logger.info(f"Return order email sent for {return_order.reference}")
            return True
            
        except Exception as e:
            logger.exception(f"Unexpected error sending return order email: {e}")
            return False

    @classmethod
    def send_purchase_order_status_notification(cls, purchase_order, status_change, additional_context=None):
        """
        Send status change notifications for purchase orders
        Args:
            purchase_order: PurchaseOrder instance
            status_change: dict with 'from_status' and 'to_status'
            additional_context: dict with additional template context
        """
        try:
            if not purchase_order.contact or not purchase_order.contact.email:
                logger.warning(f"No contact email for PO status notification: {purchase_order.reference}")
                return False

            subject = f"Purchase Order {purchase_order.reference} Status Update - {status_change['to_status'].title()}"
            
            context = {
                'purchase_order': purchase_order,
                'status_change': status_change,
                'company_name': purchase_order.profile.name,
                'contact_name': purchase_order.contact.name or "Supplier",
            }
            
            if additional_context:
                context.update(additional_context)
            
            html_content = render_to_string('emails/purchase_order_status_notification.html', context)
            
            email = EmailMultiAlternatives(
                subject,
                "",
                settings.DEFAULT_FROM_EMAIL,
                [purchase_order.contact.email]
            )
            email.attach_alternative(html_content, "text/html")
            email.send()
            
            logger.info(f"Status notification sent for PO {purchase_order.reference}: {status_change['to_status']}")
            return True
            
        except Exception as e:
            logger.exception(f"Failed to send status notification for PO {purchase_order.reference}: {e}")
            return False
