"""Offline tests for methods that call external services, using fake modules."""

import json
import sys
import types

import pandas as pd
import pytest

from bynat.cleaning import BaseData


def _install(monkeypatch, name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def test_translate_categorical_column_translates_each_unique_value_once(monkeypatch, tmp_path):
    calls = []

    class GoogleTranslator:
        def __init__(self, source, target):
            assert (source, target) == ("auto", "en")

        def translate(self, text):
            calls.append(text)
            return {"مكتمل": "Completed", "قيد الانتظار": "Pending"}[text]

    _install(monkeypatch, "deep_translator", GoogleTranslator=GoogleTranslator)
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    frame = BaseData({"status": ["مكتمل", "قيد الانتظار", "مكتمل", None]}, cache_dir=tmp_path)
    result = frame.translate_categorical_column("status")

    assert result["status"].tolist()[:3] == ["Completed", "Pending", "Completed"]
    assert pd.isna(result["status"].iloc[3])
    assert sorted(calls) == sorted(["مكتمل", "قيد الانتظار"])

    # A second frame with the same cache directory needs no translator calls.
    calls.clear()
    again = BaseData({"status": ["مكتمل"]}, cache_dir=tmp_path).translate_categorical_column("status")
    assert again["status"].tolist() == ["Completed"]
    assert calls == []


class _Location:
    def __init__(self, latitude, longitude):
        self.latitude = latitude
        self.longitude = longitude


def _install_geopy(monkeypatch, known, calls, agents):
    class Nominatim:
        def __init__(self, user_agent):
            agents.append(user_agent)

        def geocode(self, query):
            calls.append(query)
            return known.get(query)

    class RateLimiter:
        def __init__(self, func, min_delay_seconds):
            self.func = func

        def __call__(self, query):
            return self.func(query)

    _install(monkeypatch, "geopy")
    _install(monkeypatch, "geopy.geocoders", Nominatim=Nominatim)
    _install(monkeypatch, "geopy.extra")
    _install(monkeypatch, "geopy.extra.rate_limiter", RateLimiter=RateLimiter)


def test_fill_missing_coordinates_shortens_queries_and_caches_misses(monkeypatch, tmp_path):
    calls, agents = [], []
    _install_geopy(monkeypatch, {"Springfield, USA": _Location(39.8, -89.6)}, calls, agents)

    frame = BaseData(
        {
            "street": ["1 Main St", "Nowhere Rd", None],
            "city": ["Springfield", "Atlantis", "Paris"],
            "country": ["USA", None, "France"],
            "lat": [None, None, 48.8],
            "lng": [None, None, 2.3],
        },
        cache_dir=tmp_path,
    )

    result = frame.fill_missing_coordinates(
        "street", "city", "country", lat_col="lat", long_col="lng", user_agent="bynat-tests"
    )

    assert agents == ["bynat-tests"]
    # The leftmost part is dropped one step at a time; the known row is never queried.
    assert calls == [
        "1 Main St, Springfield, USA",
        "Springfield, USA",
        "Nowhere Rd, Atlantis",
        "Atlantis",
    ]
    assert result[["lat", "lng"]].iloc[0].tolist() == [39.8, -89.6]
    assert result[["lat", "lng"]].iloc[1].isna().all()
    assert result[["lat", "lng"]].iloc[2].tolist() == [48.8, 2.3]

    cache = json.loads((tmp_path / "fill_missing_coordinates_cache.json").read_text(encoding="utf-8"))
    assert cache["geocode_queries"]["Nowhere Rd, Atlantis"] is None

    calls.clear()
    frame.fill_missing_coordinates("street", "city", "country", lat_col="lat", long_col="lng")
    assert calls == []

    frame.fill_missing_coordinates(
        "street", "city", "country", lat_col="lat", long_col="lng", retry_not_found=True
    )
    assert calls == ["Nowhere Rd, Atlantis", "Atlantis"]


def _install_currency(monkeypatch, history_by_ticker_year, fetches):
    class Country:
        def __init__(self, alpha_2):
            self.alpha_2 = alpha_2

    def search_fuzzy(name):
        if name == "Atlantis":
            raise LookupError(name)
        return [Country({"Egypt": "EG", "United States": "US"}[name])]

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, start, end):
            year = int(start[:4])
            fetches.append((self.symbol, year))
            closes = history_by_ticker_year.get((self.symbol, year), [])
            return pd.DataFrame({"Close": closes})

    _install(monkeypatch, "pycountry", countries=types.SimpleNamespace(search_fuzzy=search_fuzzy))
    _install(monkeypatch, "babel")
    _install(
        monkeypatch,
        "babel.numbers",
        get_territory_currencies=lambda alpha_2: {"EG": ["EGP"], "US": ["USD"]}[alpha_2],
    )
    _install(monkeypatch, "yfinance", Ticker=Ticker)


def test_convert_to_usd_looks_back_and_caches_missing_years(monkeypatch, tmp_path):
    fetches = []
    _install_currency(monkeypatch, {("EGP=X", 2019): [16.0, 18.0]}, fetches)

    frame = BaseData(
        {
            "country": ["Egypt", "United States", "Atlantis"],
            "year": [2020, 2020, 2020],
            "salary": [170.0, 100.0, 50.0],
        },
        index=pd.Index(["a", "b", "c"], name="row"),
        cache_dir=tmp_path,
    )
    frame.loc["d"] = ["Egypt", 2019, 34.0]

    result = frame.convert_to_usd("salary", country_col="country", year_col="year", round_decimals=2)

    assert result["salary_usd"].tolist()[:2] == [10.0, 100.0]
    assert pd.isna(result.loc["c", "salary_usd"])
    assert result.loc["d", "salary_usd"] == 2.0
    assert fetches == [("EGP=X", 2020), ("EGP=X", 2019)]

    cache = json.loads((tmp_path / "convert_to_usd_cache.json").read_text(encoding="utf-8"))
    assert cache["currency_codes"]["Atlantis"] is None
    assert cache["exchange_rates"]["EGP_2020"] is None
    assert cache["exchange_rates"]["EGP_2019"] == 17.0

    fetches.clear()
    frame.convert_to_usd("salary", country_col="country", year_col="year")
    assert fetches == []


def test_extract_entities_nlp_skips_unused_pipes_and_reuses_cache(monkeypatch, tmp_path):
    pipe_calls = []

    class Doc(list):
        pass

    class Nlp:
        vocab = types.SimpleNamespace(strings={})

        def pipe(self, texts, **kwargs):
            texts = list(texts)
            pipe_calls.append((texts, kwargs))
            return [Doc() for _ in texts]

    class Matcher:
        def __init__(self, vocab):
            pass

        def add(self, name, patterns):
            pass

        def __call__(self, doc):
            return []

    _install(monkeypatch, "spacy", load=lambda model: Nlp())
    _install(monkeypatch, "spacy.matcher", Matcher=Matcher)

    frame = BaseData({"text": ["python and sql", "python and sql", "excel"]}, cache_dir=tmp_path)
    lexicon = {"skills": ["python", "sql"]}

    result = frame.extract_entities_nlp("text", lexicon_source=lexicon, skills="skills")

    assert result["skills"].tolist() == [[], [], []]
    text_pipe = pipe_calls[-1]
    assert text_pipe[0] == ["python and sql", "excel"]
    assert text_pipe[1]["disable"] == ["parser", "ner"]

    pipe_calls.clear()
    frame.extract_entities_nlp("text", lexicon_source=lexicon, skills="skills")
    assert pipe_calls == []


def test_categorize_by_reference_with_real_pyahocorasick(tmp_path):
    pytest.importorskip("ahocorasick")
    frame = BaseData(
        {"title": ["Senior Python Developer", "Data Analyst", "Chef", None]},
        cache_dir=tmp_path,
    )

    result = frame.categorize_by_reference(
        "title",
        target_col="family",
        reference={"Engineering": ["python developer", "developer"], "Data": ["data analyst"]},
    )

    assert result["family"].tolist() == ["Engineering", "Data", "Other", "Other"]


def test_save_to_sql_with_real_sqlalchemy_and_sqlite(tmp_path):
    sqlalchemy = pytest.importorskip("sqlalchemy")
    database = tmp_path / "out.db"
    frame = BaseData({"a": [1, 2], "b": ["x", "y"]}, cache_dir=tmp_path)

    frame.save_to_sql(
        "items",
        database_type="sqlite",
        database_user="",
        database_password="",
        database_host="",
        database_port="",
        database_name=str(database),
    )

    engine = sqlalchemy.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        rows = connection.execute(sqlalchemy.text("SELECT a, b FROM items ORDER BY a")).all()
    engine.dispose()

    assert [tuple(row) for row in rows] == [(1, "x"), (2, "y")]


def test_save_to_sql_propagates_database_errors(tmp_path):
    pytest.importorskip("sqlalchemy")
    frame = BaseData({"a": [1]}, cache_dir=tmp_path)
    arguments = dict(
        database_type="sqlite",
        database_user="",
        database_password="",
        database_host="",
        database_port="",
        database_name=str(tmp_path / "out.db"),
    )
    frame.save_to_sql("items", **arguments)

    with pytest.raises(ValueError, match="already exists"):
        frame.save_to_sql("items", if_exists="fail", **arguments)


def test_extract_entities_nlp_raises_for_bad_configuration(monkeypatch, tmp_path):
    def load(model):
        raise OSError(f"Can't find model '{model}'")

    _install(monkeypatch, "spacy", load=load)
    _install(monkeypatch, "spacy.matcher", Matcher=object)
    frame = BaseData({"text": ["python"]}, cache_dir=tmp_path)

    with pytest.raises(ValueError, match="target column"):
        frame.extract_entities_nlp("text", lexicon_source={"skills": ["python"]})
    with pytest.raises(ValueError, match="No valid terms"):
        frame.extract_entities_nlp("text", lexicon_source={"other": ["x"]}, skills="skills")
    with pytest.raises(OSError, match="missing_model"):
        frame.extract_entities_nlp(
            "text", lexicon_source={"skills": ["python"]}, spacy_model="missing_model", skills="skills"
        )
