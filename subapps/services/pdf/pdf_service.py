from django.template.loader import render_to_string
from io import BytesIO
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from html import escape
import logging
import os
import json
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from subapps.services.emails.email_services import get_workspace_display_name
from mainapps.identity.models import IdentityCompanyProfile

logger = logging.getLogger(__name__)


class PDFServiceUnavailableError(RuntimeError):
    """Raised when PDF generation dependencies are unavailable on the host."""


def _load_weasyprint():
    """Import WeasyPrint lazily so missing native libs do not break app startup."""
    try:
        homebrew_lib = "/opt/homebrew/lib"
        if os.path.isdir(homebrew_lib):
            existing = [path for path in (os.environ.get("DYLD_FALLBACK_LIBRARY_PATH") or "").split(":") if path]
            if homebrew_lib not in existing:
                os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join([homebrew_lib, *existing])

        from weasyprint import HTML, CSS
        return HTML, CSS
    except Exception as exc:
        logger.error("WeasyPrint is unavailable: %s", exc)
        raise PDFServiceUnavailableError(
            "PDF generation is unavailable because WeasyPrint native dependencies are not installed."
        ) from exc

class PDFService:
    """Enhanced PDF service using WeasyPrint matching your implementation"""

    @staticmethod
    def _as_record(value):
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _as_array(value):
        return value if isinstance(value, list) else []

    @staticmethod
    def _as_string(value):
        return value if isinstance(value, str) else ""

    @staticmethod
    def _format_company_address(address) -> str:
        if not address:
            return ""
        parts = [
            getattr(address, "title", ""),
            getattr(address, "address", ""),
            getattr(address, "short_address", ""),
        ]
        return "\n".join(str(part).strip() for part in parts if str(part or "").strip())

    @staticmethod
    def _resolve_shared_address(address_id):
        if not address_id:
            return {}
        service_key = os.getenv("SUBSCRIPTION_SERVICE_KEY", "")
        if not service_key:
            return {}
        base_url = os.getenv("SUBSCRIPTION_SERVICE_URL", "http://subscriptions:8550").rstrip("/")
        try:
            request = Request(
                f"{base_url}/api/v1/locations/internal/addresses/{address_id}/",
                headers={"X-Intera-Service-Key": service_key, "Accept": "application/json"},
            )
            with urlopen(request, timeout=float(os.getenv("SUBSCRIPTION_SERVICE_TIMEOUT", "2.0"))) as response:
                value = json.loads(response.read().decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception as exc:
            logger.warning("Shared address resolution failed for %s: %s", address_id, exc)
            return {}

    @classmethod
    def _format_shared_or_local_address(cls, address, profile_id=None) -> str:
        address_id = getattr(address, "address_id", None)
        resolved = cls._resolve_shared_address(address_id) if address_id else {}
        if resolved:
            parts = [
                resolved.get("address_line_1"),
                resolved.get("address_line_2"),
                resolved.get("city_name") or resolved.get("city"),
                resolved.get("region_name") or resolved.get("region"),
                resolved.get("country_name") or resolved.get("country"),
                resolved.get("postal_code"),
            ]
            return "\n".join(str(part).strip() for part in parts if str(part or "").strip())
        return cls._format_company_address(address)

    @staticmethod
    def _workspace_logo_url(profile_id) -> str:
        if not profile_id:
            return ""
        logo_url = (
            IdentityCompanyProfile.objects.filter(profile_id=profile_id, is_active=True)
            .values_list("logo_url", flat=True)
            .first()
            or ""
        ).strip()
        parsed = urlparse(logo_url)
        return logo_url if parsed.scheme == "https" and parsed.netloc else ""

    @staticmethod
    def _workspace_address(profile_id) -> str:
        if not profile_id:
            return ""
        profile = IdentityCompanyProfile.objects.filter(profile_id=profile_id, is_active=True).first()
        if not profile:
            return ""

        address = {}
        if profile.headquarters_address_id:
            base_url = os.getenv("SUBSCRIPTION_SERVICE_URL", "http://subscriptions:8550").rstrip("/")
            service_key = os.getenv("SUBSCRIPTION_SERVICE_KEY", "")
            if service_key:
                try:
                    request = Request(
                        f"{base_url}/api/v1/locations/internal/addresses/{profile.headquarters_address_id}/",
                        headers={"X-Intera-Service-Key": service_key, "Accept": "application/json"},
                    )
                    with urlopen(request, timeout=float(os.getenv("SUBSCRIPTION_SERVICE_TIMEOUT", "2.0"))) as response:
                        resolved = json.loads(response.read().decode("utf-8"))
                    if isinstance(resolved, dict):
                        address = resolved
                except Exception as exc:
                    logger.warning("Shared headquarters address resolution failed for profile %s: %s", profile_id, exc)

        if not address:
            address = profile.headquarters_address or {}
        if not isinstance(address, dict):
            return ""
        street_parts = [
            str(address.get("street_number") or "").strip(),
            str(address.get("street") or address.get("address_line_1") or "").strip(),
        ]
        location_parts = [
            str(address.get("city_name") or address.get("city") or "").strip(),
            str(address.get("subregion_name") or address.get("subregion") or "").strip(),
            str(address.get("region_name") or address.get("region") or "").strip(),
        ]
        lines = [" ".join(part for part in street_parts if part)]
        if address.get("apt_number") not in (None, ""):
            lines.append(f"Apt {address['apt_number']}")
        lines.extend(
            [
                ", ".join(part for part in location_parts if part),
                str(address.get("country_name") or address.get("country") or "").strip(),
                str(address.get("postal_code") or "").strip(),
            ]
        )
        return "\n".join(line for line in lines if line)

    @classmethod
    def _render_badges(cls, title, values, tone):
        if not values:
            return ""
        badges = "".join(
            f'<span class="chip {escape(tone)}">{escape(cls._as_string(value))}</span>'
            for value in values
            if cls._as_string(value).strip()
        )
        if not badges:
            return ""
        return f"""
        <section class="card">
          <h3>{escape(title)}</h3>
          <div class="chips">{badges}</div>
        </section>
        """

    @classmethod
    def _render_table(cls, columns, rows):
        header_html = "".join(f"<th>{escape(column)}</th>" for column in columns)
        body_html = "".join(
            "<tr>"
            + "".join(
                f"<td>{escape('' if row.get(column) is None else str(row.get(column))) or '&nbsp;'}</td>"
                for column in columns
            )
            + "</tr>"
            for row in rows
        )
        return f"""
        <div class="table-wrap">
          <table>
            <thead><tr>{header_html}</tr></thead>
            <tbody>{body_html}</tbody>
          </table>
        </div>
        """

    @classmethod
    def _widget_rows(cls, widget):
        widget = cls._as_record(widget)
        widget_type = cls._as_string(widget.get("type")) or "widget"
        title = cls._as_string(widget.get("title"))
        subtitle = cls._as_string(widget.get("subtitle"))
        base = {
            "widget_type": widget_type,
            "widget_title": title,
            "widget_subtitle": subtitle,
        }

        if widget_type == "metric_grid":
            return [
                {
                    **base,
                    "row_type": "metric",
                    "label": cls._as_string(item.get("label")) or f"Metric {index + 1}",
                    "value": item.get("value"),
                    "unit": cls._as_string(item.get("unit")) or None,
                    "detail": cls._as_string(item.get("detail")) or None,
                }
                for index, item in enumerate(cls._as_record(item) for item in cls._as_array(widget.get("data")))
                if item
            ]

        if widget_type in {"bar_chart", "histogram", "line_chart", "donut_chart", "sparkline_metric"}:
            x_key = cls._as_string(widget.get("x_key")) or cls._as_string(widget.get("label_key")) or "label"
            y_key = cls._as_string(widget.get("y_key")) or cls._as_string(widget.get("value_key")) or "value"
            return [
                {
                    **base,
                    "row_type": "chart_point",
                    "label": item.get(x_key),
                    "value": item.get(y_key),
                }
                for item in (cls._as_record(entry) for entry in cls._as_array(widget.get("data")))
                if item
            ]

        if widget_type == "ranked_list":
            rows = []
            for index, item in enumerate(cls._as_record(entry) for entry in cls._as_array(widget.get("items"))):
                if not item:
                    continue
                meta = cls._as_record(item.get("meta"))
                rows.append(
                    {
                        **base,
                        "row_type": "ranked_item",
                        "rank": index + 1,
                        "label": cls._as_string(item.get("label")) or cls._as_string(item.get("title")) or f"Item {index + 1}",
                        "value": item.get("value", item.get("count")),
                        "secondary_value": item.get("secondary_value"),
                        "detail": cls._as_string(item.get("detail")) or None,
                        "barcode": cls._as_string(item.get("barcode") or meta.get("barcode")) or None,
                        "image_url": cls._as_string(item.get("image_url")) or None,
                    }
                )
            return rows

        if widget_type == "risk_panel":
            return [
                {
                    **base,
                    "row_type": "risk",
                    "label": cls._as_string(item.get("label")) or f"Risk {index + 1}",
                    "severity": cls._as_string(item.get("severity") or widget.get("severity")) or None,
                    "detail": cls._as_string(item.get("detail")) or None,
                    "next_action": cls._as_string(item.get("next_action")) or None,
                }
                for index, item in enumerate(cls._as_record(entry) for entry in cls._as_array(widget.get("items")))
                if item
            ]

        if widget_type == "comparison_table":
            columns = []
            for index, column in enumerate(cls._as_array(widget.get("columns"))):
                record = cls._as_record(column)
                columns.append(
                    {
                        "key": cls._as_string(record.get("key")) or str(column) or f"col_{index}",
                        "label": cls._as_string(record.get("label")) or cls._as_string(record.get("key")) or str(column) or f"Column {index + 1}",
                    }
                )
            rows = []
            for row in (cls._as_record(entry) for entry in cls._as_array(widget.get("rows"))):
                if not row:
                    continue
                output = {**base, "row_type": "table_row"}
                for column in columns:
                    output[column["label"]] = row.get(column["key"])
                rows.append(output)
            return rows

        if widget_type == "timeline":
            return [
                {
                    **base,
                    "row_type": "timeline_event",
                    "title": cls._as_string(item.get("title")) or cls._as_string(item.get("event_name")) or f"Event {index + 1}",
                    "timestamp": cls._as_string(item.get("timestamp")) or cls._as_string(item.get("occurred_at")) or None,
                    "severity": cls._as_string(item.get("severity")) or None,
                    "detail": cls._as_string(item.get("detail")) or cls._as_string(item.get("summary")) or None,
                }
                for index, item in enumerate(
                    cls._as_record(entry) for entry in cls._as_array(widget.get("events") or widget.get("items"))
                )
                if item
            ]

        if widget_type == "progress_tracker":
            return [
                {
                    **base,
                    "row_type": "progress_step",
                    "label": cls._as_string(item.get("label")) or f"Step {index + 1}",
                    "status": cls._as_string(item.get("status")) or None,
                    "detail": cls._as_string(item.get("detail")) or None,
                }
                for index, item in enumerate(cls._as_record(entry) for entry in cls._as_array(widget.get("steps")))
                if item
            ]

        if widget_type == "entity_preview":
            entity = cls._as_record(widget.get("entity"))
            return [
                {
                    **base,
                    "row_type": "entity_preview",
                    "title": cls._as_string(entity.get("title")) or cls._as_string(entity.get("name")) or None,
                    "subtitle": cls._as_string(entity.get("subtitle")) or None,
                    "kind": cls._as_string(entity.get("kind")) or None,
                    "image_url": cls._as_string(entity.get("image_url")) or None,
                }
            ]

        if widget_type == "action_form":
            return [
                {
                    **base,
                    "row_type": "action_field",
                    "label": cls._as_string(item.get("label")) or cls._as_string(item.get("name")) or f"Field {index + 1}",
                    "field_name": cls._as_string(item.get("name")) or None,
                    "field_type": cls._as_string(item.get("type")) or None,
                    "required": item.get("required"),
                    "placeholder": cls._as_string(item.get("placeholder")) or None,
                }
                for index, item in enumerate(cls._as_record(entry) for entry in cls._as_array(widget.get("fields")))
                if item
            ]

        if widget_type == "confirmation_card":
            return [
                {
                    **base,
                    "row_type": "confirmation",
                    "summary": cls._as_string(widget.get("summary")) or None,
                    "risk_level": cls._as_string(widget.get("risk_level")) or None,
                    "action": cls._as_string(widget.get("action")) or None,
                }
            ]

        return [{**base, "row_type": "raw_widget", "raw_json": str(widget)}]

    @classmethod
    def _render_insight_report_body_html(cls, payload):
        payload = cls._as_record(payload)
        summary = cls._as_string(payload.get("summary")).strip()
        explanation = cls._as_string(payload.get("explanation")).strip()
        insights = [cls._as_record(item) for item in cls._as_array(payload.get("insights")) if cls._as_record(item)]
        widgets = [cls._as_record(item) for item in cls._as_array(payload.get("widgets")) if cls._as_record(item)]
        actions = [cls._as_record(item) for item in cls._as_array(payload.get("suggested_actions")) if cls._as_record(item)]
        warnings = [cls._as_string(item).strip() for item in cls._as_array(payload.get("warnings")) if cls._as_string(item).strip()]
        permissions = [cls._as_string(item).strip() for item in cls._as_array(payload.get("permissions_checked")) if cls._as_string(item).strip()]
        data_sources = [cls._as_record(item) for item in cls._as_array(payload.get("data_sources")) if cls._as_record(item)]

        hero_html = ""
        if summary or explanation or insights:
            hero_html = f"""
            <section class="hero">
              {'<h2>' + escape(summary) + '</h2>' if summary else ''}
              {'<p>' + escape(explanation) + '</p>' if explanation else ''}
              {
                '<div class="insight-grid">' + ''.join(
                    f'''
                    <article class="mini-card">
                      <h4>{escape(cls._as_string(insight.get("title")) or f"Insight {index + 1}")}</h4>
                      <p>{escape(cls._as_string(insight.get("detail")) or cls._as_string(insight.get("summary")) or cls._as_string(insight.get("description")))}</p>
                    </article>
                    '''
                    for index, insight in enumerate(insights)
                ) + '</div>'
                if insights else ''
              }
            </section>
            """

        widgets_html = ""
        for widget in widgets:
            rows = cls._widget_rows(widget)
            widget_type = cls._as_string(widget.get("type")) or "widget"
            title = cls._as_string(widget.get("title")) or widget_type.replace("_", " ").title()
            subtitle = cls._as_string(widget.get("subtitle"))
            columns = []
            for row in rows:
                for key in row.keys():
                    if key not in {"widget_type", "widget_title", "widget_subtitle"} and key not in columns:
                        columns.append(key)
            widgets_html += f"""
            <section class="card">
              <div class="section-head">
                <div>
                  <h3>{escape(title)}</h3>
                  {'<p class="muted">' + escape(subtitle) + '</p>' if subtitle else ''}
                </div>
                <span class="type-pill">{escape(widget_type)}</span>
              </div>
              {cls._render_table(columns, rows)}
            </section>
            """

        actions_html = ""
        if actions:
            actions_html = """
            <section class="card">
              <h3>Suggested actions</h3>
              <ul class="list">
                %s
              </ul>
            </section>
            """ % "".join(
                f"<li><strong>{escape(cls._as_string(action.get('label')) or cls._as_string(action.get('action')) or f'Action {index + 1}')}</strong>{': ' + escape(cls._as_string(action.get('prompt'))) if cls._as_string(action.get('prompt')) else ''}</li>"
                for index, action in enumerate(actions)
            )

        data_sources_html = ""
        if data_sources:
            rows = [
                {
                    "service": cls._as_string(item.get("service")) or None,
                    "endpoint_or_topic": cls._as_string(item.get("endpoint_or_topic")) or None,
                    "freshness": cls._as_string(item.get("freshness")) or None,
                }
                for item in data_sources
            ]
            data_sources_html = f"""
            <section class="card">
              <h3>Data sources</h3>
              {cls._render_table(["service", "endpoint_or_topic", "freshness"], rows)}
            </section>
            """

        return (
            hero_html
            + widgets_html
            + actions_html
            + cls._render_badges("Warnings", warnings, "warning")
            + cls._render_badges("Permissions checked", permissions, "neutral")
            + data_sources_html
        )

    @classmethod
    def _render_chat_report_body_html(cls, messages):
        html = ""
        for message in cls._as_array(messages):
            record = cls._as_record(message)
            role = cls._as_string(record.get("role")) or "assistant"
            timestamp = cls._as_string(record.get("timestamp"))
            structured_payload = cls._as_record(record.get("structuredPayload"))
            body_html = ""
            if cls._as_string(structured_payload.get("kind")) == "insight_response":
                body_html = cls._render_insight_report_body_html(structured_payload)
            else:
                body_html = f'<div class="message-body text-block">{escape(cls._as_string(record.get("content"))).replace(chr(10), "<br />")}</div>'
            html += f"""
            <section class="message {'user' if role == 'user' else 'assistant'}">
              <div class="message-meta">
                <span class="author">{'You' if role == 'user' else 'Intera AI'}</span>
                {'<span class="timestamp">' + escape(timestamp) + '</span>' if timestamp else ''}
              </div>
              {body_html}
            </section>
            """
        return html

    @classmethod
    def generate_ai_insight_report_pdf(cls, payload, title="AI Insight Report"):
        try:
            HTML, CSS = _load_weasyprint()
            context = {
                "title": title,
                "generated_at": timezone.now(),
                "body_html": cls._render_insight_report_body_html(payload),
                "mode": "insight",
            }
            html_string = render_to_string("pdf/ai_report.html", context)
            pdf_file = BytesIO()
            HTML(string=html_string, base_url=settings.STATIC_ROOT).write_pdf(
                pdf_file,
                stylesheets=[CSS(string='@page { size: A4; margin: 1.2cm; }')],
            )
            pdf_file.seek(0)
            return pdf_file
        except Exception as e:
            logger.error("Failed to generate AI insight report PDF: %s", str(e))
            raise

    @classmethod
    def generate_ai_chat_report_pdf(cls, messages, title="AI Chat Export"):
        try:
            HTML, CSS = _load_weasyprint()
            context = {
                "title": title,
                "generated_at": timezone.now(),
                "body_html": cls._render_chat_report_body_html(messages),
                "mode": "chat",
            }
            html_string = render_to_string("pdf/ai_report.html", context)
            pdf_file = BytesIO()
            HTML(string=html_string, base_url=settings.STATIC_ROOT).write_pdf(
                pdf_file,
                stylesheets=[CSS(string='@page { size: A4; margin: 1.2cm; }')],
            )
            pdf_file.seek(0)
            return pdf_file
        except Exception as e:
            logger.error("Failed to generate AI chat report PDF: %s", str(e))
            raise
    
    @classmethod
    def generate_purchase_order_pdf(cls, purchase_order):
        """
        Generate PDF for purchase order using your exact template and styling
        Args:
            purchase_order: PurchaseOrder instance
        Returns:
            BytesIO: PDF file content
        """
        try:
            HTML, CSS = _load_weasyprint()

            # Calculate totals (matching your template logic)
            tax = sum(line_item.tax_amount for line_item in purchase_order.line_items.all())
            discount = sum(line_item.discount for line_item in purchase_order.line_items.all())
            
            supplier = purchase_order.supplier
            purchase_order_currency = str(
                purchase_order.order_currency or getattr(supplier, "currency", "") or ""
            ).strip().upper()
            line_items = purchase_order.line_items.select_related("inventory_item").all()
            headquarters_address = cls._workspace_address(purchase_order.profile_id)
            shipping_address = cls._format_shared_or_local_address(
                purchase_order.address,
                purchase_order.profile_id,
            ) or headquarters_address

            # Keep the PDF independent from legacy profile/address object shapes.
            context = {
                'po': purchase_order,
                'tax': tax,
                'company_name': get_workspace_display_name(purchase_order),
                'company_logo_url': cls._workspace_logo_url(purchase_order.profile_id),
                'company_address': headquarters_address,
                'supplier_address': cls._format_company_address(supplier),
                'shipping_address': shipping_address,
                'currency_code': purchase_order_currency or "Not specified",
                'discount': discount,
                'subtotal': purchase_order.total_price + discount - tax,
                'valid_until': (
                    purchase_order.issue_date + timedelta(days=30)
                    if purchase_order.issue_date
                    else None
                ),
                'line_items': line_items,
                'static_path': settings.STATIC_ROOT  
            }
            
            # Render HTML template
            html_string = render_to_string('pdf/purchase_order.html', context)
            
            # Create PDF with A4 landscape sizing (matching your CSS)
            pdf_file = BytesIO()
            HTML(string=html_string, base_url=settings.STATIC_ROOT).write_pdf(
                pdf_file,
                stylesheets=[CSS(string='@page { size: A4 landscape; margin: 1cm; }')]
            )
            pdf_file.seek(0)
            
            logger.info(f"Generated PDF for Purchase Order {purchase_order.reference}")
            return pdf_file
            
        except Exception as e:
            logger.error(f"Failed to generate purchase order PDF for {purchase_order.reference}: {str(e)}")
            raise

    @classmethod
    def generate_return_order_pdf(cls, return_order):
        """
        Generate PDF for return order
        Args:
            return_order: ReturnOrder instance
        Returns:
            BytesIO: PDF file content
        """
        try:
            HTML, CSS = _load_weasyprint()

            # Prepare template context
            context = {
                'return_order': return_order,
                'company_profile': return_order.profile,
                'line_items': return_order.line_items.select_related('original_line_item'),
                'static_path': settings.STATIC_ROOT,
                'purchase_order': return_order.purchase_order  # Include original PO details
            }
            
            # Render HTML template
            html_string = render_to_string('pdf/return_order.html', context)
            
            # Create PDF with A4 portrait sizing
            pdf_file = BytesIO()
            HTML(string=html_string, base_url=settings.STATIC_ROOT).write_pdf(
                pdf_file,
                stylesheets=[CSS(string='@page { size: A4; margin: 1cm; }')]
            )
            pdf_file.seek(0)
            
            logger.info(f"Generated PDF for Return Order {return_order.reference}")
            return pdf_file
            
        except Exception as e:
            logger.error(f"Failed to generate return order PDF for {return_order.reference}: {str(e)}")
            raise

    @classmethod
    def generate_purchase_order_summary_pdf(cls, purchase_orders, date_range=None):
        """
        Generate summary PDF for multiple purchase orders
        Args:
            purchase_orders: QuerySet of PurchaseOrder instances
            date_range: dict with 'start_date' and 'end_date'
        Returns:
            BytesIO: PDF file content
        """
        try:
            HTML, CSS = _load_weasyprint()

            # Calculate summary data
            total_orders = purchase_orders.count()
            total_value = sum(po.total_price for po in purchase_orders)
            
            # Group by status
            status_summary = {}
            for po in purchase_orders:
                status = po.status
                if status not in status_summary:
                    status_summary[status] = {'count': 0, 'value': 0}
                status_summary[status]['count'] += 1
                status_summary[status]['value'] += po.total_price

            context = {
                'purchase_orders': purchase_orders,
                'total_orders': total_orders,
                'total_value': total_value,
                'status_summary': status_summary,
                'date_range': date_range,
                'static_path': settings.STATIC_ROOT
            }
            
            html_string = render_to_string('pdf/purchase_order_summary.html', context)
            
            pdf_file = BytesIO()
            HTML(string=html_string, base_url=settings.STATIC_ROOT).write_pdf(
                pdf_file,
                stylesheets=[CSS(string='@page { size: A4; margin: 1cm; }')]
            )
            pdf_file.seek(0)
            
            logger.info(f"Generated summary PDF for {total_orders} purchase orders")
            return pdf_file
            
        except Exception as e:
            logger.error(f"Failed to generate purchase order summary PDF: {str(e)}")
            raise

    @classmethod
    def generate_supplier_statement_pdf(cls, supplier, purchase_orders, period):
        """
        Generate supplier statement PDF
        Args:
            supplier: Supplier/Company instance
            purchase_orders: QuerySet of PurchaseOrder instances for this supplier
            period: dict with period information
        Returns:
            BytesIO: PDF file content
        """
        try:
            HTML, CSS = _load_weasyprint()

            # Calculate supplier metrics
            total_orders = purchase_orders.count()
            total_value = sum(po.total_price for po in purchase_orders)
            completed_orders = purchase_orders.filter(status='completed').count()
            pending_orders = purchase_orders.exclude(status__in=['completed', 'cancelled']).count()

            context = {
                'supplier': supplier,
                'purchase_orders': purchase_orders.order_by('-created_at'),
                'total_orders': total_orders,
                'total_value': total_value,
                'completed_orders': completed_orders,
                'pending_orders': pending_orders,
                'period': period,
                'static_path': settings.STATIC_ROOT
            }
            
            html_string = render_to_string('pdf/supplier_statement.html', context)
            
            pdf_file = BytesIO()
            HTML(string=html_string, base_url=settings.STATIC_ROOT).write_pdf(
                pdf_file,
                stylesheets=[CSS(string='@page { size: A4; margin: 1cm; }')]
            )
            pdf_file.seek(0)
            
            logger.info(f"Generated supplier statement PDF for {supplier.name}")
            return pdf_file
            
        except Exception as e:
            logger.error(f"Failed to generate supplier statement PDF: {str(e)}")
            raise
