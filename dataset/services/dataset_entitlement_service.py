"""DatasetEntitlementService — projects active plans onto entitled datasets (T7).

Implements the dataset-owned ``IDatasetEntitlements`` port by composing the
subscription entitlements port (``active_plan_ids``) with the dataset↔plan grant
links. This is the single read home for "which datasets may this user access
right now", derived live from the subscription read model (DIP) — no ghrm import,
no subscription model import (the concrete is injected at the composition root).
"""
from typing import List
from uuid import UUID

from plugins.dataset.dataset.services.ports import (
    IDatasetEntitlements,
    ISubscriptionEntitlements,
)


class DatasetEntitlementService(IDatasetEntitlements):
    """Maps a user's active tariff plans to the datasets those plans unlock."""

    def __init__(
        self,
        subscription_entitlements: ISubscriptionEntitlements,
        dataset_plan_repository,
    ) -> None:
        self._subscription_entitlements = subscription_entitlements
        self._dataset_plans = dataset_plan_repository

    def active_dataset_ids(self, user_id: UUID) -> List[UUID]:
        dataset_ids = set()
        for plan_id in self._subscription_entitlements.active_plan_ids(user_id):
            for dataset_plan in self._dataset_plans.find_all_by_tariff_plan_id(plan_id):
                dataset_ids.add(dataset_plan.dataset_id)
        return list(dataset_ids)
