"""DatasetTaxonomyService — categories via the shared term engine (S110 T4).

Single home for the dataset<->``dataset_category`` term junction. Categories are
the shared cms terms (``cms_term`` with ``term_type='dataset_category'``); this
service assigns/unassigns them to datasets through the NET-NEW ``dataset_term``
junction and resolves the admin list ``?category=`` filter (by slug or id) to a
concrete dataset-id list.

Depends only on abstractions (DIP): the junction repository and the cms term
repository. The ``dataset_category`` term type is the single source of truth in
``plugins.dataset`` (re-exported here for callers/tests).
"""
from typing import Dict, List, Optional
from uuid import UUID

from plugins.dataset import DATASET_CATEGORY_TERM_TYPE

__all__ = [
    "DATASET_CATEGORY_TERM_TYPE",
    "DatasetTaxonomyService",
    "InvalidCategoryTermError",
]


class InvalidCategoryTermError(Exception):
    """Raised when a term id is missing or is not a ``dataset_category`` term."""


class DatasetTaxonomyService:
    """Assign/unassign dataset categories and resolve the category filter."""

    def __init__(self, dataset_term_repository, term_repository) -> None:
        self._junction = dataset_term_repository
        self._terms = term_repository

    # ------------------------------------------------------------------
    # Assignment
    # ------------------------------------------------------------------

    def assign_category(self, dataset_id: str, term_id: str) -> None:
        """Link a ``dataset_category`` term to a dataset (idempotent)."""
        self._require_category_term(term_id)
        if not self._junction.exists(dataset_id, term_id):
            self._junction.add(dataset_id, term_id)

    def unassign_category(self, dataset_id: str, term_id: str) -> None:
        """Remove a category link (a no-op when absent)."""
        self._junction.remove(dataset_id, term_id)

    def bulk_assign_category(self, dataset_ids: List[str], term_id: str) -> int:
        """Assign one category term to many datasets; return the count."""
        self._require_category_term(term_id)
        assigned = 0
        for dataset_id in dataset_ids:
            if not self._junction.exists(dataset_id, term_id):
                self._junction.add(dataset_id, term_id)
            assigned += 1
        return assigned

    def assigned_category_ids(self, dataset_id: str) -> List[str]:
        """The category term ids currently linked to a dataset."""
        return self._junction.find_term_ids_by_dataset(dataset_id)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def list_category_index(self) -> List[Dict[str, str]]:
        """The public ``dataset_category`` index as ``{id, slug, label}`` dicts.

        Single home for listing dataset categories (the public catalogue filter
        reuses this). Ordered by the shared term repository's default ordering.
        Returns a read projection — the cms term model never leaves this service.
        """
        terms = self._terms.find_by_type(DATASET_CATEGORY_TERM_TYPE)
        return [
            {"id": str(term.id), "slug": term.slug, "label": term.name}
            for term in terms
        ]

    def dataset_ids_for_category(self, category: str) -> List[str]:
        """Resolve a category (slug or id) to the dataset ids linked to it.

        Returns an empty list — an honest "no matches" — when the category does
        not resolve, never ``None`` (which the repository reads as "no filter").
        """
        term_id = self._resolve_term_id(category)
        if term_id is None:
            return []
        return self._junction.find_dataset_ids_by_term(term_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_category_term(self, term_id: str) -> None:
        term = self._terms.find_by_id(term_id)
        if term is None or term.term_type != DATASET_CATEGORY_TERM_TYPE:
            raise InvalidCategoryTermError(
                f"Term '{term_id}' is not a '{DATASET_CATEGORY_TERM_TYPE}' term"
            )

    def _resolve_term_id(self, category: str) -> Optional[str]:
        """Map a category slug or id to its term id (``None`` when unknown)."""
        by_slug = self._terms.find_by_type_and_slug(
            DATASET_CATEGORY_TERM_TYPE, category
        )
        if by_slug is not None:
            return str(by_slug.id)

        if self._looks_like_uuid(category):
            by_id = self._terms.find_by_id(category)
            if by_id is not None and by_id.term_type == DATASET_CATEGORY_TERM_TYPE:
                return str(by_id.id)
        return None

    @staticmethod
    def _looks_like_uuid(value: str) -> bool:
        try:
            UUID(str(value))
            return True
        except (ValueError, AttributeError, TypeError):
            return False
