# vbwd-plugin-dataset

Backend **Datasets** vertical (S110). A `Dataset` is a `Priceable` sellable whose
purchase grants a scoped entitlement to its data (API + browser download).

Depends on `subscription` and `cms` (declared in `metadata.dependencies`).

## Structure

```
plugins/dataset/
├── __init__.py           # DatasetPlugin(BasePlugin)
├── config.json           # Default configuration
├── admin-config.json     # Admin-editable settings
├── populate_db.py        # Demo data (idempotent, Air-Quality seed)
├── dataset/              # Source code
│   ├── models/           # Dataset, DatasetSnapshot, DatasetTerm, ...
│   ├── repositories/
│   ├── services/         # DatasetService, DatasetAccessService, ...
│   ├── storage/          # LocalArchiveBackend, AwsS3Backend
│   └── routes.py
├── migrations/versions/
└── tests/
    ├── unit/
    └── integration/
```

## Buy paths

- **One-time:** `POST /api/v1/dataset/orders` → CUSTOM invoice line +
  `invoice.payment_metadata.dataset` → capture → `invoice.paid` →
  `DatasetOneTimePaymentHandler` grants access for `invoice.user_id`.
- **Recurring:** `DatasetLineItemHandler` (rides `LineItemType.CUSTOM` +
  `extra_data.plugin='dataset'`; there is no core `DATASET` enum).

## Development

```bash
docker compose run --rm test pytest plugins/dataset/tests/unit/ -v
docker compose run --rm test pytest plugins/dataset/tests/integration/ -v
```
