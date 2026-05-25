from __future__ import annotations

from typing import Iterable

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from mainapps.stock.models import StockLocation, StockLocationType


_STOCK_LOCATION_TYPE_ALIAS_MAP: dict[str, tuple[str, ...]] = {
    "warehouse": ("warehouse", "main warehouse", "central warehouse", "storage facility"),
    "showroom": ("showroom", "front store", "store", "retail floor", "sales floor"),
    "backroom": ("backroom", "back room", "stock room", "storage room"),
    "returns area": ("returns area", "returns shelf", "returns rack", "returns processing"),
    "overflow": ("overflow", "overflow room"),
    "shelf": ("shelf",),
    "rack": ("rack",),
    "bin": ("bin",),
    "wardrobe": ("wardrobe",),
}


def _normalize_stock_location_type_token(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().replace("-", " ").replace("_", " ").split())


def _match_stock_location_type(
    location_types: Iterable[StockLocationType],
    *,
    requested_name: str | None,
    location_name: str | None,
    structural: bool,
    parent_id: str | None,
) -> StockLocationType | None:
    location_type_list = list(location_types)
    normalized_requested = _normalize_stock_location_type_token(requested_name)
    normalized_location_name = _normalize_stock_location_type_token(location_name)

    alias_to_canonical: dict[str, str] = {}
    for canonical, aliases in _STOCK_LOCATION_TYPE_ALIAS_MAP.items():
        alias_to_canonical[canonical] = canonical
        for alias in aliases:
            alias_to_canonical[_normalize_stock_location_type_token(alias)] = canonical

    if normalized_requested:
        canonical = alias_to_canonical.get(normalized_requested, normalized_requested)
        for item in location_type_list:
            if _normalize_stock_location_type_token(item.name) == canonical:
                return item

    if normalized_location_name:
        for alias, canonical in alias_to_canonical.items():
            if alias and alias in normalized_location_name:
                for item in location_type_list:
                    if _normalize_stock_location_type_token(item.name) == canonical:
                        return item

    fallback_names = ["warehouse"] if structural and not parent_id else ["backroom", "shelf", "showroom", "warehouse"]
    for fallback in fallback_names:
        for item in location_type_list:
            if _normalize_stock_location_type_token(item.name) == fallback:
                return item
    return None


class Command(BaseCommand):
    help = "Repair missing stock location defaults such as code, location type, and parent hierarchy."

    def add_arguments(self, parser):
        parser.add_argument("--profile-id", type=int, required=True, help="Workspace profile_id to repair.")
        parser.add_argument(
            "--root-name",
            type=str,
            default="",
            help="Optional explicit primary structural root location name.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist changes. Without this flag the command runs as a dry-run.",
        )

    def handle(self, *args, **options):
        profile_id = int(options["profile_id"])
        root_name = str(options.get("root_name") or "").strip()
        apply_changes = bool(options.get("apply"))

        queryset = StockLocation.objects.select_related("location_type", "parent").filter(profile_id=profile_id).order_by("created_at", "id")
        locations = list(queryset)
        if not locations:
            raise CommandError(f"No stock locations found for profile_id={profile_id}.")

        location_types = list(StockLocationType.objects.order_by("name", "id"))
        if not location_types:
            raise CommandError("No StockLocationType rows found. Seed location types before running this repair.")

        root_location = self._resolve_root_location(locations, root_name=root_name)
        if root_location is None:
            raise CommandError(
                "Unable to resolve a root location. Provide --root-name or ensure the profile has one structural top-level location."
            )

        planned_changes: list[tuple[StockLocation, list[str]]] = []
        for location in locations:
            changes: list[str] = []

            if location.id != root_location.id and location.parent_id is None and not location.structural:
                location.parent = root_location
                changes.append(f"parent->{root_location.name}")

            if not location.location_type_id:
                matched_type = _match_stock_location_type(
                    location_types,
                    requested_name=None,
                    location_name=location.name,
                    structural=bool(location.structural),
                    parent_id=str(location.parent_id) if location.parent_id else None,
                )
                if matched_type is not None:
                    location.location_type = matched_type
                    changes.append(f"type->{matched_type.name}")

            if location.code in (None, ""):
                changes.append("code->auto")

            if changes:
                planned_changes.append((location, changes))

        if not planned_changes:
            self.stdout.write(self.style.SUCCESS(f"No repairs needed for profile_id={profile_id}."))
            return

        self.stdout.write(
            self.style.WARNING(
                f"{'Applying' if apply_changes else 'Dry-run for'} {len(planned_changes)} stock location repair(s) on profile_id={profile_id}."
            )
        )
        for location, changes in planned_changes:
            self.stdout.write(f"- {location.name}: {', '.join(changes)}")

        if not apply_changes:
            self.stdout.write(self.style.WARNING("Dry-run only. Re-run with --apply to persist these changes."))
            return

        with transaction.atomic():
            for location, _changes in planned_changes:
                location.save()

        self.stdout.write(self.style.SUCCESS(f"Applied {len(planned_changes)} stock location repair(s)."))

    def _resolve_root_location(
        self,
        locations: list[StockLocation],
        *,
        root_name: str,
    ) -> StockLocation | None:
        if root_name:
            normalized_root_name = root_name.casefold()
            for location in locations:
                if str(location.name or "").strip().casefold() == normalized_root_name:
                    return location

        structural_roots = [location for location in locations if location.structural and location.parent_id is None]
        if len(structural_roots) == 1:
            return structural_roots[0]
        if structural_roots:
            return structural_roots[0]
        top_level_locations = [location for location in locations if location.parent_id is None]
        return top_level_locations[0] if top_level_locations else None
