"""T7 — DatasetAccessService lifecycle (grant/grace/revoke/restore + events).

Pure unit tests: the repositories are MagicMocks, so no DB. Assert the access
transitions and that the subscription-event handlers project a plan onto its
linked datasets and drive the right transition (copied ghrm lifecycle, no ghrm
import).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from plugins.dataset.dataset.models.dataset_membership import DatasetMembershipStatus
from plugins.dataset.dataset.services.dataset_access_service import (
    DatasetAccessService,
)


def _make_service(dataset_ids_for_plan=None):
    membership_repo = MagicMock()
    membership_repo.upsert.return_value = MagicMock()
    access_log_repo = MagicMock()
    plan_repo = MagicMock()
    plan_repo.find_all_by_tariff_plan_id.return_value = [
        SimpleNamespace(dataset_id=dataset_id)
        for dataset_id in (dataset_ids_for_plan or [])
    ]
    service = DatasetAccessService(
        membership_repository=membership_repo,
        access_log_repository=access_log_repo,
        dataset_plan_repository=plan_repo,
        grace_period_fallback_days=7,
    )
    return service, membership_repo, access_log_repo, plan_repo


def _upsert_status(membership_repo):
    return membership_repo.upsert.call_args.kwargs["status"]


def test_grant_sets_active_and_logs():
    service, membership_repo, access_log_repo, _ = _make_service()
    user_id, dataset_id = uuid4(), uuid4()

    service.grant(user_id, dataset_id, triggered_by="line_item")

    assert _upsert_status(membership_repo) == DatasetMembershipStatus.ACTIVE.value
    assert membership_repo.upsert.call_args.kwargs["grace_expires_at"] is None
    access_log_repo.log.assert_called_once()


def test_grace_revoke_sets_grace_with_expiry():
    service, membership_repo, _, _ = _make_service()

    service.grace_revoke(uuid4(), uuid4(), trailing_days=3)

    assert _upsert_status(membership_repo) == DatasetMembershipStatus.GRACE.value
    assert membership_repo.upsert.call_args.kwargs["grace_expires_at"] is not None


def test_revoke_sets_revoked():
    service, membership_repo, _, _ = _make_service()

    service.revoke(uuid4(), uuid4())

    assert _upsert_status(membership_repo) == DatasetMembershipStatus.REVOKED.value


def test_restore_sets_active():
    service, membership_repo, _, _ = _make_service()

    service.restore(uuid4(), uuid4())

    assert _upsert_status(membership_repo) == DatasetMembershipStatus.ACTIVE.value


def test_on_subscription_activated_grants_every_linked_dataset():
    dataset_a, dataset_b = uuid4(), uuid4()
    service, membership_repo, _, plan_repo = _make_service([dataset_a, dataset_b])
    user_id, plan_id = uuid4(), uuid4()

    service.on_subscription_activated(user_id, plan_id)

    plan_repo.find_all_by_tariff_plan_id.assert_called_once_with(plan_id)
    granted = {call.args[1] for call in membership_repo.upsert.call_args_list}
    assert granted == {dataset_a, dataset_b}
    for call in membership_repo.upsert.call_args_list:
        assert call.kwargs["status"] == DatasetMembershipStatus.ACTIVE.value


def test_on_subscription_cancelled_grace_revokes_linked_datasets():
    dataset_id = uuid4()
    service, membership_repo, _, _ = _make_service([dataset_id])

    service.on_subscription_cancelled(uuid4(), uuid4(), trailing_days=5)

    assert _upsert_status(membership_repo) == DatasetMembershipStatus.GRACE.value


def test_on_subscription_renewed_restores_linked_datasets():
    dataset_id = uuid4()
    service, membership_repo, _, _ = _make_service([dataset_id])

    service.on_subscription_renewed(uuid4(), uuid4())

    assert _upsert_status(membership_repo) == DatasetMembershipStatus.ACTIVE.value


def test_activation_for_plan_without_link_is_a_no_op():
    """The disabled/unlinked path must not raise (Liskov) and grant nothing."""
    service, membership_repo, _, _ = _make_service([])

    service.on_subscription_activated(uuid4(), uuid4())

    membership_repo.upsert.assert_not_called()


def test_active_dataset_ids_returns_live_membership_datasets():
    """The materialised-membership half of "which datasets may this user access"."""
    service, membership_repo, _, _ = _make_service()
    dataset_a, dataset_b = uuid4(), uuid4()
    membership_repo.find_active_by_user.return_value = [
        SimpleNamespace(dataset_id=dataset_a),
        SimpleNamespace(dataset_id=dataset_b),
    ]
    user_id = uuid4()

    dataset_ids = service.active_dataset_ids(user_id)

    membership_repo.find_active_by_user.assert_called_once_with(user_id)
    assert set(dataset_ids) == {dataset_a, dataset_b}


def test_revoke_expired_grace_access_revokes_each_expired():
    service, membership_repo, _, _ = _make_service()
    expired = [
        SimpleNamespace(user_id=uuid4(), dataset_id=uuid4()),
        SimpleNamespace(user_id=uuid4(), dataset_id=uuid4()),
    ]
    membership_repo.find_grace_expired.return_value = expired

    count = service.revoke_expired_grace_access()

    assert count == 2
    statuses = {call.kwargs["status"] for call in membership_repo.upsert.call_args_list}
    assert statuses == {DatasetMembershipStatus.REVOKED.value}


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
