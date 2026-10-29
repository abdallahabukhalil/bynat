"""Tests for the data quality profiling API and report structures."""

import json
from collections.abc import Mapping
from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest

import bynat
from bynat.cleaning import (
    BaseData,
    ColumnProfile,
    DatasetProfile,
    ProfileReport,
    profile,
)

EXPECTED_TOP_LEVEL_KEYS: set[str] = {"dataset", "columns"}

EXPECTED_DATASET_FIELDS: set[str] = {
    "row_count",
    "column_count",
    "total_cells",
    "missing_count",
    "missing_proportion",
    "duplicate_rows",
    "duplicate_proportion",
    "empty_column_count",
    "memory_bytes",
    "dtype_counts",
}

EXPECTED_COLUMN_FIELDS: set[str] = {
    "name",
    "dtype",
    "inferred_type",
    "kind",
    "count",
    "missing_count",
    "missing_proportion",
    "distinct_count",
    "distinct_proportion",
    "is_unique",
    "stats",
}

EXPECTED_STATS_KEYS_BY_KIND: dict[str, set[str]] = {
    "numeric": {
        "min",
        "max",
        "mean",
        "std",
        "median",
        "q25",
        "q75",
        "iqr",
        "zeros_count",
        "zeros_proportion",
        "outliers_count",
    },
    "categorical": {
        "top",
        "top_frequency",
        "top_proportion",
        "min_length",
        "max_length",
        "mean_length",
    },
    "boolean": {
        "true_count",
        "false_count",
        "true_proportion",
    },
    "datetime": {
        "min",
        "max",
        "range_days",
    },
    "other": {
        "top",
        "top_frequency",
        "top_proportion",
    },
}


def test_public_entry_points():
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})

    # Standalone from bynat.cleaning
    rep1 = profile(df)
    assert isinstance(rep1, ProfileReport)

    # Standalone from top-level bynat
    rep2 = bynat.profile(df)
    assert isinstance(rep2, ProfileReport)

    # Accessor entry point
    rep3 = df.bynat.profile()
    assert isinstance(rep3, ProfileReport)

    # BaseData entry point
    base = BaseData(df)
    rep4 = base.profile()
    assert isinstance(rep4, ProfileReport)

    # All produce equivalent metrics
    assert rep1.dataset.row_count == rep2.dataset.row_count == rep3.dataset.row_count == rep4.dataset.row_count == 3
    assert rep1.to_dict() == rep2.to_dict() == rep3.to_dict() == rep4.to_dict()


def test_profiling_does_not_modify_input():
    df = pd.DataFrame(
        {
            "num": [10.0, np.nan, 30.0],
            "text": ["hello", None, "world"],
            "flag": [True, False, None],
        }
    )
    original = df.copy(deep=True)

    # Run profile
    report = df.bynat.profile()

    assert report is not None
    # Verify input DataFrame is completely unchanged
    pd.testing.assert_frame_equal(df, original)
    assert list(df.columns) == list(original.columns)
    assert df.index.equals(original.index)


def test_report_structure_and_access_patterns():
    df = pd.DataFrame({"age": [20, 30, 40], "name": ["Alice", "Bob", "Charlie"]})
    report = df.bynat.profile()

    # Mapping contract
    assert isinstance(report, Mapping)
    assert "dataset" in report
    assert "columns" in report
    assert len(report) == 2
    assert list(report.keys()) == ["dataset", "columns"]

    # Attribute access
    assert isinstance(report.dataset, DatasetProfile)
    assert report.dataset.row_count == 3
    assert report.dataset.column_count == 2
    assert isinstance(report.columns["age"], ColumnProfile)
    assert report.columns["age"].count == 3

    # Dictionary access
    assert report["dataset"]["row_count"] == 3
    assert report["columns"]["age"]["missing_count"] == 0

    # Conversions
    d = report.to_dict()
    assert isinstance(d, dict)
    assert "dataset" in d and "columns" in d
    assert isinstance(d["dataset"], dict)
    assert isinstance(d["columns"]["age"], dict)

    summary_df = report.to_dataframe()
    assert isinstance(summary_df, pd.DataFrame)
    assert len(summary_df) == 2
    assert "column" in summary_df.columns
    assert "missing_proportion" in summary_df.columns
    assert set(summary_df["column"]) == {"age", "name"}

    # Readable repr
    repr_str = repr(report)
    assert "ProfileReport(" in repr_str
    assert "rows=3" in repr_str
    assert "columns=2" in repr_str


def test_output_type_parameter():
    df = pd.DataFrame({"x": [1, 2, 3]})

    # Default None -> ProfileReport
    rep_default = profile(df)
    assert isinstance(rep_default, ProfileReport)

    # Explicit ProfileReport
    rep_obj = profile(df, output_type=ProfileReport)
    assert isinstance(rep_obj, ProfileReport)

    # dict
    rep_dict = profile(df, output_type=dict)
    assert isinstance(rep_dict, dict)
    assert "dataset" in rep_dict and "columns" in rep_dict
    assert rep_dict["dataset"]["row_count"] == 3

    # pd.DataFrame
    rep_frame = profile(df, output_type=pd.DataFrame)
    assert isinstance(rep_frame, pd.DataFrame)
    assert len(rep_frame) == 1
    assert rep_frame.iloc[0]["column"] == "x"

    # BaseData subclass
    rep_basedata = profile(df, output_type=BaseData)
    assert isinstance(rep_basedata, BaseData)
    assert len(rep_basedata) == 1

    # Invalid output_type raises TypeError
    with pytest.raises(TypeError, match="Unsupported output_type"):
        profile(df, output_type=int)


def test_column_selection_and_errors():
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6]})

    # Select specific columns as varargs
    rep = df.bynat.profile("a", "c")
    assert rep.dataset.column_count == 2
    assert list(rep.columns.keys()) == ["a", "c"]

    # Select specific columns as sequence
    rep2 = df.bynat.profile(["b"])
    assert rep2.dataset.column_count == 1
    assert list(rep2.columns.keys()) == ["b"]

    # Non-existent column raises KeyError
    with pytest.raises(KeyError, match="not found in DataFrame"):
        df.bynat.profile("non_existent")

    # Non-DataFrame raises TypeError
    with pytest.raises(TypeError, match="Expected a pandas DataFrame"):
        profile([1, 2, 3])  # type: ignore[arg-type]


def test_missing_values_and_proportions():
    df = pd.DataFrame(
        {
            "c1": [1, None, 3, None],      # 2 missing out of 4 (50%)
            "c2": ["a", "b", None, None],  # 2 missing out of 4 (50%)
            "c3": [10, 20, 30, 40],        # 0 missing out of 4 (0%)
        }
    )
    rep = df.bynat.profile()

    # Dataset missing metrics
    assert rep.dataset.row_count == 4
    assert rep.dataset.column_count == 3
    assert rep.dataset.total_cells == 12
    assert rep.dataset.missing_count == 4
    assert rep.dataset.missing_proportion == 4 / 12

    # Column missing metrics
    assert rep.columns["c1"].count == 2
    assert rep.columns["c1"].missing_count == 2
    assert rep.columns["c1"].missing_proportion == 0.5

    assert rep.columns["c3"].count == 4
    assert rep.columns["c3"].missing_count == 0
    assert rep.columns["c3"].missing_proportion == 0.0


def test_duplicate_rows_counting_semantics():
    # 5 rows total: row 0 and 2 are identical, row 1 and 3 are identical, row 4 is unique.
    # keep='first' semantics: 2 duplicate rows (row 2 and row 3).
    df = pd.DataFrame(
        {
            "id": [1, 2, 1, 2, 3],
            "val": ["x", "y", "x", "y", "z"],
        }
    )
    rep = df.bynat.profile()

    assert rep.dataset.row_count == 5
    assert rep.dataset.duplicate_rows == 2
    assert rep.dataset.duplicate_proportion == 2 / 5

    # Completely distinct rows -> 0 duplicates
    df_distinct = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    rep_distinct = df_distinct.bynat.profile()
    assert rep_distinct.dataset.duplicate_rows == 0
    assert rep_distinct.dataset.duplicate_proportion == 0.0


def test_duplicate_rows_with_unhashable_cells():
    # Columns containing dicts and lists should not raise TypeError during duplicate detection
    df = pd.DataFrame(
        {
            "meta": [{"env": "prod"}, {"env": "dev"}, {"env": "prod"}],
            "tags": [["a", "b"], ["c"], ["a", "b"]],
        }
    )
    rep = df.bynat.profile()

    assert rep.dataset.row_count == 3
    assert rep.dataset.duplicate_rows == 1
    assert rep.dataset.duplicate_proportion == 1 / 3
    assert rep.columns["meta"].distinct_count == 2
    assert rep.columns["tags"].distinct_count == 2


def test_numeric_statistics():
    # Values: 10, 20, 20, 30, 0, 1000 (outlier)
    # count = 6, 0s = 1 (16.7%), distinct = 5
    data = [10.0, 20.0, 20.0, 30.0, 0.0, 1000.0]
    df = pd.DataFrame({"num": data})
    rep = df.bynat.profile()
    col = rep.columns["num"]

    assert col.kind == "numeric"
    assert col.count == 6
    assert col.missing_count == 0
    assert col.distinct_count == 5
    assert not col.is_unique

    stats = col.stats
    assert stats["min"] == 0.0
    assert stats["max"] == 1000.0
    assert stats["zeros_count"] == 1
    assert pytest.approx(stats["zeros_proportion"]) == 1 / 6
    assert stats["mean"] == float(np.mean(data))
    assert stats["median"] == float(np.median(data))
    assert stats["std"] == pytest.approx(float(pd.Series(data).std()))
    assert stats["iqr"] is not None
    assert stats["outliers_count"] == 1  # 1000.0 is detected as outlier


def test_categorical_and_text_statistics():
    df = pd.DataFrame(
        {
            "fruit": ["apple", "banana", "apple", "cherry", None],
        }
    )
    rep = df.bynat.profile()
    col = rep.columns["fruit"]

    assert col.kind == "categorical"
    assert col.count == 4
    assert col.missing_count == 1
    assert col.distinct_count == 3

    stats = col.stats
    assert stats["top"] == "apple"
    assert stats["top_frequency"] == 2
    assert stats["top_proportion"] == 0.5
    assert stats["min_length"] == 5  # "apple"
    assert stats["max_length"] == 6  # "banana", "cherry"
    assert stats["mean_length"] == (5 + 6 + 5 + 6) / 4


def test_boolean_statistics():
    df = pd.DataFrame({"flag": [True, False, True, True, None]})
    rep = df.bynat.profile()
    col = rep.columns["flag"]

    assert col.kind == "boolean"
    assert col.count == 4
    assert col.missing_count == 1
    assert col.distinct_count == 2

    stats = col.stats
    assert stats["true_count"] == 3
    assert stats["false_count"] == 1
    assert stats["true_proportion"] == 0.75


def test_datetime_statistics():
    dates = pd.to_datetime(["2024-01-01 10:00:00", "2024-01-05 10:00:00", None])
    df = pd.DataFrame({"timestamp": dates})
    rep = df.bynat.profile()
    col = rep.columns["timestamp"]

    assert col.kind == "datetime"
    assert col.count == 2
    assert col.missing_count == 1
    assert col.distinct_count == 2

    stats = col.stats
    assert "2024-01-01" in stats["min"]
    assert "2024-01-05" in stats["max"]
    assert stats["range_days"] == pytest.approx(4.0)


def test_mixed_and_other_statistics():
    df = pd.DataFrame({"mixed": [1, "two", 3.0, "two"]})
    rep = df.bynat.profile()
    col = rep.columns["mixed"]

    assert col.kind == "other"
    assert col.inferred_type in ("mixed", "mixed-integer")
    assert col.count == 4
    assert col.distinct_count == 3

    stats = col.stats
    assert stats["top"] == "two"
    assert stats["top_frequency"] == 2
    assert stats["top_proportion"] == 0.5
    # String lengths are inapplicable for mixed types and represented as None
    assert "min_length" not in stats or stats["min_length"] is None


def test_nullable_dtypes():
    df = pd.DataFrame(
        {
            "int_null": pd.Series([10, 20, None], dtype="Int64"),
            "float_null": pd.Series([1.5, None, 4.5], dtype="Float64"),
            "bool_null": pd.Series([True, False, None], dtype="boolean"),
            "str_null": pd.Series(["hello", "world", None], dtype="string"),
        }
    )
    rep = df.bynat.profile()

    assert rep.columns["int_null"].count == 2
    assert rep.columns["int_null"].missing_count == 1
    assert rep.columns["int_null"].stats["mean"] == 15.0

    assert rep.columns["float_null"].count == 2
    assert rep.columns["float_null"].missing_count == 1
    assert rep.columns["float_null"].stats["mean"] == 3.0

    assert rep.columns["bool_null"].count == 2
    assert rep.columns["bool_null"].missing_count == 1
    assert rep.columns["bool_null"].stats["true_count"] == 1

    assert rep.columns["str_null"].count == 2
    assert rep.columns["str_null"].missing_count == 1
    assert rep.columns["str_null"].stats["top_frequency"] == 1


def test_empty_dataframe_edge_cases():
    # 1. 0 rows, 0 columns
    empty_df = pd.DataFrame()
    rep1 = profile(empty_df)
    assert rep1.dataset.row_count == 0
    assert rep1.dataset.column_count == 0
    assert rep1.dataset.total_cells == 0
    assert rep1.dataset.missing_count == 0
    assert rep1.dataset.missing_proportion == 0.0
    assert rep1.dataset.duplicate_rows == 0
    assert rep1.dataset.duplicate_proportion == 0.0
    assert rep1.dataset.empty_column_count == 0
    assert len(rep1.columns) == 0
    assert rep1.to_dataframe().empty

    # 2. 0 rows, with column schema (untyped columns default to object -> kind 'other')
    empty_with_cols = pd.DataFrame(columns=["num_col", "text_col"])
    rep2 = profile(empty_with_cols)
    assert rep2.dataset.row_count == 0
    assert rep2.dataset.column_count == 2
    assert rep2.dataset.empty_column_count == 2
    assert rep2.columns["num_col"].count == 0
    assert rep2.columns["num_col"].missing_count == 0
    assert rep2.columns["num_col"].missing_proportion == 0.0
    assert rep2.columns["num_col"].distinct_count == 0
    assert not rep2.columns["num_col"].is_unique
    assert rep2.columns["num_col"].kind == "other"
    assert rep2.columns["num_col"].stats == {
        "top": None,
        "top_frequency": None,
        "top_proportion": None,
    }
    assert rep2.columns["text_col"].kind == "other"
    assert rep2.columns["text_col"].stats == {
        "top": None,
        "top_frequency": None,
        "top_proportion": None,
    }

    # 3. 0 rows, with explicitly typed numeric column
    empty_typed = pd.DataFrame({"num": pd.Series([], dtype="float64")})
    rep3 = profile(empty_typed)
    assert rep3.dataset.row_count == 0
    assert rep3.columns["num"].kind == "numeric"
    assert rep3.columns["num"].stats["min"] is None
    assert rep3.columns["num"].stats["max"] is None
    assert rep3.columns["num"].stats["mean"] is None
    assert rep3.columns["num"].stats["zeros_count"] == 0
    assert rep3.columns["num"].stats["zeros_proportion"] == 0.0


def test_all_missing_column_edge_cases():
    df = pd.DataFrame(
        {
            "all_na_float": [np.nan, np.nan, np.nan],
            "all_none_obj": [None, None, None],
        }
    )
    rep = profile(df)

    assert rep.dataset.row_count == 3
    assert rep.dataset.empty_column_count == 2

    # Float col
    col_float = rep.columns["all_na_float"]
    assert col_float.count == 0
    assert col_float.missing_count == 3
    assert col_float.missing_proportion == 1.0
    assert col_float.distinct_count == 0
    assert not col_float.is_unique
    assert col_float.stats["mean"] is None
    assert col_float.stats["outliers_count"] is None
    assert col_float.stats["zeros_count"] == 0

    # Obj col
    col_obj = rep.columns["all_none_obj"]
    assert col_obj.count == 0
    assert col_obj.missing_count == 3
    assert col_obj.missing_proportion == 1.0
    assert col_obj.distinct_count == 0
    assert col_obj.stats["top"] is None
    assert col_obj.stats["top_frequency"] is None


def test_single_row_statistics():
    # Test numeric sample std when n=1 (should be None, not NaN or crash)
    df = pd.DataFrame({"single_num": [42.0]})
    rep = profile(df)
    stats = rep.columns["single_num"].stats

    assert stats["min"] == 42.0
    assert stats["max"] == 42.0
    assert stats["mean"] == 42.0
    assert stats["std"] is None  # n <= 1 sample std is undefined -> None
    assert stats["outliers_count"] is None  # n < 5 samples -> None


def test_duplicate_column_names_handling():
    # DataFrame with duplicate column names: columns=['a', 'a']
    df = pd.DataFrame([[1, 2], [3, 4]], columns=["a", "a"])
    rep = profile(df)

    assert rep.dataset.column_count == 2
    assert len(rep.column_list) == 2
    assert rep.column_list[0].name == "a"
    assert rep.column_list[1].name == "a"
    df_summary = rep.to_dataframe()
    assert len(df_summary) == 2

def test_clean_scalar_bool_before_int():
    # Object column with booleans and strings must preserve bool type for top value
    df = pd.DataFrame({"obj": [True, "x", True]})
    rep = profile(df)
    col = rep.columns["obj"]
    assert col.kind == "other"
    assert col.stats["top"] is True
    assert type(col.stats["top"]) is bool
    assert col.stats["top_frequency"] == 2

    # Direct check of _clean_scalar
    from bynat.cleaning.profile import _clean_scalar

    assert _clean_scalar(True) is True
    assert type(_clean_scalar(True)) is bool
    assert _clean_scalar(False) is False
    assert type(_clean_scalar(False)) is bool
    assert _clean_scalar(np.bool_(True)) is True
    assert type(_clean_scalar(np.bool_(True))) is bool
    assert _clean_scalar(1) == 1
    assert type(_clean_scalar(1)) is int


def test_timedelta_columns_and_clean_scalar():
    # Timedelta columns are kind 'other' and serializable in to_dict()
    td_series = pd.Series([pd.Timedelta(days=1), pd.Timedelta(days=2), pd.Timedelta(days=1)])
    df = pd.DataFrame({"td": td_series})
    rep = profile(df)
    col = rep.columns["td"]
    assert col.kind == "other"
    assert col.stats["top"] == "P1DT0H0M0S"
    assert col.stats["top_frequency"] == 2
    assert col.stats["top_proportion"] == pytest.approx(2 / 3)

    # Ensure json.dumps succeeds on to_dict()
    dumped = json.dumps(rep.to_dict())
    assert "P1DT0H0M0S" in dumped

    # Direct check of _clean_scalar with pd.Timedelta, np.timedelta64, datetime.timedelta
    import datetime

    from bynat.cleaning.profile import _clean_scalar

    assert _clean_scalar(pd.Timedelta(days=1)) == "P1DT0H0M0S"
    assert _clean_scalar(np.timedelta64(10, "s")) == "P0DT0H0M10S"
    assert _clean_scalar(datetime.timedelta(days=2)) == "P2DT0H0M0S"


def test_categorical_pd_categorical():
    # pd.Categorical column should have kind 'categorical'
    cat_series = pd.Series(pd.Categorical(["apple", "banana", "apple", None]))
    df = pd.DataFrame({"cat": cat_series})
    rep = profile(df)
    col = rep.columns["cat"]
    assert col.kind == "categorical"
    assert set(col.stats.keys()) == EXPECTED_STATS_KEYS_BY_KIND["categorical"]
    assert col.stats["top"] == "apple"
    assert col.stats["top_frequency"] == 2
    assert col.stats["top_proportion"] == pytest.approx(2 / 3)


def test_tz_aware_datetime():
    # Tz-aware datetime column should have kind 'datetime'
    tz_series = pd.Series(pd.date_range("2024-01-01", periods=3, tz="UTC"))
    df = pd.DataFrame({"dt_tz": tz_series})
    rep = profile(df)
    col = rep.columns["dt_tz"]
    assert col.kind == "datetime"
    assert set(col.stats.keys()) == EXPECTED_STATS_KEYS_BY_KIND["datetime"]
    assert col.stats["min"] == "2024-01-01T00:00:00+00:00"
    assert col.stats["max"] == "2024-01-03T00:00:00+00:00"
    assert col.stats["range_days"] == pytest.approx(2.0)


def test_contract_stability_key_sets():
    df = pd.DataFrame(
        {
            "num": [1.0, 2.0, 3.0, 4.0, 5.0],
            "cat": ["a", "b", "c", "d", "e"],
            "flag": [True, False, True, False, True],
            "dt": pd.date_range("2024-01-01", periods=5),
            "misc": [{"k": 1}, {"k": 2}, {"k": 1}, [1, 2], None],
        }
    )
    rep = profile(df)

    # 1. Top-level keys
    rep_dict = rep.to_dict()
    assert set(rep_dict.keys()) == EXPECTED_TOP_LEVEL_KEYS
    assert set(rep.keys()) == EXPECTED_TOP_LEVEL_KEYS

    # 2. DatasetProfile fields
    assert set(asdict(rep.dataset).keys()) == EXPECTED_DATASET_FIELDS
    assert set(rep_dict["dataset"].keys()) == EXPECTED_DATASET_FIELDS

    # 3. ColumnProfile fields
    for col_name, col_prof in rep.columns.items():
        assert set(asdict(col_prof).keys()) == EXPECTED_COLUMN_FIELDS
        assert set(rep_dict["columns"][col_name].keys()) == EXPECTED_COLUMN_FIELDS

    # 4. Stats keys per kind
    for col_name, expected_kind in [
        ("num", "numeric"),
        ("cat", "categorical"),
        ("flag", "boolean"),
        ("dt", "datetime"),
        ("misc", "other"),
    ]:
        col = rep.columns[col_name]
        assert col.kind == expected_kind
        assert set(col.stats.keys()) == EXPECTED_STATS_KEYS_BY_KIND[expected_kind]
        assert set(rep_dict["columns"][col_name]["stats"].keys()) == EXPECTED_STATS_KEYS_BY_KIND[expected_kind]

    # Empty columns of each kind retain identical stats keys
    empty_df = pd.DataFrame(
        {
            "num_empty": pd.Series([], dtype="float64"),
            "cat_empty": pd.Series([], dtype="category"),
            "flag_empty": pd.Series([], dtype="boolean"),
            "dt_empty": pd.Series([], dtype="datetime64[ns]"),
            "other_empty": pd.Series([], dtype="object"),
        }
    )
    empty_rep = profile(empty_df)
    for col_name, kind in [
        ("num_empty", "numeric"),
        ("cat_empty", "categorical"),
        ("flag_empty", "boolean"),
        ("dt_empty", "datetime"),
        ("other_empty", "other"),
    ]:
        col = empty_rep.columns[col_name]
        assert col.kind == kind
        assert set(col.stats.keys()) == EXPECTED_STATS_KEYS_BY_KIND[kind]


def test_contract_stability_value_types():
    df = pd.DataFrame(
        {
            "num": [10.0, 20.0, 0.0, 1000.0, 20.0, None],
            "cat": ["alpha", "beta", "alpha", None, "gamma", "delta"],
            "flag": [True, False, True, True, None, False],
            "dt": pd.to_datetime(["2024-01-01", "2024-01-05", "2024-01-03", None, "2024-01-02", "2024-01-04"]),
            "other": [pd.Timedelta(days=1), pd.Timedelta(days=2), pd.Timedelta(days=1), None, pd.Timedelta(days=3), pd.Timedelta(days=1)],
        }
    )
    rep = profile(df)

    # Dataset field types
    ds = rep.dataset
    assert isinstance(ds.row_count, int)
    assert isinstance(ds.column_count, int)
    assert isinstance(ds.total_cells, int)
    assert isinstance(ds.missing_count, int)
    assert isinstance(ds.missing_proportion, float)
    assert isinstance(ds.duplicate_rows, int)
    assert isinstance(ds.duplicate_proportion, float)
    assert isinstance(ds.empty_column_count, int)
    assert isinstance(ds.memory_bytes, int)
    assert isinstance(ds.dtype_counts, dict)

    # ColumnProfile field types
    for col in rep.columns.values():
        assert isinstance(col.name, str)
        assert isinstance(col.dtype, str)
        assert isinstance(col.inferred_type, str)
        assert isinstance(col.kind, str)
        assert isinstance(col.count, int)
        assert isinstance(col.missing_count, int)
        assert isinstance(col.missing_proportion, float)
        assert isinstance(col.distinct_count, int)
        assert isinstance(col.distinct_proportion, float)
        assert isinstance(col.is_unique, bool)
        assert isinstance(col.stats, dict)

    # Specific stats value types
    num_stats = rep.columns["num"].stats
    assert isinstance(num_stats["zeros_count"], int)
    assert isinstance(num_stats["zeros_proportion"], float)
    assert isinstance(num_stats["outliers_count"], int)
    assert isinstance(num_stats["min"], (int, float))
    assert isinstance(num_stats["max"], (int, float))
    assert isinstance(num_stats["mean"], (int, float))

    cat_stats = rep.columns["cat"].stats
    assert isinstance(cat_stats["top"], str)
    assert isinstance(cat_stats["top_frequency"], int)
    assert isinstance(cat_stats["top_proportion"], float)
    assert isinstance(cat_stats["min_length"], int)
    assert isinstance(cat_stats["max_length"], int)
    assert isinstance(cat_stats["mean_length"], float)

    bool_stats = rep.columns["flag"].stats
    assert isinstance(bool_stats["true_count"], int)
    assert isinstance(bool_stats["false_count"], int)
    assert isinstance(bool_stats["true_proportion"], float)

    dt_stats = rep.columns["dt"].stats
    assert isinstance(dt_stats["min"], str)
    assert isinstance(dt_stats["max"], str)
    assert isinstance(dt_stats["range_days"], float)

    other_stats = rep.columns["other"].stats
    assert isinstance(other_stats["top"], str)
    assert isinstance(other_stats["top_frequency"], int)
    assert isinstance(other_stats["top_proportion"], float)


def test_contract_json_dumps_mixed_dataframe():
    # Frame mixing every kind plus NaN/None, nullable dtypes, datetimes, timedeltas, and list/dict cells
    df = pd.DataFrame(
        {
            "num_standard": [1.5, 2.5, np.nan, 4.5],
            "num_nullable_int": pd.Series([10, None, 30, 40], dtype="Int64"),
            "num_nullable_float": pd.Series([1.1, 2.2, None, 4.4], dtype="Float64"),
            "cat_str": ["apple", "banana", None, "apple"],
            "cat_categorical": pd.Series(pd.Categorical(["cat", "dog", None, "cat"])),
            "cat_nullable_str": pd.Series(["x", None, "z", "x"], dtype="string"),
            "bool_standard": [True, False, None, True],
            "bool_nullable": pd.Series([True, None, False, False], dtype="boolean"),
            "dt_naive": pd.to_datetime(["2024-01-01", "2024-01-02", None, "2024-01-04"]),
            "dt_tz": pd.Series(pd.date_range("2024-01-01", periods=4, tz="UTC")),
            "timedelta_col": [pd.Timedelta(days=1), pd.Timedelta(hours=2), None, pd.Timedelta(days=1)],
            "unhashable_list": [["a", "b"], ["c"], None, ["a", "b"]],
            "unhashable_dict": [{"a": 1}, {"b": 2}, None, {"a": 1}],
        }
    )

    report = profile(df)
    raw_dict = report.to_dict()

    # Must serialize to JSON without throwing TypeError or ValueError
    dumped = json.dumps(raw_dict)
    assert isinstance(dumped, str)

    # Re-parse to verify round-trip integrity
    parsed = json.loads(dumped)
    assert set(parsed.keys()) == {"dataset", "columns"}
    assert parsed["dataset"]["row_count"] == 4
    assert parsed["dataset"]["column_count"] == 13
    assert "timedelta_col" in parsed["columns"]
    assert "unhashable_dict" in parsed["columns"]

