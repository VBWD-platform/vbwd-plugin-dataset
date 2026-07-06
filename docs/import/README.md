# Data-store storefront import bundle (S110)

Publish the public **Data-store** (`/data-store`) catalogue + detail pages to any
running instance **via import** — no seeding required after the initial install.

## Why this bundle exists

A dataset catalogue page is **widget-driven**: the page itself is an almost-empty
shell that renders a `DatasetCatalogue` **vue-component widget** placed into a
layout area. Rendering it needs three coupled record sets, and importing only the
page (`cms_posts`) leaves an empty frame. This bundle carries all three, and the
push script imports them **in dependency order**:

| Order | File | What it carries |
|-------|------|-----------------|
| 1 | `cms_widgets.json` | `dataset-catalogue` (`DatasetCatalogue`) + `dataset-detail-widget` (`DatasetDetail`) widget records |
| 2 | `cms_layouts.json` | `dataset-catalogue-layout` + `dataset-detail-layout`, **including their `widget_assignments`** (widget placements, resolved by widget slug — hence widgets go first) |
| 3 | `cms_posts.json` | `data-store` + `dataset-detail` pages, each bound to its layout by slug |

All imports are **idempotent upserts** keyed by `slug`, so re-running is safe.

Shared nav widgets referenced by the layouts (`header-nav`, `footer-nav`,
`breadcrumbs`) are deliberately **excluded** — they are platform-wide and already
present on any instance. Their placements resolve by slug; a missing one
safe-degrades (that placement is skipped, the page still imports).

## Seeding vs. import

`plugins/dataset/populate_db.py` (`_seed_cms_data_store`) is for **initial install
only**. Every later propagation between environments uses this bundle. To refresh
the bundle from a correctly-seeded instance, export the six records and re-scope:

```bash
for e in cms_widgets cms_layouts cms_posts; do
  curl -s -X POST "$BASE/api/v1/admin/data-exchange/$e/export" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{}'
done   # then keep only the dataset-* / data-store / dataset-detail rows
```

## Usage

```bash
./push-data-store.sh https://vbwd.cc      admin@example.com 'AdminPass123@'
./push-data-store.sh http://localhost:8081 admin@example.com 'AdminPass123@'
```

The storefront route is **`/data-store`** (hyphenated). `/datastore` is not a
dataset route.
