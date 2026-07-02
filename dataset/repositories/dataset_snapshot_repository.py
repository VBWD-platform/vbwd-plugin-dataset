"""DatasetSnapshotRepository — data access for dataset snapshots (S110 T2)."""
from typing import List, Optional

from plugins.dataset.dataset.models.dataset_snapshot import DatasetSnapshot


class DatasetSnapshotRepository:
    """CRUD + per-dataset listing for ``DatasetSnapshot`` rows."""

    def __init__(self, session) -> None:
        self.session = session

    def save(self, snapshot: DatasetSnapshot) -> DatasetSnapshot:
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def delete(self, snapshot: DatasetSnapshot) -> None:
        self.session.delete(snapshot)
        self.session.flush()

    def find_by_id(self, snapshot_id: str) -> Optional[DatasetSnapshot]:
        return (
            self.session.query(DatasetSnapshot)
            .filter(DatasetSnapshot.id == snapshot_id)
            .first()
        )

    def find_for_dataset(self, dataset_id: str) -> List[DatasetSnapshot]:
        """Return a dataset's snapshots, newest ``taken_at`` first."""
        return (
            self.session.query(DatasetSnapshot)
            .filter(DatasetSnapshot.dataset_id == dataset_id)
            .order_by(DatasetSnapshot.taken_at.desc())
            .all()
        )
