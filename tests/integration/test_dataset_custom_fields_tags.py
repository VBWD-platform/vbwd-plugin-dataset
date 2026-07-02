"""T5 — custom fields + tags for the ``dataset`` entity (generic core backend).

The generic tags/custom-fields backend already lives in core
(``vbwd/services/tags_and_custom_fields.py`` + ``vbwd/routes/admin/
tags_custom_fields.py`` + the ``vbwd_tag`` / ``vbwd_custom_field_*`` tables).
The dataset plugin only has to REGISTER its entity type (done in ``on_enable``);
this test proves a custom field can be defined and its value stored/read, and a
tag added/removed, all keyed by the ``dataset`` entity type.
"""
from uuid import uuid4

from vbwd.repositories.custom_field_def_repository import CustomFieldDefRepository
from vbwd.repositories.custom_field_value_repository import (
    CustomFieldValueRepository,
)
from vbwd.services.custom_field_service import CustomFieldService
from vbwd.services.entity_type_registry import is_registered
from vbwd.services.tags_and_custom_fields import resolve_tags_and_custom_fields

from plugins.dataset.dataset.models.dataset import Dataset

DATASET_ENTITY_TYPE = "dataset"


def _new_dataset(db, slug):
    dataset = Dataset()
    dataset.slug = slug
    dataset.title = slug.title()
    dataset.price = 10.0
    db.session.add(dataset)
    db.session.commit()
    return dataset


def _custom_field_service(db):
    return CustomFieldService(
        def_repo=CustomFieldDefRepository(db.session),
        value_repo=CustomFieldValueRepository(db.session),
    )


def test_dataset_entity_type_is_registered(db):
    # on_enable ran via the conftest enable-guard; the generic value endpoints
    # only accept a registered entity type.
    assert is_registered(DATASET_ENTITY_TYPE)


def test_define_custom_field_then_set_and_read_value(db):
    dataset = _new_dataset(db, f"cf-{uuid4().hex[:6]}")

    _custom_field_service(db).create_def(
        entity_type=DATASET_ENTITY_TYPE,
        key="licence",
        label="Licence",
        field_type="text",
    )
    db.session.commit()

    port = resolve_tags_and_custom_fields()
    port.set_custom_fields(DATASET_ENTITY_TYPE, dataset.id, {"licence": "CC-BY-4.0"})
    db.session.commit()

    values = port.get_custom_fields(DATASET_ENTITY_TYPE, dataset.id)
    assert values.get("licence") == "CC-BY-4.0"


def test_add_and_remove_a_tag(db):
    dataset = _new_dataset(db, f"tag-{uuid4().hex[:6]}")
    port = resolve_tags_and_custom_fields()

    port.set_tags(DATASET_ENTITY_TYPE, dataset.id, ["public", "air"])
    db.session.commit()
    assert set(port.get_tags(DATASET_ENTITY_TYPE, dataset.id)) == {"public", "air"}

    # Full-replace with a smaller set drops the removed tag.
    port.set_tags(DATASET_ENTITY_TYPE, dataset.id, ["public"])
    db.session.commit()
    assert set(port.get_tags(DATASET_ENTITY_TYPE, dataset.id)) == {"public"}
