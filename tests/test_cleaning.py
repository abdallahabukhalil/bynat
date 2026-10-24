import json
import sys
import types

import pandas as pd
import pytest

from bynat.caching import CacheManager
from bynat.cleaning import BaseData, CsvFile, JsonFile


def test_public_cleaning_imports_use_the_bundled_cache_manager(tmp_path):
    frame = BaseData({"value": [1]}, cache_dir=tmp_path)

    assert isinstance(frame.cache_manager, CacheManager)
    assert BaseData.__module__ == "bynat.cleaning.base"
    legacy = __import__("bynat.cleaning.Cleaning", fromlist=["__all__"])
    assert legacy.BaseData is BaseData
    assert {"BaseData", "JsonFile", "CsvFile"}.issubset(set(legacy.__all__))


def test_basedata_core_column_transformations_preserve_subclass_and_cache(tmp_path):
    frame = BaseData(
        {
            "user.name": ["Ada", "Grace"],
            "user_id": [1, 2],
            "value": [10, 20],
        },
        cache_dir=tmp_path,
    )

    renamed = frame.rename_all_DOTS()
    assert isinstance(renamed, BaseData)
    assert list(renamed.columns) == ["user_name", "user_id", "value"]
    assert renamed.cache_manager is not None
    assert list(frame.drop_by_substring("user").columns) == ["value"]

    frame.move_column("value", "user_id", position="before", inplace=True)
    assert list(frame.columns) == ["user.name", "value", "user_id"]


def test_basedata_text_list_reshape_and_imputation_helpers(tmp_path):
    frame = BaseData(
        {
            "id": [1, 2],
            "description": ["Blue shoes", "red shirt"],
            "items": [["A", "B"], ["C", "D"]],
            "group": ["x", "x"],
            "score": [1, None],
            "left": ["a", "b"],
            "right": ["c", "d"],
        },
        cache_dir=tmp_path,
    )

    keywords = frame.extract_keywords("description", colors=["blue", "red"])
    assert keywords["colors"].tolist() == ["Blue", "Red"]

    split = frame.split_list_objects("items", first=0, pair=[0, 1])
    assert split["first"].tolist() == ["A", "C"]
    assert split["pair"].tolist() == ["A, B", "C, D"]

    reshaped = frame.melt_transform_pivot(
        id_vars="id", value_vars=["left", "right"], transform_func=lambda values: values.str.upper()
    )
    assert reshaped[["left", "right"]].to_dict("list") == {
        "left": ["A", "B"],
        "right": ["C", "D"],
    }

    imputed = frame.fill_missing("score", hierarchy_cols="group")
    assert imputed["score"].tolist() == [1.0, 1.0]


def test_save_to_json_writes_local_output(tmp_path):
    output = tmp_path / "nested" / "records.json"
    BaseData({"name": ["Ada"]}, cache_dir=tmp_path).save_to_json(output)

    assert json.loads(output.read_text(encoding="utf-8")) == [{"name": "Ada"}]


def test_basedata_boundary_errors_and_inplace_return(tmp_path):
    frame = BaseData({"id": [1], "first": [2]}, cache_dir=tmp_path)

    with pytest.raises(ValueError, match="position"):
        frame.move_column("id", "first", position="middle")
    with pytest.raises(ValueError, match="positive integer"):
        frame.translate_categorical_column("first", save_every=0)

    assert frame.drop_by_substring("missing", inplace=True) is None
    assert list(frame.columns) == ["id", "first"]


def test_json_and_csv_constructors_read_local_files(tmp_path):
    json_path = tmp_path / "records.json"
    json_path.write_text(json.dumps({"items": [{"name": "Ada", "age": 37}]}), encoding="utf-8")
    csv_path = tmp_path / "records.csv"
    csv_path.write_text("name;age\nGrace;28\n", encoding="utf-8")

    json_frame = JsonFile(json_file_path=json_path, key="items", cache_dir=tmp_path / "json-cache")
    csv_frame = CsvFile(csv_file_path=csv_path, read_kwargs={"sep": ";"}, cache_dir=tmp_path / "csv-cache")

    assert list(json_frame.columns) == ["name", "age"]
    assert json_frame.iloc[0]["name"] == "Ada"
    assert list(csv_frame.columns) == ["name", "age"]
    assert csv_frame.iloc[0]["name"] == "Grace"
    assert isinstance(json_frame.view(), pd.DataFrame)
    assert type(json_frame.view()) is pd.DataFrame


def test_file_constructors_raise_for_missing_paths(tmp_path):
    with pytest.raises(FileNotFoundError, match="JSON file not found"):
        JsonFile(json_file_path=tmp_path / "missing.json")
    with pytest.raises(FileNotFoundError, match="CSV file not found"):
        CsvFile(csv_file_path=tmp_path / "missing.csv")


def test_impute_by_language_missing_reference_returns_before_optional_import(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "langdetect", None)
    frame = BaseData({"category": ["pending"]}, cache_dir=tmp_path)

    result = frame.impute_by_language("missing")
    assert result is not frame
    assert result.equals(frame)
    assert frame.impute_by_language("missing", inplace=True) is None


def test_optional_integration_reports_missing_dependency_at_method_use(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "deep_translator", None)
    frame = BaseData({"category": ["pending"]}, cache_dir=tmp_path)

    with pytest.raises(ImportError, match="deep-translator"):
        frame.translate_categorical_column("category")


def test_optional_categorization_reports_missing_dependency_at_method_use(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "ahocorasick", None)
    frame = BaseData({"description": ["python developer"]}, cache_dir=tmp_path)

    with pytest.raises(ImportError, match="pyahocorasick"):
        frame.categorize_by_reference(
            "description",
            target_col="category",
            reference={"Software": ["python developer"]},
        )


def test_translate_conditional_safe_partition_does_not_import_deep_translator(monkeypatch, tmp_path):
    detect_calls = []

    class DetectorFactory:
        seed = None

    langdetect = types.ModuleType("langdetect")
    langdetect.DetectorFactory = DetectorFactory
    langdetect.detect = lambda text: detect_calls.append(text) or "en"

    class FailingGoogleTranslator:
        def __init__(self, *args, **kwargs):
            pytest.fail("deep-translator should not be required for a safe partition")

    deep_translator = types.ModuleType("deep_translator")
    deep_translator.GoogleTranslator = FailingGoogleTranslator
    monkeypatch.setitem(sys.modules, "langdetect", langdetect)
    monkeypatch.setitem(sys.modules, "deep_translator", deep_translator)

    frame = BaseData(
        {"partition": ["safe"], "description": ["already English"], "label": ["Existing Label"]},
        cache_dir=tmp_path,
    )

    result = frame.translate_conditional_column(
        partition_col="partition",
        detect_col="description",
        target_col="label",
        sample_size=1,
        number_of_samples=2,
        threshold=1.0,
        save_every=1,
    )

    assert result.equals(frame)
    assert DetectorFactory.seed == 0
    assert len(detect_calls) == 2


def test_translate_conditional_missing_langdetect_raises_documented_import_error(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "langdetect", None)
    monkeypatch.setitem(sys.modules, "deep_translator", None)
    frame = BaseData(
        {"partition": ["group"], "description": ["text"], "label": ["value"]},
        cache_dir=tmp_path,
    )

    with pytest.raises(ImportError, match="langdetect"):
        frame.translate_conditional_column(
            partition_col="partition",
            detect_col="description",
            target_col="label",
            sample_size=1,
            number_of_samples=1,
        )



def test_save_to_json_append_keeps_existing_rows_with_list_columns(tmp_path):
    file_path = tmp_path / "out.json"

    BaseData({"a": [1, 2], "tags": [["x"], ["y"]]}, cache_dir=tmp_path).save_to_json(file_path)
    BaseData({"a": [2, 3], "tags": [["y"], ["z"]]}, cache_dir=tmp_path).save_to_json(
        file_path, append=True
    )

    saved = json.loads(file_path.read_text(encoding="utf-8"))

    assert saved == [
        {"a": 1, "tags": ["x"]},
        {"a": 2, "tags": ["y"]},
        {"a": 3, "tags": ["z"]},
    ]


@pytest.mark.parametrize(
    "index",
    [
        pd.Index(range(10, 16), name="row_id"),
        pd.MultiIndex.from_tuples([("k", i) for i in range(6)], names=["k", "i"]),
        pd.Index([0, 0, 1, 1, 2, 2]),
    ],
)
def test_hierarchy_methods_support_named_multi_and_duplicate_indexes(tmp_path, index):
    frame = BaseData(
        {"g": ["a"] * 6, "v": [1.0, 2.0, 3.0, 4.0, 5.0, 100.0]},
        index=index,
        cache_dir=tmp_path,
    )

    handled = frame.handle_outliers("v", hierarchy_cols=["g"])
    dropped = frame.drop_outliers("v", hierarchy_cols=["g"])

    assert handled["v"].isna().tolist() == [False] * 5 + [True]
    assert handled.index.equals(index)
    assert dropped["v"].tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert dropped.index.equals(index[:5])

    in_place = frame.copy()
    assert in_place.drop_outliers("v", hierarchy_cols=["g"], inplace=True) is None
    assert in_place.equals(dropped)

    modes = BaseData(
        {"g": ["a"] * 6, "c": ["x", "x", "y", None, "x", None]},
        index=index,
        cache_dir=tmp_path,
    ).fill_missing("c", strategy="mode", hierarchy_cols=["g"])

    assert modes["c"].tolist() == ["x", "x", "y", "x", "x", "x"]
    assert modes.index.equals(index)



def test_save_to_json_append_deduplicates_plain_columns_without_json_keys(tmp_path, monkeypatch):
    import bynat.cleaning.io as cleaning

    file_path = tmp_path / "out.json"
    BaseData({"a": [1, 2], "b": ["x", "y"]}, cache_dir=tmp_path).save_to_json(file_path)

    monkeypatch.setattr(cleaning, "_dedupe_key", lambda value: pytest.fail("fast path expected"))
    BaseData({"a": [2, 3], "b": ["y", "z"]}, cache_dir=tmp_path).save_to_json(file_path, append=True)

    assert json.loads(file_path.read_text(encoding="utf-8")) == [
        {"a": 1, "b": "x"},
        {"a": 2, "b": "y"},
        {"a": 3, "b": "z"},
    ]


def test_fill_missing_rejects_unknown_strategy(tmp_path):
    frame = BaseData({"g": ["a", "a"], "v": [1.0, None]}, cache_dir=tmp_path)

    with pytest.raises(ValueError, match="Invalid strategy 'sum'"):
        frame.fill_missing("v", strategy="sum", hierarchy_cols="g")


def test_save_to_json_append_refuses_to_overwrite_unreadable_file(tmp_path):
    file_path = tmp_path / "out.json"
    file_path.write_text('{"not": "records"', encoding="utf-8")

    with pytest.raises(ValueError, match="Cannot append"):
        BaseData({"a": [1]}, cache_dir=tmp_path).save_to_json(file_path, append=True)

    assert file_path.read_text(encoding="utf-8") == '{"not": "records"'


def test_save_to_json_requires_file_path(tmp_path):
    with pytest.raises(ValueError, match="file_path"):
        BaseData({"a": [1]}, cache_dir=tmp_path).save_to_json("  ")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"table_name": " "}, "table_name"),
        ({"if_exists": "merge"}, "if_exists"),
        ({"database_type": ""}, "database_type"),
        ({"database_name": ""}, "SQLite"),
        ({"database_type": "postgresql", "database_host": ""}, "are required"),
        ({"database_type": "mssql+pyodbc", "database_user": "u", "database_password": "p",
          "database_host": "h", "database_port": "1"}, "database_driver"),
    ],
)
def test_save_to_sql_rejects_invalid_arguments_before_connecting(monkeypatch, tmp_path, kwargs, message):
    monkeypatch.setitem(sys.modules, "sqlalchemy", None)
    arguments = {
        "table_name": "items",
        "database_type": "sqlite",
        "database_user": "",
        "database_password": "",
        "database_host": "",
        "database_port": "",
        "database_name": str(tmp_path / "out.db"),
        "if_exists": "append",
    }
    arguments.update(kwargs)

    with pytest.raises(ValueError, match=message):
        BaseData({"a": [1]}, cache_dir=tmp_path).save_to_sql(**arguments)
