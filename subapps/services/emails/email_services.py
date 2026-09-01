import logging
import os
from io import BytesIO

from django.conf import settings
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)
INTERA_BRAND = {
    "name": "Intera IMS",
    "deep_blue": "#101727",
    "bright_blue": "#3c83f7",
    "light_green": "#98fcc2",
    "surface": "#f5f9ff",
}


def get_workspace_display_name(order, default: str = "Company") -> str:
    """Resolve the local identity projection without treating legacy profile IDs as objects."""
    profile = getattr(order, "profile", None)
    for attribute in ("display_name", "name"):
        value = getattr(profile, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip()

    profile_id = getattr(order, "profile_id", None)
    if profile_id:
        try:
            from mainapps.identity.models import IdentityCompanyProfile

            display_name = (
                IdentityCompanyProfile.objects.filter(profile_id=profile_id, is_active=True)
                .values_list("display_name", flat=True)
                .first()
            )
            if display_name:
                return display_name
        except Exception:
            logger.warning("Could not resolve workspace display name for profile %s", profile_id, exc_info=True)

    return default


class EmailService:
    """Enhanced email service matching your existing implementation"""

    @classmethod
    def _resolve_from_email(cls):
        return getattr(settings, "DEFAULT_FROM_EMAIL", None) or settings.EMAIL_HOST_USER

    @classmethod
    def _resolve_reply_to(cls):
        reply_to = (getattr(settings, "EMAIL_DEFAULT_REPLY_TO", "") or "").strip()
        return [reply_to] if reply_to else None

    @classmethod
    def _email_backend_is_local_only(cls):
        backend = (getattr(settings, "EMAIL_BACKEND", "") or "").lower()
        return any(
            marker in backend
            for marker in (
                "console.emailbackend",
                "locmem.emailbackend",
                "dummy.emailbackend",
                "filebased.emailbackend",
            )
        )

    @classmethod
    def _mail_is_configured(cls):
        if cls._email_backend_is_local_only():
            return True
        return bool(settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD)

    @classmethod
    def _brand_context(cls):
        frontend_site = getattr(settings, "FRONTEND_SITE_URL", "").strip().rstrip("/")
        site_url = getattr(settings, "SITE_URL", "").strip().rstrip("/")
        brand_site_url = frontend_site or site_url
        white_logo = "/assets/img/logos/verticals/no-bg/INTERA-PRIMARY-LOGO-VERTICAL-WHITE-4.png"
        black_logo = "/assets/img/logos/verticals/no-bg/INTERA-PRIMARY-LOGO-VERTICAL-BLACK-3.png"
        return {
            "brand": INTERA_BRAND,
            "brand_name": INTERA_BRAND["name"],
            "brand_site_url": brand_site_url,
            "brand_logo_url": getattr(settings, "EMAIL_BRAND_LOGO_URL", "")
            or getattr(settings, "EMAIL_BRAND_LOGO_DARK_URL", "")
            or (f"{brand_site_url}{white_logo}" if brand_site_url else ""),
            "brand_logo_light_url": getattr(settings, "EMAIL_BRAND_LOGO_LIGHT_URL", "")
            or (f"{brand_site_url}{black_logo}" if brand_site_url else ""),
            "brand_logo_dark_url": getattr(settings, "EMAIL_BRAND_LOGO_DARK_URL", "")
            or (f"{brand_site_url}{white_logo}" if brand_site_url else ""),
            "support_email": getattr(settings, "EMAIL_SUPPORT_EMAIL", "") or "support@interaims.com",
        }

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
            if not cls._mail_is_configured():
                logger.error(
                    "Inventory email settings are incomplete. "
                    "Set EMAIL_HOST_USER / EMAIL_HOST_PASSWORD (or use a local-only EMAIL_BACKEND)."
                )
                return False

            # Check if contact exists and has email
            if not purchase_order.contact or not purchase_order.contact.email:
                logger.error(f"No contact or email for Purchase Order #{purchase_order.reference}")
                return False

            contact_email = purchase_order.contact.email
            company_name = get_workspace_display_name(purchase_order)
            subject = f"Purchase Order #{purchase_order.reference} from {company_name}"
            from_email = cls._resolve_from_email()
            to = [contact_email]

            # Add CC recipients if needed
            cc_emails = []
            if purchase_order.supplier and hasattr(purchase_order.supplier, 'email') and purchase_order.supplier.email:
                cc_emails.append(purchase_order.supplier.email)
            
            # Render email content using your template
            try:
                html_content = render_to_string("emails/purchase_order_email.html", {
                    "purchase_order": purchase_order,
                    "company_name": company_name,
                    "contact_name": purchase_order.contact.name or "Supplier",
                    "line_items": purchase_order.line_items.all(),
                    **cls._brand_context(),
                })
            except Exception as e:
                logger.exception("Failed to render email template.")
                return False

            # Create email with HTML content
            email = EmailMultiAlternatives(
                subject,
                "",
                from_email,
                to,
                cc=cc_emails,
                reply_to=cls._resolve_reply_to(),
            )
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
            email.send(fail_silently=False)
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
            if not cls._mail_is_configured():
                logger.error(
                    "Inventory email settings are incomplete. "
                    "Set EMAIL_HOST_USER / EMAIL_HOST_PASSWORD (or use a local-only EMAIL_BACKEND)."
                )
                return False

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
                'company_name': get_workspace_display_name(return_order),
                **cls._brand_context(),
            }
            
            # Render email template
            html_content = render_to_string('emails/return_order_email.html', context)
            
            # Create email
            email = EmailMessage(
                subject,
                html_content,
                cls._resolve_from_email(),
                recipients,
                reply_to=cls._resolve_reply_to(),
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
            
            email.send(fail_silently=False)
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
            if not cls._mail_is_configured():
                logger.error(
                    "Inventory email settings are incomplete. "
                    "Set EMAIL_HOST_USER / EMAIL_HOST_PASSWORD (or use a local-only EMAIL_BACKEND)."
                )
                return False

            if not purchase_order.contact or not purchase_order.contact.email:
                logger.warning(f"No contact email for PO status notification: {purchase_order.reference}")
                return False

            subject = f"Purchase Order {purchase_order.reference} Status Update - {status_change['to_status'].title()}"
            
            context = {
                'purchase_order': purchase_order,
                'status_change': status_change,
                'company_name': get_workspace_display_name(purchase_order),
                'contact_name': purchase_order.contact.name or "Supplier",
                **cls._brand_context(),
            }
            
            if additional_context:
                context.update(additional_context)
            
            html_content = render_to_string('emails/purchase_order_status_notification.html', context)
            
            email = EmailMultiAlternatives(
                subject,
                "",
                cls._resolve_from_email(),
                [purchase_order.contact.email],
                reply_to=cls._resolve_reply_to(),
            )
            email.attach_alternative(html_content, "text/html")
            email.send(fail_silently=False)
            
            logger.info(f"Status notification sent for PO {purchase_order.reference}: {status_change['to_status']}")
            return True
            
        except Exception as e:
            logger.exception(f"Failed to send status notification for PO {purchase_order.reference}: {e}")
            return False
