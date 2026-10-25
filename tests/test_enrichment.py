"""Injectable providers, the shared cached-lookup engine, and batched external calls."""

import sys
import threading
import types

import pandas as pd
import pytest

from bynat.caching import CacheManager
from bynat.cleaning.enrich import (
    Geocoder,
    GoogleTranslatorProvider,
    RateSource,
    Translator,
    YFinanceRateSource,
    engine,
)
from bynat.cleaning.enrich.engine import SKIP, _cached_map, _rate_limited_map


class DictTranslator:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def translate(self, text, target_lang):
        self.calls.append((text, target_lang))
        return self.mapping.get(text)


class BatchTranslator(DictTranslator):
    def __init__(self, mapping):
        super().__init__(mapping)
        self.batches = []

    def translate(self, text, target_lang):
        pytest.fail("translate_batch should be preferred")

    def translate_batch(self, texts, target_lang):
        self.batches.append((list(texts), target_lang))
        return [self.mapping.get(text) for text in texts]


class DictGeocoder:
    def __init__(self, known):
        self.known = known
        self.calls = []

    def geocode(self, query):
        self.calls.append(query)
        return self.known.get(query)


class FakeRates:
    def __init__(self, rates, fail=()):
        self.rates = rates
        self.fail = set(fail)
        self.calls = []

    def yearly_rates(self, currency, start_year, end_year):
        self.calls.append((currency, start_year, end_year))
        if currency in self.fail:
            raise ConnectionError("offline")
        return {
            year: rate
            for (code, year), rate in self.rates.items()
            if code == currency and start_year <= year <= end_year
        }


def _install(monkeypatch, name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def _install_currency_lookup(monkeypatch):
    class Country:
        def __init__(self, alpha_2):
            self.alpha_2 = alpha_2

    def search_fuzzy(name):
        if name == "Atlantis":
            raise LookupError(name)
        return [Country({"Egypt": "EG", "United States": "US", "France": "FR"}[name])]

    _install(monkeypatch, "pycountry", countries=types.SimpleNamespace(search_fuzzy=search_fuzzy))
    _install(monkeypatch, "babel")
    _install(
        monkeypatch,
        "babel.numbers",
        get_territory_currencies=lambda alpha_2: {"EG": ["EGP"], "US": ["USD"], "FR": ["EUR"]}[alpha_2],
    )


def test_default_providers_satisfy_the_protocols():
    assert issubclass(DictTranslator, Translator)
    assert issubclass(DictGeocoder, Geocoder)
    assert issubclass(FakeRates, RateSource)


# ---------------------------------------------------------------------------
# translation
# ---------------------------------------------------------------------------


def test_translate_categorical_column_accepts_a_plain_provider(tmp_path):
    translator = DictTranslator({"مكتمل": "Completed", "قيد الانتظار": "Pending"})
    frame = pd.DataFrame({"status": ["مكتمل", "قيد الانتظار", "مكتمل", None]})

    result = frame.bynat.with_cache(tmp_path).translate_categorical_column(
        "status", target_lang="en", provider=translator
    )

    assert result["status"].tolist()[:3] == ["Completed", "Pending", "Completed"]
    assert sorted(translator.calls) == sorted([("مكتمل", "en"), ("قيد الانتظار", "en")])


def test_translate_skips_and_does_not_cache_empty_translations(tmp_path):
    translator = DictTranslator({"a": "A", "b": ""})
    frame = pd.DataFrame({"letter": ["a", "b"]})

    result = frame.bynat.with_cache(tmp_path).translate_categorical_column("letter", provider=translator)
    frame.bynat.with_cache(tmp_path).translate_categorical_column("letter", provider=translator)

    assert result["letter"].tolist() == ["A", "b"]
    assert [call[0] for call in translator.calls] == ["a", "b", "b"]


def test_translate_batch_is_used_in_save_every_chunks(tmp_path):
    translator = BatchTranslator({value: value.upper() for value in "abcde"})
    frame = pd.DataFrame({"letter": list("abcdea")})

    result = frame.bynat.with_cache(tmp_path).translate_categorical_column(
        "letter", save_every=2, provider=translator
    )

    assert result["letter"].tolist() == list("ABCDEA")
    assert translator.batches == [(["a", "b"], "en"), (["c", "d"], "en"), (["e"], "en")]


def test_default_google_provider_uses_one_translator_per_thread(monkeypatch):
    created = []

    class GoogleTranslator:
        def __init__(self, source, target):
            created.append((threading.get_ident(), source, target))

        def translate(self, text):
            return text.upper()

    _install(monkeypatch, "deep_translator", GoogleTranslator=GoogleTranslator)
    provider = GoogleTranslatorProvider()

    assert provider.translate("a", "en") == "A"
    assert provider.translate("b", "en") == "B"
    assert provider.translate("c", "fr") == "C"
    assert [entry[1:] for entry in created] == [("auto", "en"), ("auto", "fr")]

    worker = threading.Thread(target=provider.translate, args=("d", "en"))
    worker.start()
    worker.join()

    assert len(created) == 3
    assert created[-1][0] != created[0][0]


def test_rate_limited_map_runs_calls_concurrently():
    barrier = threading.Barrier(3, timeout=5)

    def call(item):
        barrier.wait()
        return item * 2

    assert _rate_limited_map(call, [1, 2, 3], max_workers=3) == [2, 4, 6]


def test_rate_limited_map_spaces_call_starts(monkeypatch):
    clock = {"now": 100.0}
    sleeps = []

    def sleep(seconds):
        sleeps.append(round(seconds, 6))
        clock["now"] += seconds

    monkeypatch.setattr(engine.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(engine.time, "sleep", sleep)

    results = _rate_limited_map(lambda item: item, ["a", "b", "c"], max_workers=1, min_interval=0.2)

    assert results == ["a", "b", "c"]
    assert sleeps == [0.2, 0.2]


def test_rate_limited_map_returns_exceptions_in_place():
    def call(item):
        if item == 2:
            raise RuntimeError("boom")
        return item

    results = _rate_limited_map(call, [1, 2, 3], max_workers=2)

    assert results[0] == 1 and results[2] == 3
    assert isinstance(results[1], RuntimeError)


def _install_langdetect(monkeypatch, detect):
    class DetectorFactory:
        seed = None

    _install(monkeypatch, "langdetect", DetectorFactory=DetectorFactory, detect=detect)


def test_translate_conditional_column_sampling_is_reproducible(monkeypatch, tmp_path):
    seen = []
    _install_langdetect(monkeypatch, lambda text: seen.append(text) or "en")
    frame = pd.DataFrame(
        {
            "partition": ["p1"] * 20 + ["p2"] * 20,
            "description": [f"text {i}" for i in range(40)],
            "label": ["x"] * 40,
        }
    )

    def run(random_state):
        seen.clear()
        frame.bynat.with_cache(tmp_path).translate_conditional_column(
            "partition", "description", "label", sample_size=3, number_of_samples=4, random_state=random_state
        )
        return list(seen)

    first = run(0)
    assert run(0) == first
    assert run(1) != first
    # One generator feeds every sample, so repeated samples differ.
    assert len(set(first[:4])) > 1
    # Partitions are sampled in first-seen order, each from its own rows.
    row_numbers = [[int(part.split()[1]) for part in sample.split(" . ")] for sample in first]
    assert all(number < 20 for sample in row_numbers[:4] for number in sample)
    assert all(number >= 20 for sample in row_numbers[4:] for number in sample)


def test_translate_conditional_column_translates_only_foreign_partitions(monkeypatch, tmp_path):
    _install_langdetect(monkeypatch, lambda text: "en" if "hello" in text else "ar")
    translator = DictTranslator({"قطة": "cat"})
    frame = pd.DataFrame(
        {
            "partition": ["en", "en", "ar", "ar", None],
            "description": ["hello", "hello there", "مرحبا", "مرحبا بك", "x"],
            "label": ["dog", "dog", "قطة", "قطة", "قطة"],
        }
    )

    result = frame.bynat.with_cache(tmp_path).translate_conditional_column(
        "partition", "description", "label", sample_size=2, number_of_samples=2, provider=translator
    )

    assert result["label"].tolist() == ["Dog", "Dog", "Cat", "Cat", "Cat"]
    assert translator.calls == [("قطة", "en")]


def test_translate_conditional_column_accepts_a_generator(monkeypatch, tmp_path):
    import numpy as np

    _install_langdetect(monkeypatch, lambda text: "en")
    frame = pd.DataFrame({"p": ["a"] * 5, "d": list("vwxyz"), "t": ["x"] * 5})

    frame.bynat.with_cache(tmp_path).translate_conditional_column(
        "p", "d", "t", sample_size=2, random_state=np.random.default_rng(3)
    )


# ---------------------------------------------------------------------------
# geocoding
# ---------------------------------------------------------------------------


def test_fill_missing_coordinates_accepts_a_plain_provider(tmp_path):
    geocoder = DictGeocoder({"Springfield, USA": (39.8, -89.6)})
    frame = pd.DataFrame(
        {
            "street": ["1 Main St", "Nowhere Rd"],
            "city": ["Springfield", "Atlantis"],
            "country": ["USA", None],
            "lat": [None, None],
            "lng": [None, None],
        }
    )

    result = frame.bynat.with_cache(tmp_path).fill_missing_coordinates(
        "street", "city", "country", lat_col="lat", long_col="lng", provider=geocoder
    )

    assert result[["lat", "lng"]].iloc[0].tolist() == [39.8, -89.6]
    assert result[["lat", "lng"]].iloc[1].isna().all()
    assert geocoder.calls == ["1 Main St, Springfield, USA", "Springfield, USA", "Nowhere Rd, Atlantis", "Atlantis"]

    geocoder.calls.clear()
    frame.bynat.with_cache(tmp_path).fill_missing_coordinates(
        "street", "city", "country", lat_col="lat", long_col="lng", provider=geocoder, retry_not_found=True
    )
    assert geocoder.calls == ["Nowhere Rd, Atlantis", "Atlantis"]


def test_geocoding_errors_are_not_cached(tmp_path):
    class FlakyGeocoder:
        calls = 0

        def geocode(self, query):
            FlakyGeocoder.calls += 1
            if FlakyGeocoder.calls == 1:
                raise TimeoutError("slow")
            return (1.0, 2.0)

    frame = pd.DataFrame({"city": ["Paris"], "lat": [None], "lng": [None]})
    accessor = frame.bynat.with_cache(tmp_path)

    first = accessor.fill_missing_coordinates("city", lat_col="lat", long_col="lng", provider=FlakyGeocoder())
    second = accessor.fill_missing_coordinates("city", lat_col="lat", long_col="lng", provider=FlakyGeocoder())

    assert first["lat"].isna().all()
    assert second[["lat", "lng"]].iloc[0].tolist() == [1.0, 2.0]


# ---------------------------------------------------------------------------
# currency
# ---------------------------------------------------------------------------


def test_convert_to_usd_downloads_once_per_currency(monkeypatch, tmp_path):
    _install_currency_lookup(monkeypatch)
    rates = FakeRates({("EGP", 2019): 17.0, ("EGP", 2021): 20.0, ("EUR", 2020): 0.5})
    frame = pd.DataFrame(
        {
            "country": ["Egypt", "Egypt", "Egypt", "France", "United States", "Atlantis"],
            "year": [2020, 2021, 2019, 2020, 2020, 2020],
            "salary": [170.0, 200.0, 34.0, 10.0, 100.0, 1.0],
        }
    )

    result = frame.bynat.with_cache(tmp_path).convert_to_usd(
        "salary", country_col="country", year_col="year", provider=rates
    )

    assert result["salary_usd"].tolist()[:5] == [10.0, 10.0, 2.0, 20.0, 100.0]
    assert pd.isna(result["salary_usd"].iloc[5])
    assert sorted(rates.calls) == [("EGP", 2019, 2021), ("EUR", 2019, 2020)]

    rates.calls.clear()
    frame.bynat.with_cache(tmp_path).convert_to_usd("salary", country_col="country", year_col="year", provider=rates)
    assert rates.calls == []


def test_convert_to_usd_does_not_cache_provider_failures(monkeypatch, tmp_path):
    _install_currency_lookup(monkeypatch)
    frame = pd.DataFrame({"country": ["Egypt"], "year": [2020], "salary": [170.0]})
    accessor = frame.bynat.with_cache(tmp_path)

    offline = FakeRates({}, fail={"EGP"})
    first = accessor.convert_to_usd("salary", country_col="country", year_col="year", provider=offline)

    online = FakeRates({("EGP", 2020): 17.0})
    second = accessor.convert_to_usd("salary", country_col="country", year_col="year", provider=online)

    assert first["salary_usd"].isna().all()
    assert second["salary_usd"].tolist() == [10.0]
    assert online.calls == [("EGP", 2020, 2020)]


def test_yfinance_rate_source_computes_yearly_means_from_one_download(monkeypatch):
    downloads = []

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, start, end):
            downloads.append((self.symbol, start, end))
            index = pd.to_datetime(["2019-03-01", "2019-06-01", "2020-01-02", "2021-05-05"])
            return pd.DataFrame({"Close": [10.0, 20.0, 30.0, float("nan")]}, index=index)

    _install(monkeypatch, "yfinance", Ticker=Ticker)

    rates = YFinanceRateSource().yearly_rates("EGP", 2019, 2021)

    assert rates == {2019: 15.0, 2020: 30.0}
    assert downloads == [("EGP=X", "2019-01-01", "2021-12-31")]


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------


def test_cached_map_checkpoints_skips_and_retries_failures(monkeypatch, tmp_path):
    manager = CacheManager(tmp_path)
    saves = []
    original_save = manager.save_function_cache
    monkeypatch.setattr(manager, "save_function_cache", lambda name: saves.append(name) or original_save(name))
    started = []

    def fetch(value):
        if value == "boom":
            raise RuntimeError("transient")
        if value == "skip":
            return SKIP
        return value.upper()

    series = pd.Series(["a", "b", "boom", "skip", "c", "a", None])
    cache = _cached_map(
        series, fetch, "bucket", 2, cache_manager=manager, func_name="demo", on_fetch_start=started.append
    )

    assert cache == {"a": "A", "b": "B", "c": "C"}
    assert started == [5]
    assert saves == ["demo", "demo"]

    reloaded = CacheManager(tmp_path).get_column_cache("demo", "bucket")
    assert reloaded == {"a": "A", "b": "B", "c": "C"}

    started.clear()
    _cached_map(["a", "c"], fetch, "bucket", 2, cache_manager=manager, func_name="demo", on_fetch_start=started.append)
    assert started == []


def test_cached_map_multi_bucket_requires_every_bucket(tmp_path):
    manager = CacheManager(tmp_path)
    manager.get_column_cache("demo", "left")["x"] = 1
    fetched = []

    def fetch_many(chunk):
        fetched.extend(chunk)
        return [{"left": value * 2, "right": value * 3} for value in chunk]

    caches = _cached_map(
        ["x", "y"], None, ("left", "right"), 10, cache_manager=manager, func_name="demo", fetch_many=fetch_many
    )

    assert fetched == ["x", "y"]
    assert caches["left"] == {"x": "xx", "y": "yy"}
    assert caches["right"] == {"x": "xxx", "y": "yyy"}


def test_cached_map_batch_failure_leaves_the_chunk_uncached(tmp_path):
    manager = CacheManager(tmp_path)

    def fetch_many(chunk):
        if "bad" in chunk:
            raise RuntimeError("batch failed")
        return [value.upper() for value in chunk]

    cache = _cached_map(
        ["ok", "bad", "fine"], None, "bucket", 2, cache_manager=manager, func_name="demo", fetch_many=fetch_many
    )

    assert cache == {"fine": "FINE"}


def test_cached_map_rejects_batches_with_the_wrong_length(tmp_path):
    manager = CacheManager(tmp_path)

    cache = _cached_map(
        ["a", "b"], None, "bucket", 5, cache_manager=manager, func_name="demo", fetch_many=lambda chunk: ["A"]
    )

    assert cache == {}
