# Artifact checksum verification

## Problem

Managed model profiles can show **Unverified** after a download completes when the Hugging Face file metadata does not expose a SHA-256 digest through the SDK's `sibling.lfs.oid` field. The current validation can confirm the file exists and its size matches the recorded metadata, but it cannot confirm the file contents.

This affects newly downloaded files as well as legacy download records. It is not necessarily evidence of corruption; it means a trusted checksum was not available to compare.

## Current behavior

- A stored SHA-256 is verified during artifact validation.
- A completed file with matching size but no stored SHA-256 is reported as **Unverified**.
- A missing file, size mismatch, invalid path/metadata, or checksum mismatch is reported as **Broken**.
- Download-worker checksum verification remains active when a checksum is available from repository metadata.

## Revisit

Investigate a stronger verification path that does not require a Hub LFS checksum, for example:

1. Confirm whether Hugging Face exposes a canonical blob digest or checksum through another metadata endpoint or SDK field for Xet-backed files.
2. Calculate and persist a local SHA-256 after every completed download when no trusted remote digest is available.
3. Distinguish **locally hashed** from **remotely verified** artifacts in the data model and UI.
4. Define how locally calculated digests are trusted across restart, backup, model replacement, and repository revision changes.
5. Add migration/reconciliation support for existing completed downloads and avoid forcing unnecessary multi-gigabyte re-downloads.

Do not silently label a locally calculated digest as a remote verification unless the source and trust boundary are documented.
