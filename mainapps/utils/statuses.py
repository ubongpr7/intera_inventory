
from django.utils.translation import gettext_lazy as _


import enum
import re


class BaseEnum(enum.IntEnum):
    """An `Enum` capabile of having its members have docstrings.

    Based on https://stackoverflow.com/questions/19330460/how-do-i-put-docstrings-on-enums
    """

    def __new__(cls, *args):
        """Assign values on creation."""
        obj = object.__new__(cls)
        obj._value_ = args[0]
        return obj

    def __eq__(self, obj):
        """Override equality operator to allow comparison with int."""
        if type(self) is type(obj):
            return super().__eq__(obj)
        return self.value == obj

    def __ne__(self, obj):
        """Override inequality operator to allow comparison with int."""
        if type(self) is type(obj):
            return super().__ne__(obj)
        return self.value != obj


class StatusCode(BaseEnum):
    """Base class for representing a set of StatusCodes.

    Use enum syntax to define the status codes, e.g.
    ```python
    PENDING = 10, _("Pending"), 'secondary'
    ```

    The values of the status can be accessed with `StatusCode.PENDING.value`.

    Additionally there are helpers to access all additional attributes `text`, `label`, `color`.
    """

    def __new__(cls, *args):
        """Define object out of args."""
        obj = int.__new__(cls)
        obj._value_ = args[0]

        # Normal item definition
        if len(args) == 1:
            obj.label = args[0]
            obj.color = 'secondary'
        else:
            obj.label = args[1]
            obj.color = args[2] if len(args) > 2 else 'secondary'

        return obj

    @classmethod
    def _is_element(cls, d):
        """Check if the supplied value is a valid status code."""
        if d.startswith('_'):
            return False
        if d != d.upper():
            return False

        value = getattr(cls, d, None)

        if value is None:
            return False
        if callable(value):
            return False
        if not isinstance(value.value, int):
            return False
        return True

    @classmethod
    def values(cls, key=None):
        """Return a dict representation containing all required information."""
        elements = [itm for itm in cls if cls._is_element(itm.name)]
        if key is None:
            return elements

        ret = [itm for itm in elements if itm.value == key]
        if ret:
            return ret[0]
        return None

    @classmethod
    def render(cls, key, large=False):
        """Render the value as a HTML label."""
        # If the key cannot be found, pass it back
        item = cls.values(key)
        if item is None:
            return key

        return f"<span class='badge rounded-pill bg-{item.color}'>{item.label}</span>"

    @classmethod
    def tag(cls):
        """Return tag for this status code."""
        # Return the tag if it is defined
        if hasattr(cls, '_TAG') and bool(cls._TAG):
            return cls._TAG.value

        # Try to find a default tag
        # Remove `Status` from the class name
        ref_name = cls.__name__.removesuffix('Status')
        # Convert to snake case
        return re.sub(r'(?<!^)(?=[A-Z])', '_', ref_name).lower()

    @classmethod
    def items(cls):
        """All status code items."""
        return [(x.value, x.label) for x in cls.values()]

    @classmethod
    def keys(cls):
        """All status code keys."""
        return [x.value for x in cls.values()]

    @classmethod
    def labels(cls):
        """All status code labels."""
        return [x.label for x in cls.values()]

    @classmethod
    def names(cls):
        """Return a map of all 'names' of status codes in this class."""
        return {x.name: x.value for x in cls.values()}

    @classmethod
    def text(cls, key):
        """Text for supplied status code."""
        filtered = cls.values(key)
        if filtered is None:
            return key
        return filtered.label

    @classmethod
    def label(cls, key):
        """Return the status code label associated with the provided value."""
        filtered = cls.values(key)
        if filtered is None:
            return key
        return filtered.label

    @classmethod
    def dict(cls, key=None):
        """Return a dict representation containing all required information."""
        return {
            x.name: {'color': x.color, 'key': x.value, 'label': x.label, 'name': x.name}
            for x in cls.values(key)
        }

    @classmethod
    def list(cls):
        """Return the StatusCode options as a list of mapped key / value items."""
        return list(cls.dict().values())

    @classmethod
    def template_context(cls):
        """Return a dict representation containing all required information for templates."""
        ret = {x.name: x.value for x in cls.values()}
        ret['list'] = cls.list()

        return ret



class PurchaseOrderStatus(StatusCode):
    """Defines a set of status codes for a PurchaseOrder."""

    # Order status codes
    PENDING = 10, _('Pending'), 'secondary'  # Order is pending (not yet placed)
    PLACED = 20, _('Placed'), 'primary'  # Order has been placed with supplier
    COMPLETE = 30, _('Complete'), 'success'  # Order has been completed
    CANCELLED = 40, _('Cancelled'), 'danger'  # Order was cancelled
    LOST = 50, _('Lost'), 'warning'  # Order was lost
    RETURNED = 60, _('Returned'), 'warning'  # Order was returned


class PurchaseOrderStatusGroups:
    """Groups for PurchaseOrderStatus codes."""

    # Open orders
    OPEN = [PurchaseOrderStatus.PENDING.value, PurchaseOrderStatus.PLACED.value]

    # Failed orders
    FAILED = [
        PurchaseOrderStatus.CANCELLED.value,
        PurchaseOrderStatus.LOST.value,
        PurchaseOrderStatus.RETURNED.value,
    ]


class SalesOrderStatus(StatusCode):
    """Defines a set of status codes for a SalesOrder."""

    PENDING = 10, _('Pending'), 'secondary'  # Order is pending
    IN_PROGRESS = (
        15,
        _('In Progress'),
        'primary',
    )  # Order has been issued, and is in progress
    SHIPPED = 20, _('Shipped'), 'success'  # Order has been shipped to customer
    CANCELLED = 40, _('Cancelled'), 'danger'  # Order has been cancelled
    LOST = 50, _('Lost'), 'warning'  # Order was lost
    RETURNED = 60, _('Returned'), 'warning'  # Order was returned


class SalesOrderStatusGroups:
    """Groups for SalesOrderStatus codes."""

    # Open orders
    OPEN = [SalesOrderStatus.PENDING.value, SalesOrderStatus.IN_PROGRESS.value]

    # Completed orders
    COMPLETE = [SalesOrderStatus.SHIPPED.value]


class StockStatus(StatusCode):
    """Status codes for Stock."""

    OK = 10, _('OK'), 'success'  # Item is OK
    ATTENTION = 50, _('Attention needed'), 'warning'  # Item requires attention
    DAMAGED = 55, _('Damaged'), 'warning'  # Item is damaged
    DESTROYED = 60, _('Destroyed'), 'danger'  # Item is destroyed
    REJECTED = 65, _('Rejected'), 'danger'  # Item is rejected
    LOST = 70, _('Lost'), 'dark'  # Item has been lost
    QUARANTINED = (
        75,
        _('Quarantined'),
        'info',
    )  # Item has been quarantined and is unavailable
    RETURNED = 85, _('Returned'), 'warning'  # Item has been returned from a customer


class StockStatusGroups:
    """Groups for StockStatus codes."""

    # The following codes correspond to parts that are 'available' or 'in stock'
    AVAILABLE_CODES = [
        StockStatus.OK.value,
        StockStatus.ATTENTION.value,
        StockStatus.DAMAGED.value,
        StockStatus.RETURNED.value,
    ]


class StockHistoryCode(StatusCode):
    """Status codes for StockHistory."""

    LEGACY = 0, _('Legacy stock tracking entry')

    CREATED = 1, _('Stock item created')

    # Manual editing operations
    EDITED = 5, _('Edited stock item')
    ASSIGNED_SERIAL = 6, _('Assigned serial number')

    # Manual stock operations
    STOCK_COUNT = 10, _('Stock counted')
    STOCK_ADD = 11, _('Stock manually added')
    STOCK_REMOVE = 12, _('Stock manually removed')

    # Location operations
    STOCK_MOVE = 20, _('Location changed')
    STOCK_UPDATE = 25, _('Stock updated')

    # Installation operations
    INSTALLED_INTO_ASSEMBLY = 30, _('Installed into assembly')
    REMOVED_FROM_ASSEMBLY = 31, _('Removed from assembly')

    INSTALLED_CHILD_ITEM = 35, _('Installed component item')
    REMOVED_CHILD_ITEM = 36, _('Removed component item')

    # Stock splitting operations
    SPLIT_FROM_PARENT = 40, _('Split from parent item')
    SPLIT_CHILD_ITEM = 42, _('Split child item')

    # Stock merging operations
    MERGED_STOCK_ITEMS = 45, _('Merged stock items')

    # Convert stock item to variant
    CONVERTED_TO_VARIANT = 48, _('Converted to variant')

    # Build order codes
    BUILD_OUTPUT_CREATED = 50, _('Build order output created')
    BUILD_OUTPUT_COMPLETED = 55, _('Build order output completed')
    BUILD_OUTPUT_REJECTED = 56, _('Build order output rejected')
    BUILD_CONSUMED = 57, _('Consumed by build order')

    # Sales order codes
    SHIPPED_AGAINST_SALES_ORDER = 60, _('Shipped against Sales Order')

    # Purchase order codes
    RECEIVED_AGAINST_PURCHASE_ORDER = 70, _('Received against Purchase Order')

    # Return order codes
    RETURNED_AGAINST_RETURN_ORDER = 80, _('Returned against Return Order')

    # Customer actions
    SENT_TO_CUSTOMER = 100, _('Sent to customer')
    RETURNED_FROM_CUSTOMER = 105, _('Returned from customer')


class BuildStatus(StatusCode):
    """Build status codes."""

    PENDING = 10, _('Pending'), 'secondary'  # Build is pending / active
    PRODUCTION = 20, _('Production'), 'primary'  # BuildOrder is in production
    CANCELLED = 30, _('Cancelled'), 'danger'  # Build was cancelled
    COMPLETE = 40, _('Complete'), 'success'  # Build is complete


class BuildStatusGroups:
    """Groups for BuildStatus codes."""

    ACTIVE_CODES = [BuildStatus.PENDING.value, BuildStatus.PRODUCTION.value]


class ReturnOrderStatus(StatusCode):
    """Defines a set of status codes for a ReturnOrder."""

    # Order is pending, waiting for receipt of items
    PENDING = 10, _('Pending'), 'secondary'

    # Items have been received, and are being inspected
    IN_PROGRESS = 20, _('In Progress'), 'primary'

    COMPLETE = 30, _('Complete'), 'success'
    CANCELLED = 40, _('Cancelled'), 'danger'


class ReturnOrderStatusGroups:
    """Groups for ReturnOrderStatus codes."""

    OPEN = [ReturnOrderStatus.PENDING.value, ReturnOrderStatus.IN_PROGRESS.value]


class ReturnOrderLineStatus(StatusCode):
    """Defines a set of status codes for a ReturnOrderLineItem."""

    PENDING = 10, _('Pending'), 'secondary'

    # Item is to be returned to customer, no other action
    RETURN = 20, _('Return'), 'success'

    # Item is to be repaired, and returned to customer
    REPAIR = 30, _('Repair'), 'primary'

    # Item is to be replaced (new item shipped)
    REPLACE = 40, _('Replace'), 'warning'

    # Item is to be refunded (cannot be repaired)
    REFUND = 50, _('Refund'), 'info'

    # Item is rejected
    REJECT = 60, _('Reject'), 'danger'
