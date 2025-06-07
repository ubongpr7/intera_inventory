from django.template.loader import render_to_string
from weasyprint import HTML, CSS
from io import BytesIO
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

class PDFService:
    """Enhanced PDF service using WeasyPrint matching your implementation"""
    
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
            # Calculate totals (matching your template logic)
            tax = sum(line_item.tax_amount for line_item in purchase_order.line_items.all())
            discount = sum(line_item.discount for line_item in purchase_order.line_items.all())
            
            # Prepare template context
            context = {
                'po': purchase_order,
                'tax': tax,
                'company_profile': purchase_order.profile,
                'discount': discount,
                'line_items': purchase_order.line_items.all(),
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
