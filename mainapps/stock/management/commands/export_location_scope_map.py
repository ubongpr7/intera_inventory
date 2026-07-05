from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from mainapps.stock.models import StockLocation
from subapps.services.location_scope import get_workspace_default_structural_location, resolve_structural_location


def _normalize_name(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


class Command(BaseCommand):
    help = "Export a workspace stock-location map that can be used to backfill location-scoped terminal fields in other services."

    def add_arguments(self, parser):
        parser.add_argument("--profile-id", type=int, required=True, help="Workspace profile_id to export.")
        parser.add_argument(
            "--output",
            type=str,
            default="",
            help="Optional file path to write the JSON payload to. Defaults to stdout.",
        )
        parser.add_argument(
            "--pretty",
            action="store_true",
            help="Pretty-print the JSON payload.",
        )

    def handle(self, *args, **options):
        profile_id = int(options["profile_id"])
        output_path = str(options.get("output") or "").strip()
        pretty = bool(options.get("pretty"))

        locations = list(
            StockLocation.objects.select_related("parent")
            .filter(profile_id=profile_id)
            .order_by("created_at", "id")
        )
        if not locations:
            raise CommandError(f"No stock locations found for profile_id={profile_id}.")

        default_structural_location = get_workspace_default_structural_location(profile_id=profile_id)
        payload = {
            "profile_id": profile_id,
            "default_structural_location_id": str(default_structural_location.id) if default_structural_location else None,
            "default_structural_location_name": str(default_structural_location.name or "") if default_structural_location else "",
            "locations": [],
            "location_name_map": {},
        }

        for location in locations:
            structural_location = resolve_structural_location(profile_id=profile_id, stock_location=location)
            entry = {
                "location_id": str(location.id),
                "location_name": str(location.name or ""),
                "normalized_location_name": _normalize_name(location.name),
                "structural": bool(location.structural),
                "parent_id": str(location.parent_id) if location.parent_id else None,
                "structural_location_id": str(structural_location.id) if structural_location else None,
                "structural_location_name": str(structural_location.name or "") if structural_location else "",
            }
            payload["locations"].append(entry)
            normalized_name = entry["normalized_location_name"]
            if normalized_name:
                payload["location_name_map"].setdefault(normalized_name, []).append(entry)

        rendered = json.dumps(payload, indent=2 if pretty else None, sort_keys=pretty)

        if output_path:
            destination = Path(output_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(rendered + "\n", encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Wrote location scope map to {destination}."))
            return

        self.stdout.write(rendered)
