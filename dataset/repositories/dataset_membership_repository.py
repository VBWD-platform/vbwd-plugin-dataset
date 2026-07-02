"""DatasetMembershipRepository — data access for per-(user, dataset) access (T7).

Mirrors ghrm's ``GhrmRepoMembershipRepository`` (``upsert`` / ``find_by_user`` /
``find_grace_expired``) so the access service reads/writes one row per
(user, dataset).
"""
from datetime import datetime
from typing import List, Optional

from plugins.dataset.dataset.models.dataset_membership import (
    DatasetMembership,
    DatasetMembershipStatus,
)


class DatasetMembershipRepository:
    """Upsert + lookups for ``DatasetMembership`` rows."""

    def __init__(self, session) -> None:
        self.session = session

    def upsert(self, user_id, dataset_id, **fields) -> DatasetMembership:
        """Insert a membership for ``(user_id, dataset_id)`` or update in place.

        The pair is unique, so there is never more than one row per
        (user, dataset).
        """
        membership = self.find_by_user_and_dataset(user_id, dataset_id)
        if membership is None:
            membership = DatasetMembership(user_id=user_id, dataset_id=dataset_id)
            self.session.add(membership)
        for field_name, field_value in fields.items():
            setattr(membership, field_name, field_value)
        # Commit (not merely flush): the event-driven grant paths (the
        # ``invoice.paid`` one-time handler, the line-item activation handler and
        # the subscription-lifecycle subscribers) run inside the capture
        # request's context, whose scoped session is *removed* — i.e. rolled
        # back — at teardown rather than committed. A flush alone would be lost
        # unless some unrelated downstream subscriber happened to commit the
        # shared session. Mirrors ghrm/booking, whose access repos commit their
        # own writes so the grant is durable regardless of the caller. Under the
        # integration ``rollback_isolation`` fixture this commit is a SAVEPOINT
        # release, so test isolation is preserved.
        self.session.commit()
        return membership

    def find_by_user_and_dataset(
        self, user_id, dataset_id
    ) -> Optional[DatasetMembership]:
        return (
            self.session.query(DatasetMembership)
            .filter(
                DatasetMembership.user_id == user_id,
                DatasetMembership.dataset_id == dataset_id,
            )
            .first()
        )

    def find_by_user(self, user_id) -> List[DatasetMembership]:
        return (
            self.session.query(DatasetMembership)
            .filter(DatasetMembership.user_id == user_id)
            .all()
        )

    def find_active_by_user(self, user_id) -> List[DatasetMembership]:
        """Memberships that currently grant access (ACTIVE or in GRACE)."""
        live_statuses = (
            DatasetMembershipStatus.ACTIVE.value,
            DatasetMembershipStatus.GRACE.value,
        )
        return (
            self.session.query(DatasetMembership)
            .filter(
                DatasetMembership.user_id == user_id,
                DatasetMembership.status.in_(live_statuses),
            )
            .all()
        )

    def find_grace_expired(self, now: datetime) -> List[DatasetMembership]:
        return (
            self.session.query(DatasetMembership)
            .filter(
                DatasetMembership.status == DatasetMembershipStatus.GRACE.value,
                DatasetMembership.grace_expires_at <= now,
            )
            .all()
        )
