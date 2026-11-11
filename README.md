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
   - the APK signing certificate matches the previously mirrored build;
   - `apksigner` accepts the APK signature;
   - VirusTotal does not flag the APK (optional, hash lookup only).
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
- **Final APK scan.** Every new APK is verified with `apksigner` and looked up
  on VirusTotal by SHA-256. The APK is never uploaded to VirusTotal; only the
  existing report is checked. Set a repository secret `VT_API_KEY` to enable
  this. The thresholds are `VT_MAX_MALICIOUS` (default 0) and
  `VT_MAX_SUSPICIOUS` (default 2). Add the key under
  Settings → Secrets and variables → Actions → `VT_API_KEY`.
- **Weekly integrity audit.** `.github/workflows/apk-security.yml` checks every
  published release asset's GitHub digest and size against `state.json`, then
  re-checks the VirusTotal reports. It does not download the APKs.
- **Only official sources.** `apps.yaml` points at upstream GitHub/Codeberg
  repositories or the vendor's official download URL.

## Apps

<!-- APPS:START -->
| App | Version | APK | Source | Mirrored |
| --- | --- | --- | --- | --- |
| [Obtainium](https://atomic-235.github.io/obtainium-mirror/apps/dev.imranr.obtainium/) | 1.7.4 | [Obtainium-1.7.4.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/Obtainium-1.7.4.apk) | [upstream](https://github.com/ImranR98/Obtainium/releases/tag/v1.7.4) | 2026-11-08T16:12:28Z |
| [OSS Document Scanner](https://atomic-235.github.io/obtainium-mirror/apps/com.akylas.documentscanner/) | 1.26.2.165 | [OSS-Document-Scanner-1.26.2.165.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/OSS-Document-Scanner-1.26.2.165.apk) | [upstream](https://github.com/ossappscollective/OSS-DocumentScanner/releases/tag/com.akylas.documentscanner/android/github/1.26.2/165) | 2026-11-08T16:12:31Z |
| [Hiddify](https://atomic-235.github.io/obtainium-mirror/apps/app.hiddify.com/) | 5.5.5 | [Hiddify-5.5.5.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/Hiddify-5.5.5.apk) | [upstream](https://github.com/hiddify/hiddify-app/releases/tag/v5.5.5) | 2026-11-08T16:12:37Z |
| [HeliBoard](https://atomic-235.github.io/obtainium-mirror/apps/helium314.keyboard/) | 4.2 | [HeliBoard-4.2.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/HeliBoard-4.2.apk) | [upstream](https://github.com/HeliBorg/HeliBoard/releases/tag/v4.2) | 2026-11-08T16:12:41Z |
| [KeePassDX](https://atomic-235.github.io/obtainium-mirror/apps/com.kunzisoft.keepass.libre/) | 4.5.8 | [KeePassDX-4.5.8.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/KeePassDX-4.5.8.apk) | [upstream](https://github.com/Kunzisoft/KeePassDX/releases/tag/4.5.8) | 2026-11-08T16:12:44Z |
| [Coffee](https://atomic-235.github.io/obtainium-mirror/apps/com.openappslabs.coffee/) | 1.6.1 | [Coffee-1.6.1.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/Coffee-1.6.1.apk) | [upstream](https://github.com/OpenAppsLabs/Coffee/releases/tag/v1.6.1) | 2026-11-08T16:12:47Z |
| [GPTMobile](https://atomic-235.github.io/obtainium-mirror/apps/dev.chungjungsoo.gptmobile/) | 0.9.1 | [GPTMobile-0.9.1.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/GPTMobile-0.9.1.apk) | [upstream](https://github.com/Taewan-P/gpt_mobile/releases/tag/v0.9.1) | 2026-11-08T16:12:51Z |
| [NewPipe](https://atomic-235.github.io/obtainium-mirror/apps/org.schabi.newpipe/) | 0.29.3 | [NewPipe-0.29.3.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/NewPipe-0.29.3.apk) | [upstream](https://github.com/TeamNewPipe/NewPipe/releases/tag/v0.29.3) | 2026-11-10T19:53:24Z |
| [Librera FD](https://atomic-235.github.io/obtainium-mirror/apps/com.foobnix.pro.pdf.reader/) | 10.0.31-fdroid | [Librera-FD-10.0.31-fdroid.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/Librera-FD-10.0.31-fdroid.apk) | [upstream](https://github.com/foobnix/LibreraReader/releases/tag/10.0.31) | 2026-11-09T22:33:03Z |
| [RSAF](https://atomic-235.github.io/obtainium-mirror/apps/com.chiller3.rsaf/) | 4.17 | [RSAF-4.17.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/RSAF-4.17.apk) | [upstream](https://github.com/chenxiaolong/RSAF/releases/tag/v4.17) | 2026-11-08T16:13:07Z |
| [AirGuard](https://atomic-235.github.io/obtainium-mirror/apps/de.seemoo.at_tracking_detection.release/) | 3.1.3 | [AirGuard-3.1.3.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/AirGuard-3.1.3.apk) | [upstream](https://github.com/seemoo-lab/AirGuard/releases/tag/3.1.3) | 2026-11-08T16:13:11Z |
| [Proton Drive](https://atomic-235.github.io/obtainium-mirror/apps/me.proton.android.drive/) | 3.3.1 | [Proton-Drive-3.3.1.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/Proton-Drive-3.3.1.apk) | [upstream](https://github.com/ProtonDriveApps/android-drive/releases/tag/3.3.1) | 2026-11-08T16:13:21Z |
| [Proton Mail](https://atomic-235.github.io/obtainium-mirror/apps/ch.protonmail.android/) | 7.11.12 | [Proton-Mail-7.11.12.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/Proton-Mail-7.11.12.apk) | [upstream](https://github.com/ProtonMail/android-mail/releases/tag/7.11.12(18327)) | 2026-11-08T16:13:30Z |
| [Venice AI](https://atomic-235.github.io/obtainium-mirror/apps/com.ai.venice/) | 1.17.19 | [Venice-AI-1.17.19.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/Venice-AI-1.17.19.apk) | [upstream](https://github.com/veniceai/venice-android/releases/tag/v1.17.19-55545ce5-97bf-47cc-b952-ec4518ef2054) | 2026-11-08T16:13:41Z |
| [Gadgetbridge](https://atomic-235.github.io/obtainium-mirror/apps/nodomain.freeyourgadget.gadgetbridge/) | 2026.11.1 | [Gadgetbridge-2026.11.1.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/Gadgetbridge-2026.11.1.apk) | [upstream](https://codeberg.org/Freeyourgadget/Gadgetbridge/releases/tag/2026.11.1) | 2026-11-08T16:19:49Z |
| [WhatsApp](https://atomic-235.github.io/obtainium-mirror/apps/com.whatsapp/) | 2.26.44.76 | [WhatsApp-2.26.44.76.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/WhatsApp-2.26.44.76.apk) | [upstream](https://www.whatsapp.com/android/current/WhatsApp.apk) | 2026-11-09T22:33:21Z |
| [Proton VPN](https://atomic-235.github.io/obtainium-mirror/apps/ch.protonvpn.android.reproducible/) | 5.20.98.0 | [Proton-VPN-5.20.98.0.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/Proton-VPN-5.20.98.0.apk) | [upstream](https://github.com/ProtonVPN/android-app/releases/tag/5.20.98.0) | 2026-11-08T16:13:59Z |
| [SimpleLogin](https://atomic-235.github.io/obtainium-mirror/apps/io.simplelogin.android.fdroid/) | 1.22.0 | [SimpleLogin-1.22.0.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/SimpleLogin-1.22.0.apk) | [upstream](https://github.com/simple-login/Simple-Login-Android/releases/tag/1.22.0) | 2026-11-08T16:14:02Z |
| [CU GUI](https://atomic-235.github.io/obtainium-mirror/apps/io.chameleon.ultra/) | 1.4.0 | [CU-GUI-1.4.0.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/CU-GUI-1.4.0.apk) | [upstream](https://github.com/GameTec-live/ChameleonUltraGUI/releases/tag/1.4) | 2026-11-08T16:23:58Z |
| [Telegram](https://atomic-235.github.io/obtainium-mirror/apps/org.telegram.messenger.web/) | 13.2.2 | [Telegram-13.2.2.apk](https://github.com/atomic-235/obtainium-mirror/releases/download/apk-mirror/Telegram-13.2.2.apk) | [upstream](https://telegram.org/dl/android/apk) | 2026-11-11T06:49:52Z |
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

The Python environment is managed by **uv2nix** (`flake.nix`, `pyproject.toml`,
`uv.lock`).

```bash
direnv allow

# Resolve every upstream without downloading:
python scripts/sync.py --resolve-only

# Download and inspect one app, but don't upload:
python scripts/sync.py --only dev.imranr.obtainium --dry-run \
  --state /tmp/mirror-state.json --site-dir /tmp/mirror-site
```

Without direnv:

```bash
nix develop --command python scripts/sync.py --resolve-only
```

To add a dependency, edit `[project.dependencies]` in `pyproject.toml`, run
`UV_PYTHON="" UV_PYTHON_DOWNLOADS=never uv lock`, then `touch flake.nix && direnv reload`.
Do not use pip or `uv sync` locally; the venv is built by Nix.

## Notes

- This is a personal mirror, not a store. Upstream projects own their APKs and
  their trademarks. WhatsApp and Venice AI are proprietary and are marked as
  such in `apps.yaml`; remove them if you do not want to redistribute them.
- Only the latest version of each app is kept. Old release assets are deleted
  automatically.
- The workflow keeps generated files in `main` and deploys `site/` to Pages.
