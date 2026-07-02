"""T2 — Dataset conforms to the core Priceable protocol."""
from uuid import uuid4

from vbwd.pricing.price import Price
from vbwd.pricing.price_factory import PriceFactory
from vbwd.pricing.priceable import Priceable

from plugins.dataset.dataset.models.dataset import Dataset


class _FakeCurrency:
    code = "EUR"


class _FakeCurrencyService:
    def get_default_currency(self):
        return _FakeCurrency()


def _dataset() -> Dataset:
    dataset = Dataset()
    dataset.id = uuid4()
    dataset.slug = "air-quality"
    dataset.title = "Air Quality"
    dataset.price = 100.0
    dataset.taxes = []
    return dataset


def test_dataset_exposes_raw_price_float():
    dataset = _dataset()
    assert dataset.raw_price == 100.0
    assert isinstance(dataset.raw_price, float)


def test_dataset_satisfies_priceable_protocol():
    assert isinstance(_dataset(), Priceable)


def test_price_factory_returns_a_valid_price_for_a_dataset():
    factory = PriceFactory(lambda: {}, _FakeCurrencyService())
    price = factory.get_price_from_object(_dataset())

    assert isinstance(price, Price)
    assert price.netto == 100.0
    assert price.brutto == 100.0  # no taxes assigned
    assert price.currency == "EUR"


def test_to_dict_serializes_core_fields():
    payload = _dataset().to_dict()
    assert payload["slug"] == "air-quality"
    assert payload["price"] == 100.0
    assert payload["last_snapshot_id"] is None
    assert payload["taxes"] == []
