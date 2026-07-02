"""T7 — DatasetEntitlementService projects active plans onto entitled datasets.

The ``IDatasetEntitlements.active_dataset_ids`` port composes the subscription
entitlements port (``active_plan_ids``) with the dataset↔plan links (DIP): no
ghrm import, no subscription model import.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from plugins.dataset.dataset.services.dataset_entitlement_service import (
    DatasetEntitlementService,
)


def test_active_dataset_ids_maps_active_plans_to_datasets():
    plan_a, plan_b = uuid4(), uuid4()
    dataset_a, dataset_b, dataset_c = uuid4(), uuid4(), uuid4()

    subscription_entitlements = MagicMock()
    subscription_entitlements.active_plan_ids.return_value = [plan_a, plan_b]

    plan_repo = MagicMock()
    links = {
        plan_a: [SimpleNamespace(dataset_id=dataset_a)],
        plan_b: [
            SimpleNamespace(dataset_id=dataset_b),
            SimpleNamespace(dataset_id=dataset_c),
        ],
    }
    plan_repo.find_all_by_tariff_plan_id.side_effect = lambda plan_id: links[plan_id]

    service = DatasetEntitlementService(subscription_entitlements, plan_repo)
    user_id = uuid4()

    result = set(service.active_dataset_ids(user_id))

    subscription_entitlements.active_plan_ids.assert_called_once_with(user_id)
    assert result == {dataset_a, dataset_b, dataset_c}


def test_no_active_plans_yields_no_datasets():
    subscription_entitlements = MagicMock()
    subscription_entitlements.active_plan_ids.return_value = []
    plan_repo = MagicMock()

    service = DatasetEntitlementService(subscription_entitlements, plan_repo)

    assert service.active_dataset_ids(uuid4()) == []
    plan_repo.find_all_by_tariff_plan_id.assert_not_called()
