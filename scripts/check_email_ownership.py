#!/usr/bin/env python3
"""Reject new direct SMTP/Django-mail imports outside the legacy mail boundary."""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_MODULES = {
    "subapps/services/emails/email_services.py",  # Removed after notification-service cutover.
    "subapps/email_system/emails.py",  # Legacy unused helper; removal is tracked separately.
}
EXEMPTION_EXPIRES_ON = date(2026, 10, 31)
DIRECT_MAIL_IMPORT = re.compile(
    r"^\s*(?:from\s+django\.core\.mail\s+import|import\s+smtplib\b|from\s+smtplib\s+import)",
    re.MULTILINE,
)
EXCLUDED_PARTS = {".git", ".venv", "node_modules", "__pycache__", "migrations", "tests"}


def main() -> int:
    if date.today() > EXEMPTION_EXPIRES_ON:
        print(f"Legacy inventory email exemptions expired on {EXEMPTION_EXPIRES_ON.isoformat()}.")
        return 1
    violations = []
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if EXCLUDED_PARTS.intersection(relative.parts) or relative.as_posix() in ALLOWED_MODULES:
            continue
        if DIRECT_MAIL_IMPORT.search(path.read_text(encoding="utf-8")):
            violations.append(relative.as_posix())
    if violations:
        print("Direct email imports must use the approved inventory mail boundary:")
        print("\n".join(f" - {path}" for path in violations))
        return 1
    print("Email ownership check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
