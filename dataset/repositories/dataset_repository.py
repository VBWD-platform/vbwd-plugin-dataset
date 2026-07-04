"""DatasetRepository — data access for datasets (S110 T2)."""
from typing import Any, Dict, List, Optional

from plugins.dataset.dataset.models.dataset import Dataset

# The columns the admin list endpoint may sort by, mapped to model attributes.
# A closed allow-list so an arbitrary ``sort_by`` can never reach the ORM.
SORTABLE_COLUMNS = {
    "title": Dataset.title,
    "slug": Dataset.slug,
    "price": Dataset.price,
    "created_at": Dataset.created_at,
    "updated_at": Dataset.updated_at,
    "is_active": Dataset.is_active,
}
DEFAULT_SORT_COLUMN = "updated_at"
DEFAULT_SORT_DIRECTION = "desc"


class DatasetRepository:
    """CRUD + paged/sorted/filtered listing for ``Dataset`` rows."""

    def __init__(self, session) -> None:
        self.session = session

    def save(self, dataset: Dataset) -> Dataset:
        self.session.add(dataset)
        self.session.flush()
        return dataset

    def delete(self, dataset: Dataset) -> None:
        self.session.delete(dataset)
        self.session.flush()

    def find_by_id(self, dataset_id: str) -> Optional[Dataset]:
        return self.session.query(Dataset).filter(Dataset.id == dataset_id).first()

    def find_by_slug(self, slug: str) -> Optional[Dataset]:
        return self.session.query(Dataset).filter(Dataset.slug == slug).first()

    def find_by_vendor_id(self, vendor_id) -> List[Dataset]:
        """Return the datasets owned by ``vendor_id`` (marketplace vendor-mode).

        Ordered by title so the vendor's "my datasets" list is deterministic.
        Filtering in SQL (not Python) so a vendor never loads the whole catalogue
        to see their own rows.
        """
        return (
            self.session.query(Dataset)
            .filter(Dataset.vendor_id == vendor_id)
            .order_by(Dataset.title)
            .all()
        )

    def find_all(
        self,
        page: int = 1,
        per_page: int = 20,
        sort_by: str = DEFAULT_SORT_COLUMN,
        sort_dir: str = DEFAULT_SORT_DIRECTION,
        search: Optional[str] = None,
        dataset_ids: Optional[List[str]] = None,
        active_only: bool = False,
    ) -> Dict[str, Any]:
        """Return a page of datasets.

        Args:
            page: 1-based page number.
            per_page: page size (already clamped by the caller).
            sort_by: one of ``SORTABLE_COLUMNS`` (falls back to the default).
            sort_dir: ``asc`` or ``desc`` (falls back to ``desc``).
            search: case-insensitive substring on title/slug.
            dataset_ids: optional pre-filtered id set (e.g. a category filter
                resolved to ids by the caller); ``None`` means no id filter,
                an empty list means "no matches".
            active_only: when True, restrict to ``is_active`` rows (the public
                catalogue never lists inactive datasets).
        """
        query = self.session.query(Dataset)

        if active_only:
            query = query.filter(Dataset.is_active.is_(True))

        if search:
            term = f"%{search}%"
            query = query.filter(Dataset.title.ilike(term) | Dataset.slug.ilike(term))

        if dataset_ids is not None:
            query = query.filter(Dataset.id.in_(dataset_ids))

        column = SORTABLE_COLUMNS.get(sort_by, SORTABLE_COLUMNS[DEFAULT_SORT_COLUMN])
        ordering = column.asc() if sort_dir == "asc" else column.desc()

        total = query.count()
        items = (
            query.order_by(ordering).offset((page - 1) * per_page).limit(per_page).all()
        )
        pages = (total + per_page - 1) // per_page if per_page else 0
        return {
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages,
        }
