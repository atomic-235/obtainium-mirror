#!/usr/bin/env python3
"""
Audit the published APK release assets.

- Checks every asset in state.json exists in the `apk-mirror` release.
- Compares GitHub's published SHA-256 digest (and size) with state.json.
- Optionally looks up each SHA-256 on VirusTotal (read-only, no upload).

Run weekly by .github/workflows/apk-security.yml, or manually:

    GH_TOKEN=$(gh auth token) VT_API_KEY=... python scripts/scan_release.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

try:
    from apk_scan import check_virustotal_result, virustotal_lookup
except ImportError:  # when imported as scripts.scan_release
    from .apk_scan import check_virustotal_result, virustotal_lookup

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "state.json"
RELEASE_TAG = "apk-mirror"
REPO = os.environ.get("GITHUB_REPOSITORY") or "atomic-235/obtainium-mirror"
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""


def api(path: str) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "obtainium-mirror-audit/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    response = requests.get(f"https://api.github.com{path}", headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()


def main() -> int:
    if not STATE_FILE.exists():
        print("state.json is missing", file=sys.stderr)
        return 2

    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    release = api(f"/repos/{REPO}/releases/tags/{RELEASE_TAG}")
    assets = {asset["name"]: asset for asset in release.get("assets", [])}

    errors: list[str] = []
    warnings: list[str] = []
    checked = 0

    for package, entry in sorted(state.get("apps", {}).items()):
        asset_name = entry.get("asset_name")
        asset = assets.get(asset_name) if asset_name else None
        if not asset:
            errors.append(f"{package}: asset {asset_name!r} is missing from the release")
            continue

        checked += 1
        digest = asset.get("digest") or ""
        if digest.startswith("sha256:"):
            digest = digest[len("sha256:") :]
        if digest and digest != entry.get("sha256"):
            errors.append(
                f"{package}: release digest {digest} != state {entry.get('sha256')}"
            )
        if asset.get("size") != entry.get("size"):
            errors.append(
                f"{package}: release size {asset.get('size')} != state {entry.get('size')}"
            )

        vt = virustotal_lookup(entry["sha256"])
        status = vt.get("status")
        if status == "found":
            print(
                f"{package}: VirusTotal malicious={vt.get('malicious')} "
                f"suspicious={vt.get('suspicious')} "
                f"undetected={vt.get('undetected')}"
            )
            try:
                check_virustotal_result(entry["sha256"], vt)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
        elif status == "not_found":
            print(f"{package}: VirusTotal has no report yet")
        elif status == "skipped":
            pass
        else:
            warnings.append(f"{package}: VirusTotal {status}: {vt.get('reason', '')}")

    if not os.environ.get("VT_API_KEY"):
        print("VirusTotal lookup skipped: VT_API_KEY is not set")
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)

    print(f"Checked {checked} release assets.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
