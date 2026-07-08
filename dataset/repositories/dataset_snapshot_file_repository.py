"""DatasetSnapshotFileRepository — data access for issue companion files (S124).

Mirrors :class:`DatasetSnapshotRepository`: add + per-snapshot listing + lookup
+ delete for ``dataset_snapshot_file`` rows.
"""
from typing import List, Optional

from plugins.dataset.dataset.models.dataset_snapshot_file import DatasetSnapshotFile


class DatasetSnapshotFileRepository:
    """CRUD + per-snapshot listing for ``DatasetSnapshotFile`` rows."""

    def __init__(self, session) -> None:
        self.session = session

    def add(self, snapshot_file: DatasetSnapshotFile) -> DatasetSnapshotFile:
        self.session.add(snapshot_file)
        self.session.flush()
        return snapshot_file

    def delete(self, snapshot_file: DatasetSnapshotFile) -> None:
        self.session.delete(snapshot_file)
        self.session.flush()

    def find_by_id(self, file_id: str) -> Optional[DatasetSnapshotFile]:
        return (
            self.session.query(DatasetSnapshotFile)
            .filter(DatasetSnapshotFile.id == file_id)
            .first()
        )

    def find_for_snapshot(self, snapshot_id: str) -> List[DatasetSnapshotFile]:
        """Return a snapshot's companion files, oldest ``created_at`` first."""
        return (
            self.session.query(DatasetSnapshotFile)
            .filter(DatasetSnapshotFile.snapshot_id == snapshot_id)
            .order_by(DatasetSnapshotFile.created_at.asc())
            .all()
        )
