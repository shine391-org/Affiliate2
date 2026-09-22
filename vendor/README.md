# Vendor Snapshots

This directory contains **exact snapshots** of third-party tools currently used by Affiliate2.
These are captured for architecture audit purposes and are not modified in this branch.

## vendor/viralmint
Exact snapshot of the ViralMint working tree used by Affiliate2.
- Source: `tools/ViralMint`
- Captured with local modifications intact
- See `docs/VENDOR_BASELINE.md` for provenance details

## vendor/mediacrawler
Exact snapshot of the MediaCrawler working tree currently used/referenced.
- Source: `tools/MediaCrawler`
- Captured with local modifications intact
- See `docs/VENDOR_BASELINE.md` for provenance details

---

These snapshots represent the **current local source** as of the snapshot date.
They are intended for reference and architecture audit only.
Future integration decisions (adapters, replacements, etc.) will be made after remote code audit.
