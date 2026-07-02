"""DatasetAccessService — the single home for dataset access state (T7).

Copied in shape from ghrm's ``GithubAccessService`` (subscription lifecycle
handlers + grace-period revoke), but access here is internal (a
``DatasetMembership`` row, not an external collaborator), so a grant moves
straight to ACTIVE. Both the line-item handler (T6) and the subscription/one-time
event listeners delegate here so access transitions live in one place (DRY).

ghrm is NOT imported — only its pattern is reused.
"""
import logging
from datetime import timedelta
from typing import List

from vbwd.utils.datetime_utils import utcnow

from plugins.dataset.dataset.models.dataset_access_log import DatasetAccessAction
from plugins.dataset.dataset.models.dataset_membership import DatasetMembershipStatus

logger = logging.getLogger(__name__)

DEFAULT_GRACE_PERIOD_DAYS = 7

TRIGGERED_BY_SUBSCRIPTION = "subscription_event"
TRIGGERED_BY_SCHEDULER = "scheduler"


class DatasetAccessService:
    """Grant / grace-revoke / restore a user's access to a dataset."""

    def __init__(
        self,
        membership_repository,
        access_log_repository,
        dataset_plan_repository,
        grace_period_fallback_days: int = DEFAULT_GRACE_PERIOD_DAYS,
    ) -> None:
        self._memberships = membership_repository
        self._access_log = access_log_repository
        self._dataset_plans = dataset_plan_repository
        self._grace_fallback_days = grace_period_fallback_days

    # ------------------------------------------------------------------
    # Direct access transitions (single home — DRY)
    # ------------------------------------------------------------------

    def grant(self, user_id, dataset_id, triggered_by: str = "manual"):
        """Grant (or re-grant) ACTIVE access to a dataset. Idempotent."""
        membership = self._memberships.upsert(
            user_id,
            dataset_id,
            status=DatasetMembershipStatus.ACTIVE.value,
            granted_at=utcnow(),
            grace_expires_at=None,
        )
        self._access_log.log(
            user_id, dataset_id, DatasetAccessAction.GRANT, triggered_by
        )
        return membership

    def restore(self, user_id, dataset_id, triggered_by: str = "manual"):
        """Re-grant access after a reversal/renewal (ACTIVE + clear grace)."""
        membership = self._memberships.upsert(
            user_id,
            dataset_id,
            status=DatasetMembershipStatus.ACTIVE.value,
            granted_at=utcnow(),
            grace_expires_at=None,
        )
        self._access_log.log(
            user_id, dataset_id, DatasetAccessAction.RESTORE, triggered_by
        )
        return membership

    def grace_revoke(
        self, user_id, dataset_id, trailing_days: int = 0, triggered_by: str = "manual"
    ):
        """Move access into GRACE with an expiry (subscription cancel path)."""
        days = trailing_days or self._grace_fallback_days
        membership = self._memberships.upsert(
            user_id,
            dataset_id,
            status=DatasetMembershipStatus.GRACE.value,
            grace_expires_at=utcnow() + timedelta(days=days),
        )
        self._access_log.log(
            user_id, dataset_id, DatasetAccessAction.GRACE_STARTED, triggered_by
        )
        return membership

    def revoke(self, user_id, dataset_id, triggered_by: str = "manual"):
        """Immediately revoke access (refund path)."""
        membership = self._memberships.upsert(
            user_id,
            dataset_id,
            status=DatasetMembershipStatus.REVOKED.value,
            grace_expires_at=None,
        )
        self._access_log.log(
            user_id, dataset_id, DatasetAccessAction.REVOKE, triggered_by
        )
        return membership

    def has_active_access(self, user_id, dataset_id) -> bool:
        """True when the user currently holds access to ``dataset_id``.

        ACTIVE and GRACE both grant access (a grace period is still access);
        REVOKED / INVITED / no-membership do not. This is the single materialised
        access truth written by every grant path (line item, one-time order,
        subscription events), so the read API gates on it (DRY)."""
        membership = self._memberships.find_by_user_and_dataset(user_id, dataset_id)
        if membership is None:
            return False
        return membership.status in (
            DatasetMembershipStatus.ACTIVE.value,
            DatasetMembershipStatus.GRACE.value,
        )

    def active_dataset_ids(self, user_id) -> List:
        """Dataset ids the user holds a live membership for (ACTIVE or GRACE).

        The materialised-membership half of "which datasets may this user access"
        — the single home for reading a user's granted datasets (one-time grants
        and grace). The subscription-derived half comes from
        ``IDatasetEntitlements``; the read routes union the two so "My datasets"
        mirrors :meth:`has_active_access` exactly (DRY, no access logic copied).
        """
        return [
            membership.dataset_id
            for membership in self._memberships.find_active_by_user(user_id)
        ]

    # ------------------------------------------------------------------
    # Subscription event handlers (copied lifecycle from ghrm)
    # ------------------------------------------------------------------

    def on_subscription_activated(self, user_id, plan_id) -> None:
        """Grant every dataset the plan unlocks (no-op when it unlocks none)."""
        for dataset_id in self._datasets_for_plan(plan_id):
            self.grant(user_id, dataset_id, triggered_by=TRIGGERED_BY_SUBSCRIPTION)

    def on_subscription_cancelled(
        self, user_id, plan_id, trailing_days: int = 0
    ) -> None:
        """Move every dataset the plan unlocks into GRACE with an expiry."""
        for dataset_id in self._datasets_for_plan(plan_id):
            self.grace_revoke(
                user_id,
                dataset_id,
                trailing_days=trailing_days,
                triggered_by=TRIGGERED_BY_SUBSCRIPTION,
            )

    def on_subscription_payment_failed(
        self, user_id, plan_id, trailing_days: int = 0
    ) -> None:
        """Start the grace period (same as cancellation)."""
        self.on_subscription_cancelled(user_id, plan_id, trailing_days)

    def on_subscription_renewed(self, user_id, plan_id) -> None:
        """Re-grant access on renewal for every dataset the plan unlocks."""
        for dataset_id in self._datasets_for_plan(plan_id):
            self.restore(user_id, dataset_id, triggered_by=TRIGGERED_BY_SUBSCRIPTION)

    # ------------------------------------------------------------------
    # Grace-period scheduler
    # ------------------------------------------------------------------

    def revoke_expired_grace_access(self) -> int:
        """Revoke every grace-expired membership. Returns the count."""
        expired = self._memberships.find_grace_expired(utcnow())
        for membership in expired:
            self.revoke(
                membership.user_id,
                membership.dataset_id,
                triggered_by=TRIGGERED_BY_SCHEDULER,
            )
        return len(expired)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _datasets_for_plan(self, plan_id):
        """Dataset ids a tariff plan unlocks via the ``DatasetPlan`` links."""
        return [
            dataset_plan.dataset_id
            for dataset_plan in self._dataset_plans.find_all_by_tariff_plan_id(plan_id)
        ]
