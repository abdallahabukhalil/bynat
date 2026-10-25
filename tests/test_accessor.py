"""The `df.bynat` accessor, the loader helpers, and the legacy-class deprecations."""

import inspect
import json
import warnings

import pandas as pd
import pytest

import bynat
from bynat.caching import CacheManager
from bynat.cleaning import BaseData, BynatAccessor, CsvFile, JsonFile


class _Translator:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def translate(self, text, target_lang):
        self.calls.append((text, target_lang))
        return self.mapping[text]


def test_importing_bynat_registers_the_accessor():
    frame = pd.DataFrame({"a": [1]})

    assert isinstance(frame.bynat, BynatAccessor)
    assert bynat.BynatAccessor is BynatAccessor


def test_accessor_methods_return_new_plain_frames_and_leave_the_source_alone():
    frame = pd.DataFrame(
        {
            "user.name": ["Ada", "Grace"],
            "description": ["Blue shoes", "red shirt"],
            "group": ["x", "x"],
            "score": [1.0, None],
        }
    )
    original = frame.copy()

    renamed = frame.bynat.rename_all_DOTS()
    keywords = frame.bynat.extract_keywords("description", colors=["blue", "red"])
    filled = frame.bynat.fill_missing("score", hierarchy_cols="group")
    unchanged = frame.bynat.drop_by_substring("missing")

    assert type(renamed) is pd.DataFrame
    assert list(renamed.columns) == ["user_name", "description", "group", "score"]
    assert keywords["colors"].tolist() == ["Blue", "Red"]
    assert filled["score"].tolist() == [1.0, 1.0]
    assert unchanged is not frame
    assert unchanged.equals(frame)
    pd.testing.assert_frame_equal(frame, original)


def test_accessor_matches_basedata_results(tmp_path):
    data = {"g": ["a"] * 6, "v": [1.0, 2.0, 3.0, 4.0, 5.0, 100.0]}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        legacy = BaseData(data, cache_dir=tmp_path)

    plain = pd.DataFrame(data)

    pd.testing.assert_frame_equal(
        plain.bynat.drop_outliers("v", hierarchy_cols=["g"]),
        pd.DataFrame(legacy.drop_outliers("v", hierarchy_cols=["g"])),
    )
    result, stats = plain.bynat.handle_outliers("v", hierarchy_cols=["g"], get_hierarchical_stats=True)
    assert type(stats) is pd.DataFrame
    assert result["v"].isna().tolist() == [False] * 5 + [True]


def test_accessor_methods_expose_signatures_without_the_frame_or_cache_manager():
    parameters = inspect.signature(BynatAccessor.translate_categorical_column).parameters

    assert list(parameters)[:2] == ["self", "target_col"]
    assert "cache_manager" not in parameters
    assert "provider" in parameters
    assert "inplace" not in inspect.signature(BynatAccessor.drop_by_substring).parameters


def test_accessor_uses_one_shared_cache_manager_per_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("BYNAT_CACHE_DIR", str(tmp_path / "default"))
    frame = pd.DataFrame({"status": ["a"]})

    first = frame.bynat.cache_manager
    second = frame.copy().bynat.cache_manager
    explicit = frame.bynat.with_cache(tmp_path / "explicit").cache_manager

    assert first is second
    assert explicit is not first
    assert explicit is frame.bynat.with_cache(tmp_path / "explicit").cache_manager
    assert explicit.cache_dir_path == str(tmp_path / "explicit")

    manager = CacheManager(tmp_path / "manual")
    assert frame.bynat.with_cache(manager).cache_manager is manager


def test_accessor_cached_methods_use_the_selected_cache(tmp_path):
    translator = _Translator({"مكتمل": "Completed"})
    frame = pd.DataFrame({"status": ["مكتمل", "مكتمل"]})

    result = frame.bynat.with_cache(tmp_path).translate_categorical_column("status", provider=translator)
    again = frame.bynat.with_cache(tmp_path).translate_categorical_column("status", provider=translator)

    assert result["status"].tolist() == ["Completed", "Completed"]
    assert again["status"].tolist() == ["Completed", "Completed"]
    assert translator.calls == [("مكتمل", "en")]

    frame.bynat.with_cache(tmp_path).clear_cache_for_column("translate_categorical_column", "status")
    frame.bynat.with_cache(tmp_path).translate_categorical_column("status", provider=translator)
    assert len(translator.calls) == 2


def test_accessor_save_to_json(tmp_path):
    output = tmp_path / "out.json"

    pd.DataFrame({"name": ["Ada"]}).bynat.save_to_json(output)

    assert json.loads(output.read_text(encoding="utf-8")) == [{"name": "Ada"}]


def test_read_json_and_read_csv_return_plain_frames(tmp_path):
    json_path = tmp_path / "records.json"
    json_path.write_text(json.dumps({"items": [{"name": "Ada", "info": {"age": 37}}]}), encoding="utf-8")
    csv_path = tmp_path / "records.csv"
    csv_path.write_text("name;age\nGrace;28\n", encoding="utf-8")

    json_frame = bynat.read_json(json_path, key="items")
    csv_frame = bynat.read_csv(csv_path, sep=";")

    assert type(json_frame) is pd.DataFrame
    assert list(json_frame.columns) == ["name", "info.age"]
    assert list(json_frame.bynat.rename_all_DOTS().columns) == ["name", "info_age"]
    assert type(csv_frame) is pd.DataFrame
    assert csv_frame.to_dict("list") == {"name": ["Grace"], "age": [28]}


def test_read_helpers_raise_like_the_legacy_loaders(tmp_path):
    json_path = tmp_path / "list.json"
    json_path.write_text("[1, 2]", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="JSON file not found"):
        bynat.read_json(tmp_path / "missing.json")
    with pytest.raises(FileNotFoundError, match="CSV file not found"):
        bynat.read_csv(tmp_path / "missing.csv")
    with pytest.raises(TypeError, match="dictionary payload"):
        bynat.read_json(json_path, key="items")


@pytest.mark.parametrize(
    "factory",
    [
        lambda path: BaseData({"a": [1]}, cache_dir=path),
        lambda path: JsonFile({"a": [1]}, cache_dir=path),
        lambda path: CsvFile({"a": [1]}, cache_dir=path),
    ],
)
def test_supported_classes_construct_without_warning(tmp_path, factory):
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        frame = factory(tmp_path)

    assert isinstance(frame, BaseData)

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        frame.copy()
        frame[frame["a"] > 0].rename(columns={"a": "b"})
        pd.concat([frame, frame]).merge(frame, on="a")
        frame.assign(g="x").get_hierarchical_stats("a", hierarchy_cols="g")


def test_pandas_operations_share_the_legacy_cache_manager(monkeypatch, tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        frame = BaseData({"g": ["a", "b"], "v": [1, 2]}, cache_dir=tmp_path)

    manager = frame.cache_manager
    built = []
    original_init = CacheManager.__init__

    def counting_init(self, *args, **kwargs):
        built.append(args)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(CacheManager, "__init__", counting_init)

    derived = [
        frame.copy(),
        frame[frame["v"] > 1],
        frame.rename_all_DOTS(),
        frame.sort_values("v"),
        frame.groupby("g").sum(),
    ]

    assert built == []
    assert all(item.cache_manager is manager for item in derived)
    assert isinstance(derived[1], BaseData)
