from subapps.kafka.consumers.catalog import (
    handle_catalog_product_event,
    handle_catalog_variant_event,
)
from subapps.kafka.consumers.identity import (
    handle_identity_company_profile_event,
    handle_identity_membership_event,
    handle_identity_user_event,
)
from subapps.kafka.consumers.pos import handle_pos_order_event
from subapps.kafka.topics import (
    CATALOG_PRODUCT_TOPIC,
    CATALOG_VARIANT_TOPIC,
    IDENTITY_COMPANY_PROFILE_TOPIC,
    IDENTITY_MEMBERSHIP_TOPIC,
    IDENTITY_USER_TOPIC,
    POS_ORDER_TOPIC,
)

EVENT_HANDLERS = {
    IDENTITY_USER_TOPIC: handle_identity_user_event,
    IDENTITY_COMPANY_PROFILE_TOPIC: handle_identity_company_profile_event,
    IDENTITY_MEMBERSHIP_TOPIC: handle_identity_membership_event,
    CATALOG_PRODUCT_TOPIC: handle_catalog_product_event,
    CATALOG_VARIANT_TOPIC: handle_catalog_variant_event,
    POS_ORDER_TOPIC: handle_pos_order_event,
}
