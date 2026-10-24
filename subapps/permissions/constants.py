from django.utils.translation import gettext_lazy as _

class CombinedPermissions:
    """
    Permission constants for the inventory microservice
    Mirrors the permissions from the user microservice
    """
    
    # Inventory Item Permissions
    # Inventory rows are stock items in the Users permission catalogue.
    CREATE_INVENTORY_ITEM = 'create_stock_item'
    READ_INVENTORY_ITEM = 'read_stock_item'
    UPDATE_INVENTORY_ITEM = 'update_stock_item'
    DELETE_INVENTORY_ITEM = 'delete_stock_item'
    APPROVE_INVENTORY_ITEM = 'approve_stock_item'
    REJECT_INVENTORY_ITEM = 'reject_stock_item'
    ARCHIVE_INVENTORY_ITEM = 'archive_inventory'
    RESTORE_INVENTORY_ITEM = 'restore_inventory'
    MANAGE_INVENTORY_ITEM_SETTINGS = 'manage_inventory_settings'
    VIEW_INVENTORY_ITEM_REPORTS = 'view_inventory_reports'
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
    
    # Inventory Item Stock Operation Permissions
    TRANSFER_INVENTORY_ITEM = 'transfer_stock_item'
    ADJUST_INVENTORY_ITEM_QUANTITY = 'adjust_stock_item_quantity'
    VIEW_INVENTORY_ITEM_HISTORY = 'view_stock_item_history'
    
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
    READ_MANUFACTURING = 'read_manufacturing'
    MANAGE_MANUFACTURING = 'manage_manufacturing'
    MANAGE_WORK_CENTERS = 'manage_work_centers'
    MANAGE_QUALITY = 'manage_quality'
    MANAGE_WASTE_COSTING = 'manage_waste_costing'
    VIEW_PROFITABILITY_REPORTS = 'view_profitability_reports'
    MANAGE_ACCOUNTING_PERIODS = 'manage_accounting_periods'
    MANAGE_WAREHOUSE_SETTINGS = 'manage_warehouse_settings'
    VIEW_WAREHOUSE_REPORTS = 'view_warehouse_reports'

INVENTORY_ITEM_PERMISSIONS = {
    'list': CombinedPermissions.READ_INVENTORY_ITEM,
    'retrieve': CombinedPermissions.READ_INVENTORY_ITEM,
    'create': CombinedPermissions.CREATE_INVENTORY_ITEM,
    'update': CombinedPermissions.UPDATE_INVENTORY_ITEM,
    'partial_update': CombinedPermissions.UPDATE_INVENTORY_ITEM,
    'destroy': CombinedPermissions.DELETE_INVENTORY_ITEM,
    'low_stock': CombinedPermissions.READ_INVENTORY_ITEM,
    'needs_reorder': CombinedPermissions.READ_INVENTORY_ITEM,
    'analytics': CombinedPermissions.VIEW_INVENTORY_ITEM_REPORTS,
    'adjust_stock': CombinedPermissions.ADJUST_INVENTORY_ITEM_QUANTITY,
    'bulk_update_controls': CombinedPermissions.MANAGE_INVENTORY_ITEM_SETTINGS,
    'minimal_item': CombinedPermissions.READ_INVENTORY_ITEM,
    'stock_summary': CombinedPermissions.READ_INVENTORY_ITEM,
    'update_status': CombinedPermissions.UPDATE_INVENTORY_ITEM,
    'tracking_history': CombinedPermissions.VIEW_INVENTORY_ITEM_HISTORY,
    'expiring_soon': CombinedPermissions.READ_INVENTORY_ITEM,
    'create_for_variants': CombinedPermissions.CREATE_INVENTORY_ITEM,
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
    'items': CombinedPermissions.READ_INVENTORY_ITEM,
}

STOCK_LOCATION_PERMISSIONS = {
    'list': CombinedPermissions.READ_STOCK_LOCATION,
    'retrieve': CombinedPermissions.READ_STOCK_LOCATION,
    'create': CombinedPermissions.CREATE_STOCK_LOCATION,
    'update': CombinedPermissions.UPDATE_STOCK_LOCATION,
    'partial_update': CombinedPermissions.UPDATE_STOCK_LOCATION,
    'destroy': CombinedPermissions.DELETE_STOCK_LOCATION,
    'inventory_items': CombinedPermissions.READ_INVENTORY_ITEM,
    'transfer_stock': CombinedPermissions.TRANSFER_INVENTORY_ITEM,
    'warehouse_report': CombinedPermissions.VIEW_WAREHOUSE_REPORTS,
}

STOCK_RESERVATION_PERMISSIONS = {
    'list': CombinedPermissions.READ_INVENTORY_ITEM,
    'retrieve': CombinedPermissions.READ_INVENTORY_ITEM,
    'create': CombinedPermissions.UPDATE_INVENTORY_ITEM,
    'release': CombinedPermissions.UPDATE_INVENTORY_ITEM,
    'fulfill': CombinedPermissions.UPDATE_INVENTORY_ITEM,
}

STOCK_LOT_PERMISSIONS = {
    'list': CombinedPermissions.READ_INVENTORY_ITEM,
    'retrieve': CombinedPermissions.READ_INVENTORY_ITEM,
    'set_status': CombinedPermissions.UPDATE_INVENTORY_ITEM,
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
    'analytics': CombinedPermissions.VIEW_INVENTORY_ITEM_REPORTS,
    
}

RETURN_ORDER_PERMISSIONS = {
    'list': CombinedPermissions.READ_RETURN_ORDER,
    'retrieve': CombinedPermissions.READ_RETURN_ORDER,
    'create': CombinedPermissions.UPDATE_RETURN_ORDER,
    'dispatch': CombinedPermissions.PROCESS_RETURN_ORDER,
    'complete': CombinedPermissions.PROCESS_RETURN_ORDER,
    'cancel': CombinedPermissions.UPDATE_RETURN_ORDER,
}

SALES_ORDER_PERMISSIONS = {
    'list': CombinedPermissions.READ_SALES_ORDER,
    'retrieve': CombinedPermissions.READ_SALES_ORDER,
    'create': CombinedPermissions.CREATE_SALES_ORDER,
    'update': CombinedPermissions.UPDATE_SALES_ORDER,
    'partial_update': CombinedPermissions.UPDATE_SALES_ORDER,
    'destroy': CombinedPermissions.DELETE_SALES_ORDER,
    'line_items': CombinedPermissions.READ_SALES_ORDER,
    'shipments': CombinedPermissions.READ_SALES_ORDER,
    'add_line_item': CombinedPermissions.UPDATE_SALES_ORDER,
    'update_line_item': CombinedPermissions.UPDATE_SALES_ORDER,
    'remove_line_item': CombinedPermissions.UPDATE_SALES_ORDER,
    'reserve': CombinedPermissions.FULFILL_SALES_ORDER,
    'release': CombinedPermissions.FULFILL_SALES_ORDER,
    'ship': CombinedPermissions.FULFILL_SALES_ORDER,
    'complete': CombinedPermissions.FULFILL_SALES_ORDER,
    'cancel': CombinedPermissions.UPDATE_SALES_ORDER,
}

MANUFACTURING_PERMISSIONS = {
    'list': CombinedPermissions.READ_MANUFACTURING,
    'retrieve': CombinedPermissions.READ_MANUFACTURING,
    'create': CombinedPermissions.MANAGE_MANUFACTURING,
    'update': CombinedPermissions.MANAGE_MANUFACTURING,
    'partial_update': CombinedPermissions.MANAGE_MANUFACTURING,
    'destroy': CombinedPermissions.MANAGE_MANUFACTURING,
    'release': CombinedPermissions.MANAGE_MANUFACTURING,
    'start': CombinedPermissions.MANAGE_MANUFACTURING,
    'complete': CombinedPermissions.MANAGE_MANUFACTURING,
    'cancel': CombinedPermissions.MANAGE_MANUFACTURING,
    'record_waste': CombinedPermissions.MANAGE_WASTE_COSTING,
    'inspect': CombinedPermissions.MANAGE_QUALITY,
    'resolve_inspection': CombinedPermissions.MANAGE_QUALITY,
    'cost_summary': CombinedPermissions.VIEW_PROFITABILITY_REPORTS,
    'traceability': CombinedPermissions.READ_MANUFACTURING,
    'operations': CombinedPermissions.READ_MANUFACTURING,
    'requirements': CombinedPermissions.READ_MANUFACTURING,
}

WORK_CENTER_PERMISSIONS = {
    'list': CombinedPermissions.READ_MANUFACTURING,
    'retrieve': CombinedPermissions.READ_MANUFACTURING,
    'create': CombinedPermissions.MANAGE_WORK_CENTERS,
    'update': CombinedPermissions.MANAGE_WORK_CENTERS,
    'partial_update': CombinedPermissions.MANAGE_WORK_CENTERS,
    'destroy': CombinedPermissions.MANAGE_WORK_CENTERS,
    'blocks': CombinedPermissions.MANAGE_WORK_CENTERS,
}

WAREHOUSE_SETTINGS_PERMISSIONS = {
    'list': CombinedPermissions.VIEW_WAREHOUSE_REPORTS,
    'retrieve': CombinedPermissions.VIEW_WAREHOUSE_REPORTS,
    'create': CombinedPermissions.MANAGE_WAREHOUSE_SETTINGS,
    'update': CombinedPermissions.MANAGE_WAREHOUSE_SETTINGS,
    'partial_update': CombinedPermissions.MANAGE_WAREHOUSE_SETTINGS,
    'destroy': CombinedPermissions.MANAGE_WAREHOUSE_SETTINGS,
    'warehouse_report': CombinedPermissions.VIEW_WAREHOUSE_REPORTS,
}

UNIFIED_PERMISSION_DICT= {
    'inventory_item':INVENTORY_ITEM_PERMISSIONS,
    'inventory_category':INVENTORY_CATEGORY_PERMISSIONS,
    'stock_location':STOCK_LOCATION_PERMISSIONS,
    'stock_reservation':STOCK_RESERVATION_PERMISSIONS,
    'stock_lot': STOCK_LOT_PERMISSIONS,
    'purchase_order':PURCHASE_ORDER_PERMISSIONS,
    'return_order':RETURN_ORDER_PERMISSIONS,
    'sales_order':SALES_ORDER_PERMISSIONS,
    'manufacturing': MANUFACTURING_PERMISSIONS,
    'work_center': WORK_CENTER_PERMISSIONS,
    'warehouse_settings': WAREHOUSE_SETTINGS_PERMISSIONS,

}
