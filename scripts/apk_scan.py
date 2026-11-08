#!/usr/bin/env python3
"""
Security checks for the final APK files.

Two independent checks:

* apksigner: verifies that the APK signature is valid and reports the signature
  schemes. This is the canonical Android verifier, independent of androguard.
* VirusTotal: looks up the APK's SHA-256. It never uploads the APK; a missing
  report is reported as "not_found" and does not fail the sync.

VirusTotal is optional. Set VT_API_KEY to enable it. Without a key the lookup is
skipped and the rest of the pipeline still runs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

VT_API_KEY = os.environ.get("VT_API_KEY", "").strip()
VT_MAX_MALICIOUS = int(os.environ.get("VT_MAX_MALICIOUS", "0"))
VT_MAX_SUSPICIOUS = int(os.environ.get("VT_MAX_SUSPICIOUS", "2"))
VT_API = "https://www.virustotal.com/api/v3/files"
ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST_FILE = ROOT / "security_allowlist.json"
_VT_LAST_CALL = 0.0


def load_allowlist() -> dict:
    try:
        return json.loads(ALLOWLIST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def verify_with_apksigner(path: str) -> dict:
    executable = shutil.which("apksigner")
    if not executable:
        return {"status": "skipped", "reason": "apksigner not installed"}

    result = subprocess.run(
        [executable, "verify", "--verbose", "--print-certs", str(path)],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "apksigner rejected the APK signature:\n"
            f"{(result.stderr or result.stdout).strip()[:2000]}"
        )

    schemes = [
        line.strip()
        for line in result.stdout.splitlines()
        if "scheme" in line.lower() and line.strip().lower().endswith("true")
    ]
    return {"status": "verified", "schemes": schemes}


def virustotal_lookup(sha256: str) -> dict:
    global _VT_LAST_CALL

    if not VT_API_KEY:
        return {"status": "skipped", "reason": "VT_API_KEY not set"}

    # The free VirusTotal API allows 4 lookups/minute. Keep a safe interval
    # between calls so a first run with many new APKs does not get rate-limited.
    elapsed = time.time() - _VT_LAST_CALL
    if elapsed < 16:
        time.sleep(16 - elapsed)

    for attempt in range(2):
        response = requests.get(
            f"{VT_API}/{sha256}",
            headers={"x-apikey": VT_API_KEY, "User-Agent": "obtainium-mirror/1.0"},
            timeout=60,
        )
        _VT_LAST_CALL = time.time()
        if response.status_code == 429 and attempt == 0:
            time.sleep(60)
            continue
        break

    if response.status_code == 404:
        return {
            "status": "not_found",
            "permalink": f"https://www.virustotal.com/gui/file/{sha256}",
        }
    if response.status_code == 429:
        return {"status": "rate_limited", "reason": "VirusTotal rate limit"}
    if response.status_code >= 400:
        return {
            "status": "error",
            "reason": f"HTTP {response.status_code}",
            "detail": response.text[:300],
        }

    data = response.json().get("data", {})
    attributes = data.get("attributes", {})
    stats = attributes.get("last_analysis_stats", {}) or {}
    detections = [
        f"{engine}: {details.get('result')}"
        for engine, details in (attributes.get("last_analysis_results") or {}).items()
        if details.get("category") in ("malicious", "suspicious")
    ]
    return {
        "status": "found",
        "malicious": int(stats.get("malicious", 0)),
        "suspicious": int(stats.get("suspicious", 0)),
        "undetected": int(stats.get("undetected", 0)),
        "harmless": int(stats.get("harmless", 0)),
        "reputation": attributes.get("reputation"),
        "last_analysis_date": attributes.get("last_analysis_date"),
        "detections": detections,
        "permalink": f"https://www.virustotal.com/gui/file/{sha256}",
        "checked_at": now_iso(),
    }


def check_virustotal_result(sha256: str, result: dict, package: str | None = None) -> None:
    if result.get("status") != "found":
        return
    max_malicious = VT_MAX_MALICIOUS
    max_suspicious = VT_MAX_SUSPICIOUS
    if package:
        exception = load_allowlist().get(package)
        if exception and exception.get("sha256") == sha256:
            max_malicious = int(exception.get("max_malicious", max_malicious))
            max_suspicious = int(exception.get("max_suspicious", max_suspicious))
            print(
                f"NOTE: {package} allowlisted for {sha256[:12]}… "
                f"({exception.get('reason', 'no reason given')})"
            )
    malicious = int(result.get("malicious", 0))
    suspicious = int(result.get("suspicious", 0))
    if malicious > max_malicious or suspicious > max_suspicious:
        detections = "; ".join(result.get("detections") or [])
        raise RuntimeError(
            "VirusTotal flagged the APK: "
            f"malicious={malicious}, suspicious={suspicious}\n"
            f"    {detections}\n"
            f"    {result.get('permalink')}"
        )


def scan_apk(path: str, sha256: str, package: str | None = None) -> dict:
    signature = verify_with_apksigner(path)
    vt = virustotal_lookup(sha256)
    check_virustotal_result(sha256, vt, package)
    return {
        "scanned_at": now_iso(),
        "apksigner": signature,
        "virustotal": vt,
    }
