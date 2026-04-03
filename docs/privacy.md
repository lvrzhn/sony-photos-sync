---
layout: default
title: Privacy Policy — Sony Photos Sync
---

# Privacy Policy

**Last updated:** April 2, 2026

Sony Photos Sync is a free, open-source macOS application that transfers photos from Sony Alpha cameras to Google Photos.

## What Data We Access

Sony Photos Sync uses the Google Photos API solely to **upload photos** from your camera to your Google Photos account. The app:

- **Uploads** photos you take with your Sony camera to your Google Photos library
- **Cannot delete, modify, or download** any existing photos in your Google Photos account
- **Cannot access** photos uploaded by other apps or devices

## What Data We Store

All data is stored **locally on your Mac** only:

- **OAuth token** — stored in `~/Library/Application Support/SonyPhotosSync/` to authenticate with your Google account. This token is never shared with anyone.
- **Perceptual hashes** — fingerprints of uploaded photos stored locally to prevent duplicate uploads. These are mathematical hashes, not images.
- **Log files** — stored locally for troubleshooting. Logs contain filenames and timestamps only, never photo content.

## What Data We Share

**None.** Sony Photos Sync does not collect, transmit, or share any personal data with anyone. The app communicates only with:

- **Google Photos API** — to upload your photos to your own Google account
- **Your local network** — to receive photos from your camera via FTP

There is no analytics, no telemetry, no tracking, and no third-party services.

## Data Retention

All data remains on your Mac. Uninstalling the app and removing the `~/Library/Application Support/SonyPhotosSync/` folder deletes all stored data. You can revoke Google Photos access at any time at [myaccount.google.com/permissions](https://myaccount.google.com/permissions).

## Open Source

Sony Photos Sync is open source. You can review the complete source code at [github.com/lvrzhn/sony-photos-sync](https://github.com/lvrzhn/sony-photos-sync) to verify these claims.

## Contact

For questions about this privacy policy, please open an issue on [GitHub](https://github.com/lvrzhn/sony-photos-sync/issues).
