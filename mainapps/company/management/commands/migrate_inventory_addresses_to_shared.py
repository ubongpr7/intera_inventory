import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.core.management.base import BaseCommand
from django.db import transaction

from mainapps.company.models import CompanyAddress
from mainapps.stock.models import StockLocation


class Command(BaseCommand):
    help = "Migrate Inventory company and stock-location addresses to the shared locations service."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", default=os.getenv("SUBSCRIPTION_SERVICE_URL", "http://subscriptions:8550"))
        parser.add_argument("--service-key", default=os.getenv("SUBSCRIPTION_SERVICE_KEY", ""))
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--timeout", type=float, default=10)

    def handle(self, *args, **options):
        if not options["dry_run"] and not options["service_key"]:
            self.stderr.write(self.style.ERROR("--service-key is required unless --dry-run is used."))
            return

        records = list(self._company_addresses()) + list(self._stock_locations())
        if options["limit"]:
            records = records[: options["limit"]]
        report = {"total": len(records), "migrated": 0, "skipped": 0, "invalid": 0, "errors": []}

        for source_type, source in records:
            source_key = f"{source_type}:{source.pk}"
            if source.address_id:
                report["skipped"] += 1
                continue
            payload, error = self._payload(source_type, source)
            if error:
                report["invalid"] += 1
                report["errors"].append({"source": source_key, "error": error})
                continue
            if options["dry_run"]:
                self.stdout.write(json.dumps({"source": source_key, "payload": payload}, default=str))
                continue

            try:
                shared_id = self._import_address(options, payload)
                with transaction.atomic():
                    source.address_id = shared_id
                    source.save(update_fields=["address_id", "updated_at"])
                report["migrated"] += 1
            except Exception as exc:  # Continue so one bad record does not block the batch.
                report["errors"].append({"source": source_key, "error": f"{type(exc).__name__}: {exc}"})

        self.stdout.write(json.dumps(report, indent=2, default=str))

    @staticmethod
    def _company_addresses():
        return [("company_address", item) for item in CompanyAddress.objects.select_related("company").order_by("pk")]

    @staticmethod
    def _stock_locations():
        return [("stock_location", item) for item in StockLocation.objects.order_by("pk")]

    @staticmethod
    def _payload(source_type, source):
        if source_type == "company_address":
            profile_id = getattr(source.company, "profile_id", None)
            line_1 = (source.address or "").strip()
            label = (source.title or "company").strip()
        else:
            profile_id = source.profile_id
            line_1 = (source.physical_address or "").strip()
            label = (source.name or "stock location").strip()
        if not profile_id:
            return None, "Missing workspace profile_id."
        if not line_1:
            return None, "Missing address text."
        return {
            "profile_id": str(profile_id),
            "label": label[:80],
            "address_line_1": line_1,
            "external_reference": f"inventory:{source_type}:{source.pk}",
        }, None

    @staticmethod
    def _import_address(options, payload):
        url = options["base_url"].rstrip("/") + "/api/v1/locations/internal/import/addresses/"
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "X-Intera-Service-Key": options["service_key"],
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=options["timeout"]) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"shared address import failed: {exc}") from exc
        shared_id = data.get("id") if isinstance(data, dict) else None
        if not shared_id:
            raise RuntimeError("shared service response did not include id")
        return shared_id
