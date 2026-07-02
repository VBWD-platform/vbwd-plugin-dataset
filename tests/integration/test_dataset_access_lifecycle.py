"""T6/T7 — dataset entitlement + access lifecycle against the real DB.

Exercises the real Flask app + PostgreSQL (rolled-back per test):

* the access-state transitions on real repositories (grant → ACTIVE,
  grace-revoke → GRACE, scheduler → REVOKED, restore → ACTIVE);
* the copied ghrm subscription lifecycle through the plugin's own EventBus
  handlers (activate → ACTIVE, cancel → GRACE, renew → ACTIVE);
* the Liskov guard: publishing a subscription event the plugin cannot map never
  raises for the subscription caller and grants nothing.
"""
from datetime import timedelta
from uuid import uuid4

import pytest

from vbwd.events.bus import EventBus
from vbwd.models.enums import UserRole, UserStatus
from vbwd.models.user import User
from vbwd.utils.datetime_utils import utcnow

from plugins.dataset import (
    DatasetPlugin,
    build_dataset_access_service,
)
from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.models.dataset_membership import (
    DatasetMembershipStatus,
)
from plugins.dataset.dataset.models.dataset_plan import DatasetPlan
from plugins.dataset.dataset.repositories.dataset_membership_repository import (
    DatasetMembershipRepository,
)


def _make_user(db):
    user = User(
        id=uuid4(),
        email=f"buyer-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _make_dataset(db, slug=None):
    dataset = Dataset()
    dataset.slug = slug or f"ds-{uuid4().hex[:8]}"
    dataset.title = "Air Quality"
    dataset.price = 100.0
    db.session.add(dataset)
    db.session.commit()
    return dataset


def _make_plan(db):
    from plugins.subscription.subscription.models.tarif_plan import (
        BillingPeriod,
        TarifPlan,
    )

    plan = TarifPlan(
        id=uuid4(),
        name="Data Access",
        slug=f"plan-{uuid4().hex[:8]}",
        price=100.0,
        billing_period=BillingPeriod.MONTHLY,
    )
    db.session.add(plan)
    db.session.commit()
    return plan


def _link(db, dataset, plan):
    link = DatasetPlan(dataset_id=dataset.id, tariff_plan_id=plan.id)
    db.session.add(link)
    db.session.commit()
    return link


def _status(db, user, dataset):
    membership = DatasetMembershipRepository(db.session).find_by_user_and_dataset(
        user.id, dataset.id
    )
    return membership.status if membership else None


def test_direct_access_lifecycle_persists_transitions(db):
    user = _make_user(db)
    dataset = _make_dataset(db)
    service = build_dataset_access_service()

    service.grant(user.id, dataset.id, triggered_by="line_item")
    db.session.commit()
    assert _status(db, user, dataset) == DatasetMembershipStatus.ACTIVE.value

    service.grace_revoke(user.id, dataset.id, trailing_days=7)
    db.session.commit()
    assert _status(db, user, dataset) == DatasetMembershipStatus.GRACE.value

    # Backdate the grace expiry, then let the scheduler revoke it.
    membership = DatasetMembershipRepository(db.session).find_by_user_and_dataset(
        user.id, dataset.id
    )
    membership.grace_expires_at = utcnow() - timedelta(days=1)
    db.session.commit()

    revoked = service.revoke_expired_grace_access()
    db.session.commit()
    assert revoked == 1
    assert _status(db, user, dataset) == DatasetMembershipStatus.REVOKED.value

    service.restore(user.id, dataset.id)
    db.session.commit()
    assert _status(db, user, dataset) == DatasetMembershipStatus.ACTIVE.value


def test_subscription_events_drive_membership_via_plugin_handlers(db):
    user = _make_user(db)
    dataset = _make_dataset(db)
    plan = _make_plan(db)
    _link(db, dataset, plan)

    # Wire the plugin's real handlers onto an isolated bus (deterministic —
    # does not depend on the global enable state).
    bus = EventBus()
    plugin = DatasetPlugin()
    plugin.initialize({})
    plugin.register_event_handlers(bus)

    bus.publish(
        "subscription.activated",
        {"user_id": str(user.id), "plan_id": str(plan.id)},
    )
    db.session.commit()
    assert _status(db, user, dataset) == DatasetMembershipStatus.ACTIVE.value

    bus.publish(
        "subscription.cancelled",
        {"user_id": str(user.id), "plan_id": str(plan.id)},
    )
    db.session.commit()
    assert _status(db, user, dataset) == DatasetMembershipStatus.GRACE.value

    bus.publish(
        "subscription.renewed",
        {"user_id": str(user.id), "plan_id": str(plan.id)},
    )
    db.session.commit()
    assert _status(db, user, dataset) == DatasetMembershipStatus.ACTIVE.value


def test_subscription_event_for_unlinked_plan_never_raises(db):
    """Liskov — the subscription caller (publisher) is never broken by dataset."""
    user = _make_user(db)
    dataset = _make_dataset(db)  # deliberately NOT linked to the plan below

    bus = EventBus()
    plugin = DatasetPlugin()
    plugin.initialize({})
    plugin.register_event_handlers(bus)

    # No DatasetPlan link exists for this plan id → must be a silent no-op.
    bus.publish(
        "subscription.activated",
        {"user_id": str(user.id), "plan_id": str(uuid4())},
    )
    db.session.commit()
    assert _status(db, user, dataset) is None


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
