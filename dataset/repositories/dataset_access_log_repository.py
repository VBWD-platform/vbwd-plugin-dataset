"""DatasetAccessLogRepository — append-only audit writes (T7).

Mirrors ghrm's ``GhrmAccessLogRepository.log`` so every access transition is
recorded from one place.
"""
from plugins.dataset.dataset.models.dataset_access_log import DatasetAccessLog


class DatasetAccessLogRepository:
    """Writes ``DatasetAccessLog`` audit rows."""

    def __init__(self, session) -> None:
        self.session = session

    def log(self, user_id, dataset_id, action: str, triggered_by: str) -> None:
        entry = DatasetAccessLog(
            user_id=user_id,
            dataset_id=dataset_id,
            action=action,
            triggered_by=triggered_by,
        )
        self.session.add(entry)
        # Commit for the same reason the membership repo does: the audit row is
        # written from event-driven grant paths whose request session is rolled
        # back at teardown, so a flush alone would be lost. Mirrors ghrm's
        # access-log repo (a SAVEPOINT release under the integration fixture).
        self.session.commit()
