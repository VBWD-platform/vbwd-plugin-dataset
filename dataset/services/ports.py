"""Dataset-owned ports (DIP) — copied in spirit from ghrm's ports.

The dataset plugin depends on the *narrow abstractions it needs*, never on
another plugin's concrete classes. The subscription concrete is wired only at the
composition root (``plugins/dataset/__init__.py``) via an adapter that satisfies
``ISubscriptionEntitlements`` — the single, declared dataset→subscription seam
(dataset declares ``dependencies=["subscription", "cms"]``).

ghrm is NOT imported; these are the dataset plugin's own ports.
"""
from typing import List, Protocol
from uuid import UUID


class ISubscriptionEntitlements(Protocol):
    """Which tariff plans is this user actively entitled to right now?"""

    def active_plan_ids(self, user_id: UUID) -> List[UUID]:
        ...


class IDatasetEntitlements(Protocol):
    """Which datasets is this user actively entitled to right now?

    The scoped read API (T8) and the user dashboard depend on this narrow port
    rather than on the subscription read model directly (ISP + DIP).
    """

    def active_dataset_ids(self, user_id: UUID) -> List[UUID]:
        ...
