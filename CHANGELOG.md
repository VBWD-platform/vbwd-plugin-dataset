# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v26.6] - 2026-07-02

### Added
- Initial release of the backend `dataset` vertical (S110): a `Dataset` is a
  `Priceable` sellable whose purchase grants an entitlement.
- Versioned `DatasetSnapshot` archive with pluggable storage backends
  (`LocalArchiveBackend` over the core filesystem seam `var/dataset/…`,
  optional `AwsS3Backend`).
- Taxonomy via `dataset_category` (cms_term) + `DatasetTerm` junction.
- Scoped read API: metered `/dataset/<slug>/data`, session `/preview`
  (100-row cap), `/meta`, and browser `/download`; HMAC inbound webhooks.
- One-time purchase (`POST /dataset/orders` → CUSTOM invoice line →
  `invoice.paid` grant) and recurring access via `DatasetLineItemHandler`.
- `DatasetExchanger` (data-exchange) + idempotent `populate_db.py` seed.
