from decimal import Decimal

from django_filters.rest_framework import DjangoFilterBackend
from django.db import transaction
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from mainapps.stock.models import StockLocation
from subapps.kafka.producers.inventory_admin import (
    publish_inventory_admin_event,
    serialize_inventory_category,
    serialize_inventory_item,
)
from subapps.permissions.constants import UNIFIED_PERMISSION_DICT
from subapps.permissions.microservice_permissions import BaseCachePermissionViewset
from subapps.services.inventory_read_model import (
    get_inventory_ids_for_stock_filter,
    get_inventory_item_summary_map,
    get_low_stock_rows,
    get_profile_stock_analytics,
)
from subapps.services.location_scope import (
    ensure_inventory_item_placement,
    get_location_scope_ids,
    get_location_scope_ids_for_locations,
    resolve_structural_location,
    resolve_structural_locations,
)
from subapps.services.stock_domain import StockDomainError, StockDomainService
from subapps.utils.request_context import (
    get_request_profile_id,
    get_request_user_id,
    scope_queryset_by_identity,
)

from .models import InventoryCategory, InventoryItem
from .serializers import (
    InventoryCategoryDetailSerializer,
    InventoryCategoryListSerializer,
    InventoryDetailSerializer,
    InventorySetupSummarySerializer,
    InventoryListSerializer,
)


def get_inventory_setup_summary(*, profile_id, stock_location=None, stock_locations=None):
    category_queryset = scope_queryset_by_identity(
        InventoryCategory.objects.all(),
        canonical_field='profile_id',
        legacy_field='profile',
        value=profile_id,
    )
    location_queryset = scope_queryset_by_identity(
        StockLocation.objects.all(),
        canonical_field='profile_id',
        legacy_field='profile',
        value=profile_id,
    )
    inventory_queryset = scope_queryset_by_identity(
        InventoryItem.objects.all(),
        canonical_field='profile_id',
        legacy_field='profile',
        value=profile_id,
    )
    if stock_location is not None or stock_locations:
        scoped_location_ids = get_location_scope_ids_for_locations(
            profile_id=profile_id,
            stock_locations=stock_locations or ([stock_location] if stock_location is not None else []),
        )
        inventory_queryset = (
            inventory_queryset.filter(stock_balances__stock_location_id__in=scoped_location_ids).distinct()
            if scoped_location_ids
            else inventory_queryset.none()
        )
    stock_analytics = get_profile_stock_analytics(
        profile_id=profile_id,
        stock_location=stock_location,
        stock_locations=stock_locations,
    )

    return {
        'total_locations': location_queryset.count(),
        'total_categories': category_queryset.count(),
        'total_inventory_items': inventory_queryset.count(),
        'total_stock_value': stock_analytics.get('total_stock_value', Decimal('0')),
        'low_stock_count': len(
            get_low_stock_rows(
                inventory_queryset,
                stock_location=stock_location,
                stock_locations=stock_locations,
            )
        ),
    }


_CONTROL_FIELDS = (
    'minimum_stock_level',
    'safety_stock_level',
    'reorder_point',
    'reorder_quantity',
    'track_lot',
    'track_serial',
    'track_expiry',
    'allow_negative_stock',
)


def _resolve_structural_scope_location_from_request(request, *, profile_id):
    raw_location_id = (
        request.data.get('structural_location_id')
        or request.query_params.get('structural_location_id')
        or request.query_params.get('stock_location_id')
    )
    if not raw_location_id:
        return None
    return resolve_structural_location(profile_id=profile_id, stock_location_id=raw_location_id)


def _all_replenishment_thresholds_zero(inventory_item):
    return all(
        Decimal(str(getattr(inventory_item, field, 0) or 0)) == Decimal('0')
        for field in ('minimum_stock_level', 'safety_stock_level', 'reorder_point', 'reorder_quantity')
    )


def _bulk_control_result(*, inventory_item, updated_fields=None, skipped=False, skip_reason=None):
    return {
        'inventory_item_id': str(inventory_item.id),
        'name': inventory_item.name_snapshot,
        'inventory_type': inventory_item.inventory_type,
        'updated_fields': updated_fields or [],
        'skipped': skipped,
        'skip_reason': skip_reason,
        'minimum_stock_level': inventory_item.minimum_stock_level,
        'safety_stock_level': inventory_item.safety_stock_level,
        'reorder_point': inventory_item.reorder_point,
        'reorder_quantity': inventory_item.reorder_quantity,
        'track_lot': inventory_item.track_lot,
        'track_serial': inventory_item.track_serial,
        'track_expiry': inventory_item.track_expiry,
        'allow_negative_stock': inventory_item.allow_negative_stock,
    }


def _audit_actor_from_request(request) -> dict[str, str]:
    return {
        'user_id': str(get_request_user_id(request, required=True) or '').strip(),
    }


class BaseInventoryViewSet(BaseCachePermissionViewset):
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]

    def get_queryset(self):
        queryset = super().get_queryset()
        profile_id = get_request_profile_id(self.request, as_str=False)
        if profile_id:
            queryset = scope_queryset_by_identity(
                queryset,
                canonical_field='profile_id',
                legacy_field='profile',
                value=profile_id,
            )
        return queryset

    def perform_create(self, serializer):
        serializer.save(
            profile_id=get_request_profile_id(self.request, required=True, as_str=False),
            created_by_user_id=get_request_user_id(self.request, required=True, as_str=False),
            updated_by_user_id=get_request_user_id(self.request, as_str=False),
        )

    def perform_update(self, serializer):
        serializer.save(updated_by_user_id=get_request_user_id(self.request, as_str=False))


class InventoryCategoryViewSet(BaseInventoryViewSet):
    required_permission = UNIFIED_PERMISSION_DICT.get('inventory_category')
    queryset = InventoryCategory.objects.all()
    filterset_fields = ['is_active', 'structural', 'parent']
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'created_at']
    ordering = ['name']
    serializer_class = InventoryCategoryDetailSerializer

    def perform_create(self, serializer):
        super().perform_create(serializer)
        payload = serialize_inventory_category(serializer.instance)
        publish_inventory_admin_event(
            event_name='inventory.category.created',
            payload=payload,
            actor=_audit_actor_from_request(self.request),
            target={
                'type': 'inventory_category',
                'id': payload['inventory_category_id'],
                'label': payload['name'],
            },
            summary=f"Inventory category created: {payload['name']}.",
            metadata={
                'structural': payload['structural'],
                'is_active': payload['is_active'],
                'parent_name': payload['parent_name'],
            },
            after=payload,
            feature_area='inventory_master',
        )

    def perform_update(self, serializer):
        before = serialize_inventory_category(self.get_object())
        super().perform_update(serializer)
        payload = serialize_inventory_category(serializer.instance)
        publish_inventory_admin_event(
            event_name='inventory.category.updated',
            payload=payload,
            actor=_audit_actor_from_request(self.request),
            target={
                'type': 'inventory_category',
                'id': payload['inventory_category_id'],
                'label': payload['name'],
            },
            summary=f"Inventory category updated: {payload['name']}.",
            metadata={
                'structural': payload['structural'],
                'is_active': payload['is_active'],
                'parent_name': payload['parent_name'],
            },
            before=before,
            after=payload,
            feature_area='inventory_master',
        )

    def destroy(self, request, *args, **kwargs):
        category = self.get_object()
        before = serialize_inventory_category(category)
        response = super().destroy(request, *args, **kwargs)
        publish_inventory_admin_event(
            event_name='inventory.category.deleted',
            payload=before,
            actor=_audit_actor_from_request(request),
            target={
                'type': 'inventory_category',
                'id': before['inventory_category_id'],
                'label': before['name'],
            },
            summary=f"Inventory category deleted: {before['name']}.",
            metadata={
                'structural': before['structural'],
                'parent_name': before['parent_name'],
            },
            before=before,
            after={},
            severity='warning',
            feature_area='inventory_master',
        )
        return response

    @action(detail=False, methods=['get'])
    def tree(self, request):
        categories = self.get_queryset().filter(parent__isnull=True)
        serializer = InventoryCategoryDetailSerializer(categories, many=True, context=self.get_serializer_context())
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def children(self, request, pk=None):
        category = self.get_object()
        serializer = InventoryCategoryListSerializer(category.children.all(), many=True, context=self.get_serializer_context())
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def items(self, request, pk=None):
        category = self.get_object()
        queryset = category.inventory_items.all().order_by('-created_at')
        summary_map = get_inventory_item_summary_map(list(queryset))
        serializer = InventoryListSerializer(
            queryset,
            many=True,
            context={**self.get_serializer_context(), 'inventory_item_summary_map': summary_map},
        )
        return Response(serializer.data)


class InventoryItemViewSet(BaseInventoryViewSet):
    required_permission = UNIFIED_PERMISSION_DICT.get('inventory_item')
    queryset = InventoryItem.objects.select_related('inventory_category', 'default_supplier')
    filterset_fields = ['status', 'inventory_type', 'inventory_category', 'default_supplier']
    search_fields = ['name_snapshot', 'description', 'sku_snapshot', 'barcode_snapshot']
    ordering_fields = ['name_snapshot', 'created_at', 'minimum_stock_level', 'reorder_point']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return InventoryListSerializer
        return InventoryDetailSerializer

    def _get_requested_structural_location(self):
        profile_id = get_request_profile_id(self.request, as_str=False)
        if not profile_id:
            return None
        location_id = self.request.query_params.get('structural_location_id') or self.request.query_params.get('stock_location_id')
        if not location_id:
            return None
        return resolve_structural_location(profile_id=profile_id, stock_location_id=location_id)

    def _get_requested_structural_locations(self):
        profile_id = get_request_profile_id(self.request, as_str=False)
        if not profile_id:
            return []

        raw_ids: list[str] = []
        for key in ('structural_location_ids', 'stock_location_ids'):
            raw_ids.extend(self.request.query_params.getlist(key))
        for key in ('structural_location_ids', 'stock_location_ids'):
            csv_value = self.request.query_params.get(key)
            if csv_value:
                raw_ids.extend([part.strip() for part in csv_value.split(',') if part.strip()])

        singular_location = self._get_requested_structural_location()
        if singular_location is not None:
            return [singular_location] + [
                location
                for location in resolve_structural_locations(profile_id=profile_id, stock_location_ids=raw_ids)
                if location.id != singular_location.id
            ]

        return resolve_structural_locations(profile_id=profile_id, stock_location_ids=raw_ids)

    def _get_scope_mode(self):
        value = (self.request.query_params.get('scope') or '').strip().lower()
        if value == 'all':
            return 'all_locations'
        return value

    def _get_inventory_scope(self):
        scope_mode = self._get_scope_mode()
        structural_locations = self._get_requested_structural_locations()
        if scope_mode == 'all_locations':
            return None, []
        if structural_locations:
            return structural_locations[0], structural_locations
        return None, []

    def get_serializer_context(self):
        context = super().get_serializer_context()
        stock_location, stock_locations = self._get_inventory_scope()
        queryset = list(self.filter_queryset(self.get_queryset())) if self.action in {'list', 'low_stock', 'needs_reorder'} else []
        if self.action in {'retrieve', 'minimal_item', 'stock_summary'} and getattr(self, 'kwargs', {}).get('pk'):
            try:
                queryset = [self.get_object()]
            except Exception:
                queryset = []
        context['inventory_item_summary_map'] = (
            get_inventory_item_summary_map(
                queryset,
                stock_location=stock_location,
                stock_locations=stock_locations,
            )
            if queryset else {}
        )
        return context

    def get_queryset(self):
        queryset = super().get_queryset()
        _, stock_locations = self._get_inventory_scope()
        stock_status = self.request.query_params.get('stock_status')
        if stock_locations:
            scoped_location_ids = get_location_scope_ids_for_locations(
                profile_id=get_request_profile_id(self.request, required=True, as_str=False),
                stock_locations=stock_locations,
            )
            queryset = (
                queryset.filter(stock_balances__stock_location_id__in=scoped_location_ids).distinct()
                if scoped_location_ids
                else queryset.none()
            )
        if stock_status:
            stock_location, stock_locations = self._get_inventory_scope()
            summary_map = get_inventory_item_summary_map(
                list(queryset),
                stock_location=stock_location,
                stock_locations=stock_locations,
            )
            matching_ids = []
            for item in queryset:
                summary = summary_map.get(item.id, {})
                quantity = Decimal(str(summary.get('quantity', 0)))
                minimum_stock_level = Decimal(str(item.minimum_stock_level or 0))
                reorder_point = Decimal(str(item.reorder_point or 0))
                if stock_status == 'low_stock' and minimum_stock_level > 0 and Decimal('0') < quantity <= minimum_stock_level:
                    matching_ids.append(item.id)
                elif stock_status == 'needs_reorder' and reorder_point > 0 and quantity <= reorder_point:
                    matching_ids.append(item.id)
                elif stock_status == 'out_of_stock' and quantity <= 0:
                    matching_ids.append(item.id)
            queryset = queryset.filter(id__in=matching_ids) if matching_ids else queryset.none()
        return queryset

    def _get_stock_filtered_queryset(self, filter_name: str):
        queryset = self.get_queryset()
        stock_location, stock_locations = self._get_inventory_scope()
        matching_ids = get_inventory_ids_for_stock_filter(
            list(queryset),
            filter_name=filter_name,
            stock_location=stock_location,
            stock_locations=stock_locations,
        )
        return queryset.filter(id__in=matching_ids) if matching_ids else queryset.none()

    def perform_create(self, serializer):
        profile_id = get_request_profile_id(self.request, required=True, as_str=False)
        user_id = get_request_user_id(self.request, required=True, as_str=False)
        inventory_item = serializer.save(
            profile_id=profile_id,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        ensure_inventory_item_placement(
            inventory_item,
            stock_location_id=self.request.data.get('stock_location_id') or self.request.data.get('structural_location_id'),
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        payload = serialize_inventory_item(inventory_item)
        publish_inventory_admin_event(
            event_name='inventory.item.created',
            payload=payload,
            actor=_audit_actor_from_request(self.request),
            target={
                'type': 'inventory_item',
                'id': payload['inventory_item_id'],
                'label': payload['name_snapshot'],
                'barcode': payload['barcode_snapshot'],
                'sku': payload['sku_snapshot'],
            },
            summary=f"Inventory item created: {payload['name_snapshot']}.",
            metadata={
                'inventory_type': payload['inventory_type'],
                'status': payload['status'],
                'category_name': payload['inventory_category_name'],
            },
            after=payload,
            feature_area='inventory_master',
            reference_number=payload['sku_snapshot'] or payload['barcode_snapshot'],
            notification_category='stock_alert',
            notification_title=f"Inventory item {payload['name_snapshot']} created",
            notification_message=f"Inventory item {payload['name_snapshot']} was added to the workspace catalog.",
            notification_action_url='/inventory',
        )

    def perform_update(self, serializer):
        before = serialize_inventory_item(self.get_object())
        user_id = get_request_user_id(self.request, as_str=False)
        inventory_item = serializer.save(updated_by_user_id=user_id)
        if 'stock_location_id' in self.request.data or 'structural_location_id' in self.request.data:
            ensure_inventory_item_placement(
                inventory_item,
                stock_location_id=self.request.data.get('stock_location_id') or self.request.data.get('structural_location_id'),
                updated_by_user_id=user_id,
            )
        payload = serialize_inventory_item(inventory_item)
        publish_inventory_admin_event(
            event_name='inventory.item.updated',
            payload=payload,
            actor=_audit_actor_from_request(self.request),
            target={
                'type': 'inventory_item',
                'id': payload['inventory_item_id'],
                'label': payload['name_snapshot'],
                'barcode': payload['barcode_snapshot'],
                'sku': payload['sku_snapshot'],
            },
            summary=f"Inventory item updated: {payload['name_snapshot']}.",
            metadata={
                'inventory_type': payload['inventory_type'],
                'status': payload['status'],
                'category_name': payload['inventory_category_name'],
            },
            before=before,
            after=payload,
            feature_area='inventory_master',
            reference_number=payload['sku_snapshot'] or payload['barcode_snapshot'],
        )

    def destroy(self, request, *args, **kwargs):
        inventory_item = self.get_object()
        before = serialize_inventory_item(inventory_item)
        response = super().destroy(request, *args, **kwargs)
        publish_inventory_admin_event(
            event_name='inventory.item.deleted',
            payload=before,
            actor=_audit_actor_from_request(request),
            target={
                'type': 'inventory_item',
                'id': before['inventory_item_id'],
                'label': before['name_snapshot'],
                'barcode': before['barcode_snapshot'],
                'sku': before['sku_snapshot'],
            },
            summary=f"Inventory item deleted: {before['name_snapshot']}.",
            metadata={
                'inventory_type': before['inventory_type'],
                'status': before['status'],
                'category_name': before['inventory_category_name'],
            },
            before=before,
            after={},
            severity='warning',
            feature_area='inventory_master',
            reference_number=before['sku_snapshot'] or before['barcode_snapshot'],
        )
        return response

    @action(detail=False, methods=['get'])
    def low_stock(self, request):
        queryset = self._get_stock_filtered_queryset('low_stock')
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def needs_reorder(self, request):
        queryset = self._get_stock_filtered_queryset('needs_reorder')
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        profile_id = get_request_profile_id(request, required=True, as_str=False)
        stock_location, stock_locations = self._get_inventory_scope()
        serializer = InventorySetupSummarySerializer(
            get_inventory_setup_summary(
                profile_id=profile_id,
                stock_location=stock_location,
                stock_locations=stock_locations,
            ),
            context=self.get_serializer_context(),
        )
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def bulk_update_controls(self, request):
        updates = request.data.get('updates')
        if not isinstance(updates, list) or not updates:
            return Response({'error': 'updates must be a non-empty list'}, status=status.HTTP_400_BAD_REQUEST)

        base_queryset = self.get_queryset()
        actor_user_id = get_request_user_id(request, as_str=False)
        seen_item_ids = set()
        updated_count = 0
        skipped_count = 0
        results = []

        for index, raw_update in enumerate(updates):
            if not isinstance(raw_update, dict):
                return Response(
                    {'error': f'updates[{index}] must be an object'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            controls = {
                field: raw_update[field]
                for field in _CONTROL_FIELDS
                if field in raw_update and raw_update[field] is not None
            }
            if not controls:
                return Response(
                    {'error': f'updates[{index}] must include at least one control field'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if controls.get('allow_negative_stock') is True:
                return Response(
                    {'error': 'allow_negative_stock cannot be enabled through bulk_update_controls'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            queryset = base_queryset
            raw_ids = raw_update.get('inventory_item_ids') or []
            if raw_ids:
                queryset = queryset.filter(id__in=raw_ids)
            inventory_type = raw_update.get('inventory_type')
            if inventory_type:
                queryset = queryset.filter(inventory_type=inventory_type)
            if not raw_ids and not inventory_type:
                return Response(
                    {'error': f'updates[{index}] must target inventory_item_ids or inventory_type'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            only_if_all_thresholds_zero = bool(raw_update.get('only_if_all_thresholds_zero'))
            reason = str(raw_update.get('reason') or '').strip()

            with transaction.atomic():
                for inventory_item in queryset.order_by('name_snapshot', 'id'):
                    if inventory_item.id in seen_item_ids:
                        continue
                    seen_item_ids.add(inventory_item.id)

                    if only_if_all_thresholds_zero and not _all_replenishment_thresholds_zero(inventory_item):
                        skipped_count += 1
                        results.append(
                            _bulk_control_result(
                                inventory_item=inventory_item,
                                skipped=True,
                                skip_reason='Replenishment thresholds are already set.',
                            )
                        )
                        continue

                    before = serialize_inventory_item(inventory_item)
                    for field, value in controls.items():
                        setattr(inventory_item, field, value)
                    metadata = dict(inventory_item.metadata or {})
                    metadata['bulk_inventory_control_update'] = {
                        'reason': reason or None,
                        'source': 'inventory.items.bulk_update_controls',
                    }
                    inventory_item.metadata = metadata
                    inventory_item.updated_by_user_id = actor_user_id
                    save_fields = list(controls.keys()) + ['metadata', 'updated_by_user_id', 'updated_at']
                    inventory_item.save(update_fields=save_fields)
                    updated_count += 1
                    after = serialize_inventory_item(inventory_item)
                    publish_inventory_admin_event(
                        event_name='inventory.item.controls.bulk_updated',
                        payload=after,
                        actor=_audit_actor_from_request(request),
                        target={
                            'type': 'inventory_item',
                            'id': after['inventory_item_id'],
                            'label': after['name_snapshot'],
                            'barcode': after['barcode_snapshot'],
                            'sku': after['sku_snapshot'],
                        },
                        summary=f"Inventory controls bulk-updated for {after['name_snapshot']}.",
                        metadata={
                            'reason': reason,
                            'updated_fields': list(controls.keys()),
                        },
                        before=before,
                        after=after,
                        feature_area='inventory_policy',
                        reference_number=after['sku_snapshot'] or after['barcode_snapshot'],
                    )
                    results.append(
                        _bulk_control_result(
                            inventory_item=inventory_item,
                            updated_fields=list(controls.keys()),
                        )
                    )

        return Response(
            {
                'updated_count': updated_count,
                'skipped_count': skipped_count,
                'results': results,
            }
        )

    @action(detail=True, methods=['get'])
    def stock_summary(self, request, pk=None):
        inventory_item = self.get_object()
        stock_location, stock_locations = self._get_inventory_scope()
        summary = get_inventory_item_summary_map(
            [inventory_item],
            stock_location=stock_location,
            stock_locations=stock_locations,
        ).get(inventory_item.id, {})
        return Response({
            'total_quantity': summary.get('quantity', Decimal('0')),
            'quantity_reserved': summary.get('quantity_reserved', Decimal('0')),
            'quantity_available': summary.get('quantity_available', Decimal('0')),
            'total_locations': summary.get('location_count', 0),
            'avg_purchase_price': summary.get('avg_purchase_price', Decimal('0')),
            'total_value': summary.get('total_stock_value', Decimal('0')),
            'location_breakdown': summary.get('location_breakdown', []),
            'stock_status': summary.get('status', inventory_item.status),
            'expiry_date': summary.get('expiry_date'),
            'lot_count': summary.get('lot_count', 0),
            'serial_count': summary.get('serial_count', 0),
        })

    @action(detail=True, methods=['get'])
    def minimal_item(self, request, pk=None):
        serializer = InventoryListSerializer(self.get_object(), context=self.get_serializer_context())
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def adjust_stock(self, request, pk=None):
        inventory_item = self.get_object()
        location_id = request.data.get('location_id')
        quantity_change = request.data.get('quantity_change', 0)
        reason = request.data.get('reason', '')
        try:
            quantity_change = Decimal(str(quantity_change))
        except Exception:
            return Response({'error': 'quantity_change must be a valid number'}, status=status.HTTP_400_BAD_REQUEST)

        if not location_id or quantity_change == 0:
            return Response(
                {'error': 'location_id and quantity_change are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile_id = get_request_profile_id(request, required=True, as_str=False)
        stock_location = scope_queryset_by_identity(
            StockLocation.objects.filter(id=location_id),
            canonical_field='profile_id',
            legacy_field='profile',
            value=profile_id,
        ).first()
        if stock_location is None:
            return Response({'error': 'Stock location not found'}, status=status.HTTP_404_NOT_FOUND)

        structural_scope_location = _resolve_structural_scope_location_from_request(
            request,
            profile_id=profile_id,
        )
        if (
            request.data.get('structural_location_id')
            or request.query_params.get('structural_location_id')
            or request.query_params.get('stock_location_id')
        ) and structural_scope_location is None:
            return Response(
                {'error': 'The selected structural location scope is unavailable'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if structural_scope_location is not None:
            scoped_location_ids = get_location_scope_ids(
                profile_id=profile_id,
                stock_location=structural_scope_location,
            ) or [structural_scope_location.id]
            if stock_location.id not in scoped_location_ids:
                return Response(
                    {'error': 'Stock location is outside the selected structural location scope'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            adjustment_result = StockDomainService.adjust_stock(
                inventory_item=inventory_item,
                stock_location=stock_location,
                quantity_change=quantity_change,
                actor_user_id=get_request_user_id(request, as_str=False),
                reason=reason,
            )
        except StockDomainError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        payload = serialize_inventory_item(inventory_item)
        publish_inventory_admin_event(
            event_name='inventory.stock.adjusted',
            payload={
                **payload,
                'stock_location_id': str(stock_location.id),
                'stock_location_name': stock_location.name,
                'quantity_change': float(quantity_change),
                'old_quantity': float(adjustment_result['old_quantity']),
                'new_quantity': float(adjustment_result['new_quantity']),
                'reason': reason,
            },
            actor=_audit_actor_from_request(request),
            target={
                'type': 'inventory_item',
                'id': payload['inventory_item_id'],
                'label': payload['name_snapshot'],
                'barcode': payload['barcode_snapshot'],
                'sku': payload['sku_snapshot'],
                'location_id': str(stock_location.id),
            },
            summary=f"Stock adjusted for {payload['name_snapshot']} at {stock_location.name}.",
            metadata={
                'stock_location_name': stock_location.name,
                'quantity_change': float(quantity_change),
                'reason': reason,
            },
            before={
                'quantity_on_hand': float(adjustment_result['old_quantity']),
            },
            after={
                'quantity_on_hand': float(adjustment_result['new_quantity']),
            },
            severity='warning' if quantity_change < 0 else 'info',
            feature_area='stock_control',
            reference_number=payload['sku_snapshot'] or payload['barcode_snapshot'],
            notification_category='stock_alert',
            notification_title=f"Stock adjusted for {payload['name_snapshot']}",
            notification_message=(
                f"Stock for {payload['name_snapshot']} at {stock_location.name} changed by {float(quantity_change)}."
            ),
            notification_action_url='/inventory',
        )

        return Response({
            'message': 'Stock adjusted successfully',
            'old_quantity': adjustment_result['old_quantity'],
            'new_quantity': adjustment_result['new_quantity'],
            'change': quantity_change,
        })


InventoryViewSet = InventoryItemViewSet
