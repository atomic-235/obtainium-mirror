#!/usr/bin/env python3
"""
Mirror Android APKs from their official upstream releases into this repository's
GitHub Releases, and publish an Obtainium-friendly catalog on GitHub Pages.

The pipeline is intentionally simple:

  1. Resolve the newest upstream release/asset for each app in apps.yaml.
  2. Download the asset only when the upstream reference changed.
  3. Verify the APK's package name, versionCode, and signing certificate.
  4. Upload it to the `apk-mirror` release under a versioned asset name.
  5. Regenerate state.json, the Pages catalog, and obtainium_export.json.

APKs are never rebuilt or re-signed. The upstream signature is preserved, so
Android treats these as normal updates over the official build.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml
from loguru import logger as _loguru

try:
    from apk_scan import scan_apk
except ImportError:  # when imported as scripts.sync
    from .apk_scan import scan_apk

# Silence androguard's very verbose loguru output. We use print() for our own
# progress messages, so removing the sink is safe.
_loguru.remove()

ROOT = Path(__file__).resolve().parent.parent
APPS_FILE = ROOT / "apps.yaml"
STATE_FILE = ROOT / "state.json"
EXPORT_FILE = ROOT / "obtainium_export.json"
LEGACY_EXPORT_FILE = ROOT / "legacy" / "obtainium_export.json"
SITE_DIR = ROOT / "site"
README_FILE = ROOT / "README.md"

RELEASE_TAG = "apk-mirror"
RELEASE_TITLE = "APK mirror"
RELEASE_NOTES = (
    "Automated mirror of upstream APK releases. "
    "APKs are byte-identical to the official upstream assets; signatures are preserved."
)

GITHUB_API = "https://api.github.com"
USER_AGENT = "obtainium-mirror/1.0 (+https://github.com/atomic-235/obtainium-mirror)"


def log(message: str) -> None:
    print(message, flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def human_size(size: int | None) -> str:
    if not size:
        return "?"
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GiB"


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return cleaned


def make_asset_name(app_name: str, package: str, version_code: int, version_name: str) -> str:
    name = safe_component(app_name) or safe_component(package) or "app"
    version = safe_component(version_name) or str(version_code)
    return f"{name[:80].rstrip('-._')}-{version[:80]}.apk"


def html_escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def md_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

GH_TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
REPO = os.environ.get("GITHUB_REPOSITORY", "")
if not REPO:
    try:
        remote = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        match = re.search(r"github\.com[:/](?P<repo>[^/]+/[^/.]+)(?:\.git)?$", remote)
        if match:
            REPO = match.group("repo")
    except Exception:
        pass
if not REPO:
    REPO = "atomic-235/obtainium-mirror"

WEB = requests.Session()
WEB.headers.update({"User-Agent": USER_AGENT})

GH = requests.Session()
GH.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
)
if GH_TOKEN:
    GH.headers["Authorization"] = f"Bearer {GH_TOKEN}"


@dataclass
class ResolvedAsset:
    download_url: str
    asset_name: str
    ref: str
    release_tag: str | None = None
    release_url: str | None = None
    digest_sha256: str | None = None
    size: int | None = None
    published_at: str | None = None
    force_check: bool = False


def gh_api(path: str, method: str = "GET", **kwargs: Any) -> Any:
    url = path if path.startswith("http") else f"{GITHUB_API}{path}"
    response = GH.request(method, url, timeout=60, **kwargs)
    if response.status_code == 403 and "rate limit" in response.text.lower():
        raise RuntimeError(f"GitHub API rate limit hit: {response.text[:300]}")
    response.raise_for_status()
    return response.json()


def resolve_github(app: dict[str, Any], defaults: dict[str, Any]) -> ResolvedAsset:
    repo = app["repo"]
    include_pre = bool(app.get("include_prereleases", defaults.get("include_prereleases", False)))
    fallback = int(app.get("fallback_releases", defaults.get("fallback_releases", 10)))
    pattern = re.compile(app["asset"])

    releases = gh_api(f"/repos/{repo}/releases", params={"per_page": max(30, fallback + 5)})
    if not isinstance(releases, list):
        raise RuntimeError(f"Unexpected GitHub releases response for {repo}")

    tested = 0
    for release in releases:
        if release.get("draft"):
            continue
        if release.get("prerelease") and not include_pre:
            continue
        tested += 1
        if tested > fallback:
            break
        for asset in release.get("assets", []):
            name = asset.get("name", "")
            if not pattern.search(name):
                continue
            digest = asset.get("digest")
            if isinstance(digest, str) and digest.startswith("sha256:"):
                digest = digest[len("sha256:") :]
            else:
                digest = None
            return ResolvedAsset(
                download_url=asset["browser_download_url"],
                asset_name=name,
                ref=f"github:{release.get('id')}:{asset.get('id')}:{digest or asset.get('size')}",
                release_tag=release.get("tag_name"),
                release_url=release.get("html_url"),
                digest_sha256=digest,
                size=asset.get("size"),
                published_at=release.get("published_at") or release.get("created_at"),
            )

    raise RuntimeError(f"No matching stable release asset in {repo} (pattern {app['asset']!r})")


def resolve_forgejo(app: dict[str, Any], defaults: dict[str, Any]) -> ResolvedAsset:
    base = app.get("base_url", "https://codeberg.org").rstrip("/")
    repo = app["repo"]
    include_pre = bool(app.get("include_prereleases", defaults.get("include_prereleases", False)))
    fallback = int(app.get("fallback_releases", defaults.get("fallback_releases", 10)))
    pattern = re.compile(app["asset"])

    response = WEB.get(
        f"{base}/api/v1/repos/{repo}/releases",
        params={"limit": max(30, fallback + 5)},
        timeout=60,
    )
    response.raise_for_status()
    releases = response.json()
    if not isinstance(releases, list):
        raise RuntimeError(f"Unexpected Forgejo releases response for {repo}")

    tested = 0
    for release in releases:
        if release.get("draft"):
            continue
        if release.get("prerelease") and not include_pre:
            continue
        tested += 1
        if tested > fallback:
            break
        for asset in release.get("assets", []):
            name = asset.get("name", "")
            if not pattern.search(name):
                continue
            url = asset.get("browser_download_url") or (
                f"{base}/{repo}/releases/download/"
                f"{urllib.parse.quote(release['tag_name'], safe='')}/{urllib.parse.quote(name)}"
            )
            return ResolvedAsset(
                download_url=url,
                asset_name=name,
                ref=f"forgejo:{release.get('id')}:{asset.get('id')}:{asset.get('size')}",
                release_tag=release.get("tag_name"),
                release_url=release.get("html_url"),
                size=asset.get("size"),
                published_at=release.get("published_at") or release.get("created_at"),
            )

    raise RuntimeError(f"No matching stable release asset in {repo} (pattern {app['asset']!r})")


def head_direct(url: str, headers: dict[str, str]) -> tuple[str, str | None, str | None, int | None]:
    """Return (final_url, etag, last_modified, content_length)."""
    try:
        response = WEB.head(url, headers=headers, allow_redirects=True, timeout=60)
        if response.status_code >= 400 or response.status_code == 405:
            response.close()
            response = WEB.get(url, headers=headers, allow_redirects=True, stream=True, timeout=60)
            response.close()
    except requests.RequestException:
        response = WEB.get(url, headers=headers, allow_redirects=True, stream=True, timeout=60)
        response.close()

    if response.status_code >= 400:
        raise RuntimeError(f"HEAD {url} returned HTTP {response.status_code}")

    length_header = response.headers.get("Content-Length")
    length = int(length_header) if length_header and length_header.isdigit() else None
    return (
        response.url,
        response.headers.get("ETag"),
        response.headers.get("Last-Modified"),
        length,
    )


def resolve_direct(app: dict[str, Any], defaults: dict[str, Any]) -> ResolvedAsset:
    del defaults
    url = app["url"]
    headers = app.get("headers") or {}
    final_url, etag, last_modified, length = head_direct(url, headers)
    parts = [etag or "", last_modified or "", str(length or "")]
    ref = "direct:" + "|".join(parts)
    no_validators = not etag and not last_modified
    if no_validators:
        # Without validators we cannot cheaply tell whether the file changed.
        # Force a download and compare the SHA-256 afterwards.
        ref = "direct:no-validators"
    return ResolvedAsset(
        download_url=url,
        asset_name=Path(urllib.parse.urlparse(url).path).name or "app.apk",
        ref=ref,
        size=length,
        published_at=last_modified,
        force_check=no_validators,
        release_url=final_url,
    )


def resolve_asset(app: dict[str, Any], defaults: dict[str, Any]) -> ResolvedAsset:
    source = app.get("source", "github")
    if source == "github":
        return resolve_github(app, defaults)
    if source == "forgejo":
        return resolve_forgejo(app, defaults)
    if source == "direct":
        return resolve_direct(app, defaults)
    raise RuntimeError(f"Unknown source {source!r} for {app['id']}")


# ---------------------------------------------------------------------------
# APK inspection
# ---------------------------------------------------------------------------


@dataclass
class ApkInfo:
    package: str
    version_name: str
    version_code: int
    cert_sha256: list[str]


def parse_apk(path: Path) -> ApkInfo:
    from androguard.core.apk import APK

    apk = APK(str(path))
    package = apk.get_package()
    if not package:
        raise RuntimeError(f"Could not read package name from {path}")
    version_code_raw = apk.get_androidversion_code()
    if version_code_raw in (None, ""):
        raise RuntimeError(f"Could not read versionCode from {path}")
    version_code = int(version_code_raw)
    version_name = apk.get_androidversion_name() or str(version_code)
    certificates = apk.get_certificates()
    if not certificates:
        raise RuntimeError(f"{path} is not signed")
    cert_hashes = sorted({cert.sha256.hex() for cert in certificates})
    return ApkInfo(
        package=package,
        version_name=version_name,
        version_code=version_code,
        cert_sha256=cert_hashes,
    )


def download_file(url: str, destination: Path, headers: dict[str, str] | None = None) -> None:
    with WEB.get(url, headers=headers or {}, stream=True, timeout=180) as response:
        response.raise_for_status()
        expected = response.headers.get("Content-Length")
        written = 0
        last_mark = 0
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                written += len(chunk)
                if written - last_mark >= 50 * 1024 * 1024:
                    log(f"    downloaded {human_size(written)}")
                    last_mark = written
    if expected and expected.isdigit() and written != int(expected):
        raise RuntimeError(
            f"Short download for {url}: got {written} bytes, expected {expected}"
        )


def extract_inner_apk(archive: Path, inner_pattern: str, workdir: Path) -> Path:
    pattern = re.compile(inner_pattern)
    with zipfile.ZipFile(archive) as zf:
        candidates = [name for name in zf.namelist() if pattern.search(name)]
        if not candidates:
            raise RuntimeError(f"No APK matching {inner_pattern!r} inside {archive.name}")

        def preference(name: str) -> tuple[int, int, str]:
            lower = name.lower()
            if "arm64" in lower or "aarch64" in lower:
                return (0, len(name), name)
            if "universal" in lower:
                return (1, len(name), name)
            return (2, len(name), name)

        candidates.sort(key=preference)
        chosen = candidates[0]
        log(f"    using {chosen} from {archive.name}")
        output = workdir / Path(chosen).name
        with zf.open(chosen) as source, output.open("wb") as target:
            shutil.copyfileobj(source, target)
    return output


# ---------------------------------------------------------------------------
# GitHub release publishing
# ---------------------------------------------------------------------------


def gh_cli(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    if not GH_TOKEN:
        raise RuntimeError("GH_TOKEN is not set; cannot publish release assets")
    env = os.environ.copy()
    env["GH_TOKEN"] = GH_TOKEN
    result = subprocess.run(
        ["gh", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=env,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            f"gh {' '.join(args)} failed with exit status {result.returncode}:\n"
            f"{detail[:4000]}"
        )
    return result


def get_release_assets() -> dict[str, int]:
    if not REPO or not GH_TOKEN:
        return {}
    result = gh_cli(
        ["api", f"repos/{REPO}/releases/tags/{RELEASE_TAG}"],
        check=False,
    )
    if result.returncode != 0:
        return {}
    data = json.loads(result.stdout)
    return {asset["name"]: asset["id"] for asset in data.get("assets", [])}


def ensure_release() -> None:
    result = gh_cli(["api", f"repos/{REPO}/releases/tags/{RELEASE_TAG}"], check=False)
    if result.returncode == 0:
        return
    log(f"Creating release {RELEASE_TAG}")
    gh_cli(
        [
            "release",
            "create",
            RELEASE_TAG,
            "--title",
            RELEASE_TITLE,
            "--notes",
            RELEASE_NOTES,
            "--latest=false",
        ]
    )


def upload_asset(path: Path, asset_name: str) -> None:
    ensure_release()
    upload_path = path
    if path.name != asset_name:
        # gh's "file#label" syntax only sets a display label; the asset name
        # comes from the file on disk. Copy to the real target filename.
        upload_path = path.with_name(asset_name)
        if upload_path.exists():
            upload_path.unlink()
        shutil.copyfile(path, upload_path)

    upload = ["release", "upload", RELEASE_TAG, str(upload_path), "--clobber"]
    result = gh_cli(upload, check=False)
    detail = (result.stderr or result.stdout).strip()
    if result.returncode != 0 and "already exists" in detail.lower():
        # GitHub asset names are unique case-insensitively. An old upstream
        # filename (e.g. gadgetbridge-2026.11.1.apk) can collide with the new
        # readable name (Gadgetbridge-2026.11.1.apk). Remove only the exact
        # case-insensitive conflict, then retry.
        for name, asset_id in get_release_assets().items():
            if name.lower() == asset_name.lower():
                log(f"    deleting conflicting asset {name}")
                gh_cli(
                    ["api", "-X", "DELETE", f"repos/{REPO}/releases/assets/{asset_id}"],
                    check=False,
                )
        result = gh_cli(upload, check=False)
        detail = (result.stderr or result.stdout).strip()

    if result.returncode != 0:
        raise RuntimeError(
            f"gh release upload failed for {asset_name} "
            f"(exit {result.returncode}):\n{detail[:3000]}"
        )
    log(f"    uploaded {asset_name}")


def delete_old_assets(package: str, keep: str, also: set[str] | None = None) -> None:
    also = also or set()
    assets = get_release_assets()
    for name, asset_id in assets.items():
        if name == keep:
            continue
        if name.startswith(f"{package}__") or name in also:
            log(f"    deleting old asset {name}")
            gh_cli(
                ["api", "-X", "DELETE", f"repos/{REPO}/releases/assets/{asset_id}"],
                check=False,
            )


def asset_download_url(asset_name: str) -> str:
    quoted = urllib.parse.quote(asset_name)
    return f"https://github.com/{REPO}/releases/download/{RELEASE_TAG}/{quoted}"


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def load_apps() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with APPS_FILE.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    defaults = data.get("defaults") or {}
    apps = data.get("apps") or []
    seen: set[str] = set()
    seen_names: set[str] = set()
    for app in apps:
        package = app.get("id")
        if not package:
            raise RuntimeError("Every app needs an id")
        if package in seen:
            raise RuntimeError(f"Duplicate app id: {package}")
        seen.add(package)
        if not app.get("name"):
            app["name"] = package
        if app["name"] in seen_names:
            raise RuntimeError(
                f"Duplicate app name {app['name']!r}; asset filenames would collide"
            )
        seen_names.add(app["name"])
        if not app.get("author"):
            app["author"] = ""
    return defaults, apps


def load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    else:
        state = {}
    state.setdefault("schema", 1)
    state.setdefault("apps", {})
    return state


def sync_app(
    app: dict[str, Any],
    defaults: dict[str, Any],
    state: dict[str, Any],
    remote_assets: dict[str, int] | None,
    *,
    dry_run: bool,
) -> str:
    package = app["id"]
    previous = state["apps"].get(package)
    resolved = resolve_asset(app, defaults)

    remote_missing = (
        remote_assets is not None
        and previous is not None
        and previous.get("asset_name") not in remote_assets
    )
    unchanged = (
        previous is not None
        and previous.get("upstream_ref") == resolved.ref
        and not remote_missing
        and not resolved.force_check
    )
    if unchanged:
        log(f"[{package}] up to date ({previous.get('version_name')})")
        return "skipped"

    log(
        f"[{package}] {resolved.release_tag or 'direct'} "
        f"-> {resolved.asset_name} ({human_size(resolved.size)})"
    )

    with tempfile.TemporaryDirectory(prefix="obtainium-mirror-") as temp:
        workdir = Path(temp)
        downloaded = workdir / resolved.asset_name
        download_file(resolved.download_url, downloaded, app.get("headers"))
        digest = sha256_file(downloaded)
        if resolved.size is not None and downloaded.stat().st_size != resolved.size:
            raise RuntimeError(
                f"Size mismatch for {resolved.asset_name}: "
                f"{downloaded.stat().st_size} != {resolved.size}"
            )
        if resolved.digest_sha256 and digest != resolved.digest_sha256:
            raise RuntimeError(
                f"SHA-256 mismatch for {resolved.asset_name}: "
                f"upstream advertised {resolved.digest_sha256}, got {digest}"
            )

        apk_path = downloaded
        if app.get("archive"):
            apk_path = extract_inner_apk(downloaded, app.get("inner_asset", r"\.apk$"), workdir)

        info = parse_apk(apk_path)
        expected_package = app.get("package", package)
        if info.package != expected_package:
            raise RuntimeError(
                f"Package mismatch: expected {expected_package}, APK says {info.package}"
            )

        if previous:
            old_code = int(previous.get("version_code", 0))
            if info.version_code < old_code:
                raise RuntimeError(
                    f"Refusing downgrade: upstream versionCode {info.version_code} "
                    f"< mirrored {old_code}"
                )
            old_certs = set(previous.get("cert_sha256") or [])
            if old_certs and set(info.cert_sha256) != old_certs:
                raise RuntimeError(
                    "Signing certificate changed!\n"
                    f"    previously: {', '.join(sorted(old_certs))}\n"
                    f"    upstream:   {', '.join(info.cert_sha256)}\n"
                    "    Refusing to publish. Verify the new key manually, then remove "
                    f"the cert_sha256 from state.json for {package}."
                )

        asset_name = make_asset_name(
            app["name"], package, info.version_code, info.version_name
        )
        same_apk = (
            previous is not None
            and previous.get("sha256") == digest
            and previous.get("asset_name") == asset_name
        )

        if same_apk:
            log(f"[{package}] bytes unchanged, refreshing upstream reference only")
            security_scan = previous.get("security_scan") or scan_apk(str(apk_path), digest)
        else:
            security_scan = scan_apk(str(apk_path), digest)
            if not dry_run:
                upload_asset(apk_path, asset_name)
                old_names = {resolved.asset_name}
                if previous and previous.get("asset_name"):
                    old_names.add(previous["asset_name"])
                delete_old_assets(package, asset_name, old_names)
            else:
                log(f"[{package}] dry run: would upload {asset_name}")

        state["apps"][package] = {
            "name": app["name"],
            "author": app.get("author", ""),
            "package": info.package,
            "version_name": info.version_name,
            "version_code": info.version_code,
            "cert_sha256": info.cert_sha256,
            "sha256": digest,
            "size": apk_path.stat().st_size,
            "asset_name": asset_name,
            "asset_url": asset_download_url(asset_name),
            "upstream_ref": resolved.ref,
            "upstream_tag": resolved.release_tag,
            "upstream_url": resolved.release_url,
            "upstream_asset": resolved.asset_name,
            "published_at": resolved.published_at,
            "mirrored_at": previous.get("mirrored_at") if same_apk and previous else now_iso(),
            "proprietary": bool(app.get("proprietary", False)),
            "homepage": app.get("homepage", ""),
            "security_scan": security_scan,
        }
    return "updated" if not same_apk else "refreshed"


# ---------------------------------------------------------------------------
# Output generation
# ---------------------------------------------------------------------------


def obtainium_settings(app: dict[str, Any], entry: dict[str, Any], base_url: str) -> dict[str, Any]:
    # Matches the app-specific settings of Obtainium's HTML source. The page
    # carries a single APK link and a <span id="version"> element.
    return {
        "intermediateLink": [],
        "customLinkFilterRegex": r"\.apk$",
        "filterByLinkText": False,
        "matchLinksOutsideATags": False,
        "skipSort": True,
        "reverseSort": False,
        "sortByLastLinkSegment": False,
        "versionExtractWholePage": True,
        "versionExtractionRegEx": r'<span id="version">([^<]+)</span>',
        "matchGroupToUse": "1",
        "versionDetection": True,
        "releaseDateAsVersion": False,
        "useVersionCodeAsOSVersion": False,
        "apkFilterRegEx": "",
        "invertAPKFilter": False,
        "autoApkFilterByArch": False,
        "trackOnly": False,
        "defaultPseudoVersioningMethod": "ETag",
        "zippedApkFilterRegEx": "",
        "requestHeader": [],
        "allowInsecure": False,
        "shizukuPretendToBeGooglePlay": False,
        "exemptFromBackgroundUpdates": False,
        "skipUpdateNotifications": False,
        "refreshBeforeDownload": False,
        "about": "",
    }


def build_config(app: dict[str, Any], entry: dict[str, Any], base_url: str) -> dict[str, Any]:
    package = app["id"]
    settings = obtainium_settings(app, entry, base_url)
    return {
        "id": package,
        "url": f"{base_url}/apps/{package}/",
        "author": entry.get("author") or app.get("author", ""),
        "name": app["name"],
        "installedVersion": None,
        "latestVersion": entry["version_name"],
        "apkUrls": json.dumps(
            [[entry["asset_name"], entry["asset_url"]]], separators=(",", ":")
        ),
        "otherAssetUrls": "[]",
        "preferredApkIndex": 0,
        "additionalSettings": json.dumps(settings, separators=(",", ":")),
        "lastUpdateCheck": None,
        "pinned": False,
        "disabled": False,
        "categories": [],
        "releaseDate": None,
        "changeLog": None,
        "releaseUrl": entry.get("upstream_url"),
        "overrideSource": "HTML",
        "allowIdChange": False,
        "pendingRepoRenameUrl": None,
        "apkSizeBytes": entry.get("size"),
    }


def deep_link(configs: Any) -> str:
    payload = json.dumps(configs, separators=(",", ":"), ensure_ascii=False)
    return "obtainium://apps/" + urllib.parse.quote(payload, safe="")


def app_deep_link(config: dict[str, Any]) -> str:
    payload = json.dumps(config, separators=(",", ":"), ensure_ascii=False)
    return "obtainium://app/" + urllib.parse.quote(payload, safe="")


def write_export(
    state: dict[str, Any],
    apps: list[dict[str, Any]],
    base_url: str,
    destination: Path,
) -> list[dict[str, Any]]:
    settings: dict[str, Any] = {}
    if LEGACY_EXPORT_FILE.exists():
        try:
            legacy = json.loads(LEGACY_EXPORT_FILE.read_text(encoding="utf-8"))
            settings = legacy.get("settings") or {}
        except Exception:
            settings = {}

    configs: list[dict[str, Any]] = []
    for app in apps:
        entry = state["apps"].get(app["id"])
        if not entry:
            continue
        configs.append(build_config(app, entry, base_url))

    export = {
        "schemaVersion": 2,
        "appVersion": "0.0.0",
        "exportedAt": now_iso(),
        "settings": settings,
        "apps": configs,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(export, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return configs


def page_shell(title: str, body: str, relative_root: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_escape(title)}</title>
<style>
  :root {{
    color-scheme: dark;
    --bg: #0f1115; --card: #171a21; --border: #2a2f3a;
    --text: #e6e9ef; --muted: #9aa3b2; --accent: #8ab4f8;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text);
         font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
  main {{ width: min(100% - 2rem, 980px); margin: 2rem auto 4rem; }}
  h1 {{ font-size: clamp(1.6rem, 4vw, 2.4rem); margin: 0 0 .4rem; }}
  h2 {{ font-size: 1.15rem; margin: 2rem 0 .6rem; }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .muted {{ color: var(--muted); }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: .8rem; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 1rem; }}
  .card h3 {{ margin: 0 0 .25rem; font-size: 1.05rem; }}
  .meta {{ display: flex; flex-wrap: wrap; gap: .35rem .9rem; font-size: .88rem; color: var(--muted); }}
  .badge {{ display: inline-block; border: 1px solid var(--border); border-radius: 999px;
            padding: .05rem .55rem; font-size: .78rem; color: var(--muted); }}
  .actions {{ display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .8rem; }}
  .button {{ display: inline-block; border: 1px solid var(--border); border-radius: 10px;
             padding: .4rem .7rem; font-size: .9rem; }}
  .button.primary {{ background: var(--accent); color: #0b1020; border-color: var(--accent); font-weight: 600; }}
  code {{ background: #0b0d12; border: 1px solid var(--border); border-radius: 6px;
          padding: .1rem .35rem; font-size: .85em; overflow-wrap: anywhere; }}
  footer {{ margin-top: 3rem; color: var(--muted); font-size: .85rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .92rem; }}
  th, td {{ text-align: left; padding: .5rem .4rem; border-bottom: 1px solid var(--border); vertical-align: top; }}
  @media (max-width: 640px) {{ th:nth-child(4), td:nth-child(4) {{ display: none; }} }}
</style>
</head>
<body><main>
{body}
<footer>
  <p>Mirrored from official upstream releases. Upstream APK signatures are preserved and pinned.
  This is a personal mirror, not affiliated with any upstream project.</p>
  <p><a href="{relative_root}obtainium_export.json">obtainium_export.json</a></p>
</footer>
</main></body>
</html>
"""


def write_site(
    state: dict[str, Any],
    apps: list[dict[str, Any]],
    configs: list[dict[str, Any]],
    base_url: str,
    site_dir: Path,
) -> None:
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    config_by_id = {config["id"]: config for config in configs}

    cards: list[str] = []
    for app in apps:
        package = app["id"]
        entry = state["apps"].get(package)
        if entry:
            config = config_by_id.get(package)
            add_link = app_deep_link(config) if config else ""
            cards.append(
                f"""<article class="card">
  <h3><a href="./apps/{html_escape(package)}/">{html_escape(app['name'])}</a></h3>
  <div class="meta">
    <span>v{html_escape(entry['version_name'])}</span>
    <span>{html_escape(human_size(entry.get('size')))}</span>
    <span class="badge">{html_escape(package)}</span>
  </div>
  <div class="actions">
    <a class="button primary" href="{html_escape(entry['asset_url'])}">Download APK</a>
    <a class="button" href="{html_escape(add_link)}">Add to Obtainium</a>
  </div>
</article>"""
            )
        else:
            cards.append(
                f"""<article class="card">
  <h3>{html_escape(app['name'])}</h3>
  <div class="meta"><span class="badge">{html_escape(package)}</span><span>not mirrored yet</span></div>
</article>"""
            )

    mirrored = sum(1 for app in apps if app["id"] in state["apps"])
    bulk_link = deep_link(configs) if configs else ""
    body = f"""<h1>Obtainium mirror</h1>
<p class="muted">{mirrored} of {len(apps)} apps mirrored from their official upstream releases.</p>
<div class="actions">
  <a class="button primary" href="{html_escape(bulk_link)}">Add all to Obtainium</a>
  <a class="button" href="./obtainium_export.json">Download export JSON</a>
  <a class="button" href="./catalog.json">catalog.json</a>
</div>
<h2>Apps</h2>
<div class="grid">
{''.join(cards)}
</div>
"""
    (site_dir / "index.html").write_text(
        page_shell("Obtainium mirror", body, "./"), encoding="utf-8"
    )

    for app in apps:
        package = app["id"]
        entry = state["apps"].get(package)
        if not entry:
            continue
        config = config_by_id.get(package)
        scan = entry.get("security_scan") or {}
        vt = scan.get("virustotal") or {}
        if vt.get("status") == "found":
            vt_html = (
                f'<a href="{html_escape(vt.get("permalink") or "#")}">'
                f'{html_escape(vt.get("malicious", 0))} malicious / '
                f'{html_escape(vt.get("suspicious", 0))} suspicious</a>'
            )
        elif vt.get("status") == "not_found":
            vt_html = "no report yet"
        elif vt.get("status") == "skipped":
            vt_html = "not configured"
        else:
            vt_html = html_escape(vt.get("status", "unknown"))
        signature = html_escape((scan.get("apksigner") or {}).get("status", "not scanned"))
        body = f"""<p class="muted"><a href="../../">← all apps</a></p>
<h1>{html_escape(app['name'])}</h1>
<p>Version: <span id="version">{html_escape(entry['version_name'])}</span></p>
<p class="muted">{html_escape(entry['package'])} · {html_escape(human_size(entry.get('size')))}</p>
<p class="muted">Signature: {signature} · VirusTotal: {vt_html}</p>
<div class="actions">
  <a class="button primary" href="{html_escape(entry['asset_url'])}">Download APK</a>
  <a class="button" href="{html_escape(app_deep_link(config))}">Add to Obtainium</a>
</div>
<h2>Integrity</h2>
<p>SHA-256:<br><code>{html_escape(entry['sha256'])}</code></p>
<p>Signing certificate SHA-256:<br><code>{html_escape(', '.join(entry.get('cert_sha256') or []))}</code></p>
<h2>Upstream</h2>
<p><a href="{html_escape(entry.get('upstream_url') or app.get('homepage') or '#')}">{html_escape(entry.get('upstream_url') or 'upstream')}</a></p>
<p class="muted">Mirrored {html_escape(entry.get('mirrored_at', ''))}</p>
"""
        target = site_dir / "apps" / package / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            page_shell(f"{app['name']} {entry['version_name']}", body, "../../"),
            encoding="utf-8",
        )

    catalog = {
        "generated_at": now_iso(),
        "apps": [
            {
                **{k: v for k, v in state["apps"][app["id"]].items() if k != "cert_sha256"},
                "cert_sha256": state["apps"][app["id"]].get("cert_sha256", []),
                "page": f"{base_url}/apps/{app['id']}/",
            }
            for app in apps
            if app["id"] in state["apps"]
        ],
    }
    (site_dir / "catalog.json").write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def write_readme(
    state: dict[str, Any],
    apps: list[dict[str, Any]],
    base_url: str,
) -> None:
    if not README_FILE.exists():
        return
    text = README_FILE.read_text(encoding="utf-8")
    rows = []
    for app in apps:
        entry = state["apps"].get(app["id"])
        if entry:
            rows.append(
                f"| [{md_escape(app['name'])}]({base_url}/apps/{app['id']}/) "
                f"| {md_escape(entry['version_name'])} "
                f"| [{md_escape(entry['asset_name'])}]({entry['asset_url']}) "
                f"| [upstream]({entry.get('upstream_url') or app.get('homepage') or '#'}) "
                f"| {md_escape(entry.get('mirrored_at', ''))} |"
            )
        else:
            rows.append(
                f"| {md_escape(app['name'])} | not mirrored "
                f"| – | [upstream]({app.get('homepage') or '#'}) | – |"
            )
    table = (
        "| App | Version | APK | Source | Mirrored |\n"
        "| --- | --- | --- | --- | --- |\n" + "\n".join(rows)
    )
    start = "<!-- APPS:START -->"
    end = "<!-- APPS:END -->"
    if start in text and end in text:
        before, rest = text.split(start, 1)
        _, after = rest.split(end, 1)
        text = f"{before}{start}\n{table}\n{end}{after}"
    else:
        text = f"{text.rstrip()}\n\n{start}\n{table}\n{end}\n"
    README_FILE.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def default_base_url() -> str:
    if REPO:
        owner, name = REPO.split("/", 1)
        if name.lower() == f"{owner.lower()}.github.io":
            return f"https://{owner}.github.io"
        return f"https://{owner}.github.io/{name}"
    return "https://atomic-235.github.io/obtainium-mirror"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", default=[], help="Only sync this package id")
    parser.add_argument("--dry-run", action="store_true", help="Do not upload release assets")
    parser.add_argument("--resolve-only", action="store_true", help="Only resolve upstream assets")
    parser.add_argument("--base-url", default=None, help="Pages base URL")
    parser.add_argument("--state", default=str(STATE_FILE), help="State file path")
    parser.add_argument("--site-dir", default=str(SITE_DIR), help="Site output directory")
    parser.add_argument("--export", default=None, help="Export JSON path")
    parser.add_argument("--no-readme", action="store_true", help="Do not update README.md")
    parser.add_argument(
        "--no-output",
        action="store_true",
        help="Do not write state/site/export (useful for smoke tests)",
    )
    args = parser.parse_args()

    defaults, apps = load_apps()
    base_url = (args.base_url or os.environ.get("MIRROR_BASE_URL") or default_base_url()).rstrip("/")
    state_path = Path(args.state)
    site_dir = Path(args.site_dir)
    export_path = Path(args.export) if args.export else state_path.parent / "obtainium_export.json"
    state = load_state(state_path)

    if args.only:
        wanted = set(args.only)
        unknown = wanted - {app["id"] for app in apps}
        if unknown:
            parser.error(f"Unknown app id(s): {', '.join(sorted(unknown))}")
        apps = [app for app in apps if app["id"] in wanted]

    if args.resolve_only:
        failed = 0
        for app in apps:
            try:
                resolved = resolve_asset(app, defaults)
                log(
                    f"{app['id']}: {resolved.release_tag or 'direct'} "
                    f"-> {resolved.asset_name} ({human_size(resolved.size)})"
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                log(f"{app['id']}: ERROR {exc}")
        return 1 if failed else 0

    if not args.dry_run and not GH_TOKEN:
        log("ERROR: GH_TOKEN/GITHUB_TOKEN is required unless --dry-run is set")
        return 2

    remote_assets = None if args.dry_run else get_release_assets()
    if remote_assets is not None and not remote_assets:
        log("No existing release assets found (first run)")

    results = {"updated": 0, "skipped": 0, "refreshed": 0, "failed": 0}
    failures: list[tuple[str, str]] = []

    for app in apps:
        package = app["id"]
        try:
            outcome = sync_app(
                app, defaults, state, remote_assets, dry_run=args.dry_run
            )
            results[outcome] += 1
        except Exception as exc:  # noqa: BLE001
            results["failed"] += 1
            failures.append((package, str(exc)))
            log(f"[{package}] ERROR: {exc}")

    if not args.no_output:
        configs = write_export(state, apps, base_url, export_path)
        site_dir.mkdir(parents=True, exist_ok=True)
        write_export(state, apps, base_url, site_dir / "obtainium_export.json")
        write_site(state, apps, configs, base_url, site_dir)
        if not args.no_readme:
            write_readme(state, apps, base_url)
        state_path.write_text(
            json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        log(f"Wrote {state_path}, {export_path}, and {site_dir}/")

    log(
        "Done: "
        + ", ".join(f"{key}={value}" for key, value in results.items())
        + f" (base URL {base_url})"
    )
    if failures:
        log("Failures:")
        for package, message in failures:
            log(f"  - {package}: {message}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
