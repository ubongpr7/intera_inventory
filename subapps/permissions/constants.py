from django.utils.translation import gettext_lazy as _

class CombinedPermissions:
    """
    Permission constants for the inventory microservice
    Mirrors the permissions from the user microservice
    """
    
    # Inventory Permissions
    CREATE_INVENTORY = 'create_inventory'
    READ_INVENTORY = 'read_inventory'
    UPDATE_INVENTORY = 'update_inventory'
    DELETE_INVENTORY = 'delete_inventory'
    APPROVE_INVENTORY = 'approve_inventory'
    REJECT_INVENTORY = 'reject_inventory'
    ARCHIVE_INVENTORY = 'archive_inventory'
    RESTORE_INVENTORY = 'restore_inventory'
    MANAGE_INVENTORY_SETTINGS = 'manage_inventory_settings'
    VIEW_INVENTORY_REPORTS = 'view_inventory_reports'
    VIEW_DASHBOARD_REPORTS = 'can_view_dashboard'
    
    # Inventory Category Permissions
    CREATE_INVENTORY_CATEGORY = 'create_inventory_category'
    READ_INVENTORY_CATEGORY = 'read_inventory_category'
    UPDATE_INVENTORY_CATEGORY = 'update_inventory_category'
    DELETE_INVENTORY_CATEGORY = 'delete_inventory_category'
    APPROVE_INVENTORY_CATEGORY = 'approve_inventory_category'
    
    # Purchase Order Permissions
    CREATE_PURCHASE_ORDER = 'create_purchase_order'
    READ_PURCHASE_ORDER = 'read_purchase_order'
    UPDATE_PURCHASE_ORDER = 'update_purchase_order'
    DELETE_PURCHASE_ORDER = 'delete_purchase_order'
    APPROVE_PURCHASE_ORDER = 'approve_purchase_order'
    REJECT_PURCHASE_ORDER = 'reject_purchase_order'
    ISSUE_PURCHASE_ORDER = 'issue_purchase_order'
    RECEIVE_PURCHASE_ORDER = 'receive_purchase_order'
    CANCEL_PURCHASE_ORDER = 'cancel_purchase_order'
    
    # Purchase Order Line Item Permissions
    CREATE_PURCHASE_ORDER_LINE_ITEM = 'create_purchase_order_line_item'
    READ_PURCHASE_ORDER_LINE_ITEM = 'read_purchase_order_line_item'
    UPDATE_PURCHASE_ORDER_LINE_ITEM = 'update_purchase_order_line_item'
    DELETE_PURCHASE_ORDER_LINE_ITEM = 'delete_purchase_order_line_item'
    
    # Stock Item Permissions
    CREATE_STOCK_ITEM = 'create_stock_item'
    READ_STOCK_ITEM = 'read_stock_item'
    UPDATE_STOCK_ITEM = 'update_stock_item'
    DELETE_STOCK_ITEM = 'delete_stock_item'
    TRANSFER_STOCK_ITEM = 'transfer_stock_item'
    ADJUST_STOCK_ITEM_QUANTITY = 'adjust_stock_item_quantity'
    VIEW_STOCK_ITEM_HISTORY = 'view_stock_item_history'
    
    # Stock Location Permissions
    CREATE_STOCK_LOCATION = 'create_stock_location'
    READ_STOCK_LOCATION = 'read_stock_location'
    UPDATE_STOCK_LOCATION = 'update_stock_location'
    DELETE_STOCK_LOCATION = 'delete_stock_location'
    
    # Return Order Permissions
    CREATE_RETURN_ORDER = 'create_return_order'
    READ_RETURN_ORDER = 'read_return_order'
    UPDATE_RETURN_ORDER = 'update_return_order'
    DELETE_RETURN_ORDER = 'delete_return_order'
    APPROVE_RETURN_ORDER = 'approve_return_order'
    PROCESS_RETURN_ORDER = 'process_return_order'
    
    # Sales Order Permissions
    CREATE_SALES_ORDER = 'create_sales_order'
    READ_SALES_ORDER = 'read_sales_order'
    UPDATE_SALES_ORDER = 'update_sales_order'
    DELETE_SALES_ORDER = 'delete_sales_order'
    APPROVE_SALES_ORDER = 'approve_sales_order'
    FULFILL_SALES_ORDER = 'fulfill_sales_order'

INVENTORY_PERMISSIONS = {
    'list': CombinedPermissions.READ_INVENTORY,
    'retrieve': CombinedPermissions.READ_INVENTORY,
    'create': CombinedPermissions.CREATE_INVENTORY,
    'update': CombinedPermissions.UPDATE_INVENTORY,
    'partial_update': CombinedPermissions.UPDATE_INVENTORY,
    'destroy': CombinedPermissions.DELETE_INVENTORY,
    'low_stock': CombinedPermissions.READ_INVENTORY,
    'needs_reorder': CombinedPermissions.READ_INVENTORY,
    'analytics': CombinedPermissions.VIEW_INVENTORY_REPORTS,
    'adjust_stock': CombinedPermissions.ADJUST_STOCK_ITEM_QUANTITY,
}

INVENTORY_CATEGORY_PERMISSIONS = {
    'list': CombinedPermissions.READ_INVENTORY_CATEGORY,
    'retrieve': CombinedPermissions.READ_INVENTORY_CATEGORY,
    'create': CombinedPermissions.CREATE_INVENTORY_CATEGORY,
    'update': CombinedPermissions.UPDATE_INVENTORY_CATEGORY,
    'partial_update': CombinedPermissions.UPDATE_INVENTORY_CATEGORY,
    'destroy': CombinedPermissions.DELETE_INVENTORY_CATEGORY,
    'tree': CombinedPermissions.READ_INVENTORY_CATEGORY,
    'children': CombinedPermissions.READ_INVENTORY_CATEGORY,
    'inventories': CombinedPermissions.READ_INVENTORY,
}

STOCK_ITEM_PERMISSIONS = {
    'list': CombinedPermissions.READ_STOCK_ITEM,
    'retrieve': CombinedPermissions.READ_STOCK_ITEM,
    'create': CombinedPermissions.CREATE_STOCK_ITEM,
    'update': CombinedPermissions.UPDATE_STOCK_ITEM,
    'partial_update': CombinedPermissions.UPDATE_STOCK_ITEM,
    'destroy': CombinedPermissions.DELETE_STOCK_ITEM,
    'update_status': CombinedPermissions.UPDATE_STOCK_ITEM,
    'tracking_history': CombinedPermissions.VIEW_STOCK_ITEM_HISTORY,
    'analytics': CombinedPermissions.VIEW_INVENTORY_REPORTS,
}

STOCK_LOCATION_PERMISSIONS = {
    'list': CombinedPermissions.READ_STOCK_LOCATION,
    'retrieve': CombinedPermissions.READ_STOCK_LOCATION,
    'create': CombinedPermissions.CREATE_STOCK_LOCATION,
    'update': CombinedPermissions.UPDATE_STOCK_LOCATION,
    'partial_update': CombinedPermissions.UPDATE_STOCK_LOCATION,
    'destroy': CombinedPermissions.DELETE_STOCK_LOCATION,
    'stock_items': CombinedPermissions.READ_STOCK_ITEM,
    'transfer_stock': CombinedPermissions.TRANSFER_STOCK_ITEM,
}

PURCHASE_ORDER_PERMISSIONS = {
    'list': CombinedPermissions.READ_PURCHASE_ORDER,
    'retrieve': CombinedPermissions.READ_PURCHASE_ORDER,
    'create': CombinedPermissions.CREATE_PURCHASE_ORDER,
    'update': CombinedPermissions.UPDATE_PURCHASE_ORDER,
    'partial_update': CombinedPermissions.UPDATE_PURCHASE_ORDER,
    'destroy': CombinedPermissions.DELETE_PURCHASE_ORDER,
    'approve': CombinedPermissions.APPROVE_PURCHASE_ORDER,
    'receive_items': CombinedPermissions.RECEIVE_PURCHASE_ORDER,
    'add_line_item': CombinedPermissions.CREATE_PURCHASE_ORDER_LINE_ITEM,
    'analytics': CombinedPermissions.VIEW_INVENTORY_REPORTS,
}

UNIFIED_PERMISSION_DICT= {
    'inventory':INVENTORY_PERMISSIONS,
    'inventory_category':INVENTORY_CATEGORY_PERMISSIONS,
    'stock_item':STOCK_ITEM_PERMISSIONS,
    'stock_location':STOCK_LOCATION_PERMISSIONS,
    'purchase_order':PURCHASE_ORDER_PERMISSIONS

}