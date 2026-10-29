"""Tests for the data quality profiling API and report structures."""

from collections.abc import Mapping
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

    # 2. 0 rows, with column schema
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
    assert rep2.columns["num_col"].stats["min"] is None
    assert rep2.columns["num_col"].stats["max"] is None


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
