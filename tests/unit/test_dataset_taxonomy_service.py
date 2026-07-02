"""T4 — DatasetTaxonomyService assign/unassign/bulk + filter (MagicMock repos).

The taxonomy service is the single home for the dataset<->``dataset_category``
term junction. It depends only on the abstractions: the junction repository and
the cms term repository (for slug/id resolution). No DB here — MagicMocks stand
in for both repositories.
"""
from unittest.mock import MagicMock

from plugins.dataset.dataset.services.dataset_taxonomy_service import (
    DATASET_CATEGORY_TERM_TYPE,
    DatasetTaxonomyService,
    InvalidCategoryTermError,
)


def _category_term(term_id="term-1", slug="environment"):
    """A stand-in cms term of the dataset_category type."""
    term = MagicMock()
    term.id = term_id
    term.slug = slug
    term.term_type = DATASET_CATEGORY_TERM_TYPE
    return term


def _service(junction_repo, term_repo):
    return DatasetTaxonomyService(
        dataset_term_repository=junction_repo,
        term_repository=term_repo,
    )


def test_assign_category_adds_junction_when_absent():
    junction_repo = MagicMock()
    junction_repo.exists.return_value = False
    term_repo = MagicMock()
    term_repo.find_by_id.return_value = _category_term()

    service = _service(junction_repo, term_repo)
    service.assign_category("dataset-1", "term-1")

    junction_repo.add.assert_called_once_with("dataset-1", "term-1")


def test_assign_category_is_idempotent_when_present():
    junction_repo = MagicMock()
    junction_repo.exists.return_value = True
    term_repo = MagicMock()
    term_repo.find_by_id.return_value = _category_term()

    service = _service(junction_repo, term_repo)
    service.assign_category("dataset-1", "term-1")

    junction_repo.add.assert_not_called()


def test_assign_rejects_a_non_category_term():
    junction_repo = MagicMock()
    term_repo = MagicMock()
    wrong = _category_term()
    wrong.term_type = "tag"
    term_repo.find_by_id.return_value = wrong

    service = _service(junction_repo, term_repo)
    try:
        service.assign_category("dataset-1", "term-1")
        raised = False
    except InvalidCategoryTermError:
        raised = True
    assert raised
    junction_repo.add.assert_not_called()


def test_unassign_removes_the_junction():
    junction_repo = MagicMock()
    term_repo = MagicMock()

    service = _service(junction_repo, term_repo)
    service.unassign_category("dataset-1", "term-1")

    junction_repo.remove.assert_called_once_with("dataset-1", "term-1")


def test_bulk_assign_adds_the_term_to_every_dataset():
    junction_repo = MagicMock()
    junction_repo.exists.return_value = False
    term_repo = MagicMock()
    term_repo.find_by_id.return_value = _category_term()

    service = _service(junction_repo, term_repo)
    service.bulk_assign_category(["d-1", "d-2", "d-3"], "term-1")

    added = {call.args[0] for call in junction_repo.add.call_args_list}
    assert added == {"d-1", "d-2", "d-3"}


def test_list_category_index_returns_slug_and_label_per_term():
    junction_repo = MagicMock()
    term_repo = MagicMock()
    term_repo.find_by_type.return_value = [
        _category_term(term_id="term-1", slug="environment"),
        _category_term(term_id="term-2", slug="finance"),
    ]

    service = _service(junction_repo, term_repo)
    index = service.list_category_index()

    term_repo.find_by_type.assert_called_once_with(DATASET_CATEGORY_TERM_TYPE)
    assert {entry["slug"] for entry in index} == {"environment", "finance"}
    assert all("label" in entry and "id" in entry for entry in index)


def test_dataset_ids_for_category_resolves_by_slug():
    junction_repo = MagicMock()
    junction_repo.find_dataset_ids_by_term.return_value = ["d-1", "d-2"]
    term_repo = MagicMock()
    term_repo.find_by_type_and_slug.return_value = _category_term()

    service = _service(junction_repo, term_repo)
    ids = service.dataset_ids_for_category("environment")

    term_repo.find_by_type_and_slug.assert_called_once_with(
        DATASET_CATEGORY_TERM_TYPE, "environment"
    )
    assert ids == ["d-1", "d-2"]


def test_dataset_ids_for_unknown_category_is_empty_not_none():
    junction_repo = MagicMock()
    term_repo = MagicMock()
    term_repo.find_by_type_and_slug.return_value = None
    term_repo.find_by_id.return_value = None

    service = _service(junction_repo, term_repo)
    # Honest "no matches" (an empty id list), never None (which means no filter).
    assert service.dataset_ids_for_category("nonexistent") == []
