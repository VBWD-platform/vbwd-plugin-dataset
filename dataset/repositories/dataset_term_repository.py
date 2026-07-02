"""DatasetTermRepository — data access for the dataset↔term junction (T4)."""
from typing import List

from plugins.dataset.dataset.models.dataset_term import DatasetTerm


class DatasetTermRepository:
    """CRUD-ish access for ``dataset_term`` junction rows."""

    def __init__(self, session) -> None:
        self.session = session

    def exists(self, dataset_id: str, term_id: str) -> bool:
        return (
            self.session.query(DatasetTerm)
            .filter(
                DatasetTerm.dataset_id == dataset_id,
                DatasetTerm.term_id == term_id,
            )
            .first()
            is not None
        )

    def add(self, dataset_id: str, term_id: str) -> DatasetTerm:
        link = DatasetTerm()
        link.dataset_id = dataset_id
        link.term_id = term_id
        self.session.add(link)
        self.session.flush()
        return link

    def remove(self, dataset_id: str, term_id: str) -> None:
        self.session.query(DatasetTerm).filter(
            DatasetTerm.dataset_id == dataset_id,
            DatasetTerm.term_id == term_id,
        ).delete(synchronize_session="fetch")
        self.session.flush()

    def find_term_ids_by_dataset(self, dataset_id: str) -> List[str]:
        rows = (
            self.session.query(DatasetTerm.term_id)
            .filter(DatasetTerm.dataset_id == dataset_id)
            .all()
        )
        return [str(row[0]) for row in rows]

    def find_dataset_ids_by_term(self, term_id: str) -> List[str]:
        rows = (
            self.session.query(DatasetTerm.dataset_id)
            .filter(DatasetTerm.term_id == term_id)
            .all()
        )
        return [str(row[0]) for row in rows]
