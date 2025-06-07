from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

def validate_serial_number(value):
    """
    Validate a serial number.

    A valid serial number must contain at least one letter (a-z or A-Z)
    and at least one digit (0-9).

    Args:
        value (str): The serial number to validate.

    Raises:
        ValidationError: If the serial number is not valid.
    """
    if not any(char.isalpha() for char in value):
        raise ValidationError(
            _('Serial number must contain at least one letter (a-z or A-Z).'),
            code='invalid_serial_number'
        )

    if not any(char.isdigit() for char in value):
        raise ValidationError(
            _('Serial number must contain at least one digit (0-9).'),
            code='invalid_serial_number'
        )



def validate_batch_code(value):
    """
    Validate a batch code.

    A valid batch code must contain at least one letter (a-z or A-Z)
    and at least one digit (0-9).

    Args:
        value (str): The batch code to validate.

    Raises:
        ValidationError: If the batch code is not valid.
    """
    if not any(char.isalpha() for char in value):
        raise ValidationError(
            _('Batch code must contain at least one letter (a-z or A-Z).'),
            code='invalid_batch_code'
        )

    if not any(char.isdigit() for char in value):
        raise ValidationError(
            _('Batch code must contain at least one digit (0-9).'),
            code='invalid_batch_code'
        )


"""Validation methods for the order app."""


def generate_next_sales_order_reference():
    """Generate the next available SalesOrder reference."""
    from mainapps.orders.models import SalesOrder

    return SalesOrder.generate_reference()


# def generate_next_purchase_order_reference():
#     """Generate the next available PurchasesOrder reference."""
#     from order.models import PurchaseOrder

#     return PurchaseOrder.generate_reference()


# def generate_next_return_order_reference():
#     """Generate the next available ReturnOrder reference."""
#     from order.models import ReturnOrder

#     return ReturnOrder.generate_reference()


# def validate_sales_order_reference_pattern(pattern):
#     """Validate the SalesOrder reference 'pattern' setting."""
#     from order.models import SalesOrder

#     SalesOrder.validate_reference_pattern(pattern)


# def validate_purchase_order_reference_pattern(pattern):
#     """Validate the PurchaseOrder reference 'pattern' setting."""
#     from order.models import PurchaseOrder

#     PurchaseOrder.validate_reference_pattern(pattern)


# def validate_return_order_reference_pattern(pattern):
#     """Validate the ReturnOrder reference 'pattern' setting."""
#     from mainapps.order.models import ReturnOrder

#     ReturnOrder.validate_reference_pattern(pattern)


# def validate_sales_order_reference(value):
#     """Validate that the SalesOrder reference field matches the required pattern."""
#     from mainapps.order.models import SalesOrder

#     SalesOrder.validate_reference_field(value)


# def validate_purchase_order_reference(value):
#     """Validate that the PurchaseOrder reference field matches the required pattern."""
#     from mainapps.order.models import PurchaseOrder

#     PurchaseOrder.validate_reference_field(value)


# def validate_return_order_reference(value):
#     """Validate that the ReturnOrder reference field matches the required pattern."""
#     from mainapps.order.models import ReturnOrder

#     ReturnOrder.validate_reference_field(value)
