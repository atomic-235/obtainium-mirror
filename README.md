# Obtainium mirror

A personal, automated APK mirror. Every app in [`apps.yaml`](apps.yaml) is pulled
from its **official upstream release** and republished here, so Obtainium only
has to track this repository instead of nineteen different sources.

**Catalog:** <https://atomic-235.github.io/obtainium-mirror/>

The catalog page has an **Add all to Obtainium** button and per-app
`obtainium://` links. You can also download
[`obtainium_export.json`](https://atomic-235.github.io/obtainium-mirror/obtainium_export.json)
and import it from Obtainium → Import/Export.

## How it works

A GitHub Actions workflow runs every six hours (and on demand):

1. Resolves the newest stable release/asset for each entry in `apps.yaml`
   (GitHub Releases, Codeberg/Forgejo Releases, or a direct APK URL).
2. Skips the download when the upstream release/asset reference has not changed.
3. Downloads the asset and verifies:
   - the upstream SHA-256 digest, when the forge publishes one;
   - the Android package name;
   - the `versionCode` is not a downgrade;
   - the APK signing certificate matches the previously mirrored build.
4. Uploads the APK to the [`apk-mirror`](../../releases/tag/apk-mirror) release
   as `<package>__<versionCode>__<versionName>.apk`.
5. Regenerates `state.json`, the Pages catalog, and `obtainium_export.json`.

APKs are **never rebuilt or re-signed**. The bytes and the signature are the
upstream ones, so Android installs these as normal updates over the official
build.

## Why release assets instead of an F-Droid repository?

A true F-Droid repo needs the APK files to live under the same URL as the signed
index. GitHub's hard limits make that a poor fit for this app set:

- Git refuses files larger than **100 MiB**; WhatsApp (156 MB), Proton Mail
  (159 MB), Proton Drive (199 MB), and Venice (211 MB) exceed that.
- GitHub Pages sites are limited to **1 GB**, and every app version must be
  re-deployed.
- F-Droid clients do not reliably follow the cross-host redirects used by
  GitHub release assets, so release assets cannot back an F-Droid index.

GitHub Releases allow files up to 2 GB each and keep the repository small, so
this mirror uses a release asset store plus a static Pages catalog. Obtainium's
HTML source understands the catalog pages, including the real version name, so
update checks stay lightweight.

If you later want a real F-Droid repo, host the APKs on object storage
(Cloudflare R2, Backblaze B2, etc.) and point `fdroidserver` at that origin.

## Security model

- **Signature pinning.** `state.json` records the signing certificate SHA-256
  for every app. A changed certificate fails the sync instead of silently
  publishing a new signer. If an upstream project legitimately rotates keys,
  verify the new key out-of-band, then remove the `cert_sha256` field for that
  app and re-run the workflow.
- **Upstream digest check.** GitHub release assets now expose a SHA-256 digest;
  it is checked against the downloaded file before upload.
- **No downgrades.** A `versionCode` lower than the currently mirrored one is
  rejected.
- **Package check.** The APK must declare the expected package id (useful for
  repos that publish several flavours in one release).
- **Only official sources.** `apps.yaml` points at upstream GitHub/Codeberg
  repositories or the vendor's official download URL.

## Apps

<!-- APPS:START -->
| App | Version | APK | Source | Mirrored |
| --- | --- | --- | --- | --- |
| [Obtainium](https://atomic-235.github.io/obtainium-mirror/apps/dev.imranr.obtainium/) | 1.7.4 | [dev.imranr.obtainium__23623__1.7.4.apk](https://github.com//releases/download/apk-mirror/dev.imranr.obtainium__23623__1.7.4.apk) | [upstream](https://github.com/ImranR98/Obtainium/releases/tag/v1.7.4) | 2026-11-08T14:19:23Z |
| [Gadgetbridge](https://atomic-235.github.io/obtainium-mirror/apps/nodomain.freeyourgadget.gadgetbridge/) | 2026.11.1 | [nodomain.freeyourgadget.gadgetbridge__257__2026.11.1.apk](https://github.com//releases/download/apk-mirror/nodomain.freeyourgadget.gadgetbridge__257__2026.11.1.apk) | [upstream](https://codeberg.org/Freeyourgadget/Gadgetbridge/releases/tag/2026.11.1) | 2026-11-08T14:19:27Z |
| [CU GUI](https://atomic-235.github.io/obtainium-mirror/apps/io.chameleon.ultra/) | 1.4.0 | [io.chameleon.ultra__2457__1.4.0.apk](https://github.com//releases/download/apk-mirror/io.chameleon.ultra__2457__1.4.0.apk) | [upstream](https://github.com/GameTec-live/ChameleonUltraGUI/releases/tag/1.4) | 2026-11-08T14:19:30Z |
<!-- APPS:END -->

## Adding an app

Add an entry to `apps.yaml`:

```yaml
  - id: com.example.app
    name: Example
    author: example
    source: github
    repo: example/android
    asset: '^Example-.*\.apk$'
    homepage: https://github.com/example/android
```

Source types:

- `github` — `repo` is `owner/name`, `asset` is a regex for the release asset.
- `forgejo` — `base_url` plus `repo`; works with Codeberg and self-hosted Forgejo.
- `direct` — `url` is a direct APK URL; checked with `ETag`/`Last-Modified`.

Optional fields: `include_prereleases`, `fallback_releases`, `package`
(expected package override), `archive` + `inner_asset` (for zips that contain an
APK), `proprietary`, `homepage`.

Then run the workflow manually (Actions → Sync APK mirror → Run workflow) or
push the change. The first run downloads and mirrors the app.

## Local testing

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Resolve every upstream without downloading:
.venv/bin/python scripts/sync.py --resolve-only

# Download and inspect one app, but don't upload:
.venv/bin/python scripts/sync.py --only dev.imranr.obtainium --dry-run \
  --state /tmp/mirror-state.json --site-dir /tmp/mirror-site
```

## Notes

- This is a personal mirror, not a store. Upstream projects own their APKs and
  their trademarks. WhatsApp and Venice AI are proprietary and are marked as
  such in `apps.yaml`; remove them if you do not want to redistribute them.
- Only the latest version of each app is kept. Old release assets are deleted
  automatically.
- The workflow keeps generated files in `main` and deploys `site/` to Pages.
