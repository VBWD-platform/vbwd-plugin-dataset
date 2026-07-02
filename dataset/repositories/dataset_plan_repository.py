"""DatasetPlanRepository — data access for dataset ↔ plan grant links (T7).

Mirrors ghrm's ``find_by_tariff_plan_id`` lookup so the access lifecycle can go
plan_id → dataset(s). Unlike ghrm a plan may unlock several datasets, so the
lookup returns a list.
"""
from typing import List, Optional
from uuid import UUID

from plugins.dataset.dataset.models.dataset_plan import DatasetPlan


class DatasetPlanRepository:
    """CRUD + plan/dataset lookups for ``DatasetPlan`` rows."""

    def __init__(self, session) -> None:
        self.session = session

    def save(self, dataset_plan: DatasetPlan) -> DatasetPlan:
        self.session.add(dataset_plan)
        self.session.flush()
        return dataset_plan

    def delete(self, dataset_plan: DatasetPlan) -> None:
        self.session.delete(dataset_plan)
        self.session.flush()

    def find_by_id(self, dataset_plan_id: str) -> Optional[DatasetPlan]:
        return (
            self.session.query(DatasetPlan)
            .filter(DatasetPlan.id == dataset_plan_id)
            .first()
        )

    def find_all_by_tariff_plan_id(self, plan_id) -> List[DatasetPlan]:
        """Every dataset grant link for a tariff plan (empty list if none).

        Accepts a UUID or a UUID-string (event payloads carry strings); a
        non-UUID value resolves to no links rather than raising.
        """
        try:
            resolved = UUID(str(plan_id))
        except (ValueError, TypeError):
            return []
        return (
            self.session.query(DatasetPlan)
            .filter(DatasetPlan.tariff_plan_id == resolved)
            .all()
        )

    def find_by_dataset_id(self, dataset_id) -> List[DatasetPlan]:
        return (
            self.session.query(DatasetPlan)
            .filter(DatasetPlan.dataset_id == dataset_id)
            .all()
        )
