import logging
import threading

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)
INTERA_BRAND = {
    "name": "Intera IMS",
    "deep_blue": "#101727",
    "bright_blue": "#3c83f7",
    "light_green": "#98fcc2",
    "surface": "#f5f9ff",
}


class EmailThread(threading.Thread):
    def __init__(self, email_message):
        self.email_message = email_message
        threading.Thread.__init__(self, daemon=True)

    def run(self):
        try:
            self.email_message.send(fail_silently=False)
            logger.info("Inventory email sent successfully.")
        except Exception:
            logger.exception("Inventory email delivery failed.")


def _default_reply_to():
    reply_to = (getattr(settings, "EMAIL_DEFAULT_REPLY_TO", "") or "").strip()
    return [reply_to] if reply_to else None


def send_html_email(subject, message, to_email, html_file, context=None, from_email=None, reply_to=None):
    frontend_site = getattr(settings, "FRONTEND_SITE_URL", "").strip().rstrip("/")
    site_url = getattr(settings, "SITE_URL", "").strip().rstrip("/")
    brand_site_url = frontend_site or site_url
    white_logo = "/assets/img/logos/verticals/no-bg/INTERA-PRIMARY-LOGO-VERTICAL-WHITE-4.png"
    black_logo = "/assets/img/logos/verticals/no-bg/INTERA-PRIMARY-LOGO-VERTICAL-BLACK-3.png"
    template_context = {
        "subject": subject,
        "message": message,
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
    if context:
        template_context.update(context)

    html_content = render_to_string(html_file, template_context)
    text_content = strip_tags(html_content)
    resolved_from_email = from_email or getattr(settings, "DEFAULT_FROM_EMAIL", None) or settings.EMAIL_HOST_USER
    resolved_reply_to = reply_to if reply_to is not None else _default_reply_to()
    msg = EmailMultiAlternatives(
        subject,
        text_content,
        resolved_from_email,
        to_email,
        reply_to=resolved_reply_to,
    )
    msg.attach_alternative(html_content, "text/html")
    EmailThread(msg).start()
