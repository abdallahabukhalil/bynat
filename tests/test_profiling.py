"""Tests for bynat.cleaning.profiling (DQ-01)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

import bynat
from bynat.cleaning import BaseData, BynatAccessor
from bynat.cleaning.profiling import (
    ColumnReport,
    DataQualityReport,
    profile,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _col(report: DataQualityReport, name: str) -> ColumnReport:
    """Return the ColumnReport for `name`, failing clearly if absent."""
    for col in report.columns:
        if col.name == name:
            return col
    raise KeyError(f"column {name!r} not found in report")


# ---------------------------------------------------------------------------
# Report structure and output contract
# ---------------------------------------------------------------------------

class TestReportStructure:
    def test_returns_dataqualityreport_instance(self):
        df = pd.DataFrame({"a": [1, 2]})
        report = profile(df)
        assert isinstance(report, DataQualityReport)

    def test_columns_is_a_tuple_of_columnreports(self):
        df = pd.DataFrame({"x": [1], "y": ["a"]})
        report = profile(df)
        assert isinstance(report.columns, tuple)
        assert all(isinstance(c, ColumnReport) for c in report.columns)

    def test_column_order_matches_dataframe(self):
        df = pd.DataFrame({"z": [1], "a": [2], "m": [3]})
        report = profile(df)
        assert [c.name for c in report.columns] == list(df.columns)

    def test_column_count_matches(self):
        df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
        assert profile(df).column_count == 3

    def test_row_count_matches(self):
        df = pd.DataFrame({"a": range(7)})
        assert profile(df).row_count == 7

    def test_dataqualityreport_is_frozen(self):
        df = pd.DataFrame({"a": [1]})
        report = profile(df)
        with pytest.raises((AttributeError, TypeError)):
            report.row_count = 99  # type: ignore[misc]

    def test_columnreport_is_frozen(self):
        df = pd.DataFrame({"a": [1]})
        col = profile(df).columns[0]
        with pytest.raises((AttributeError, TypeError)):
            col.missing_count = 99  # type: ignore[misc]

    def test_dtype_field_is_string(self):
        df = pd.DataFrame({"a": [1], "b": ["x"]})
        report = profile(df)
        for col in report.columns:
            assert isinstance(col.dtype, str)

    def test_stats_field_is_dict(self):
        df = pd.DataFrame({"a": [1, 2]})
        report = profile(df)
        assert isinstance(report.columns[0].stats, dict)

    def test_top_level_public_exports(self):
        """DataQualityReport, ColumnReport, and profile are top-level bynat exports."""
        assert bynat.DataQualityReport is DataQualityReport
        assert bynat.ColumnReport is ColumnReport
        assert bynat.profile is profile


# ---------------------------------------------------------------------------
# Empty DataFrame edge cases
# ---------------------------------------------------------------------------

class TestEmptyDataFrame:
    def test_empty_frame_no_columns(self):
        df = pd.DataFrame()
        report = profile(df)
        assert report.row_count == 0
        assert report.column_count == 0
        assert report.total_missing == 0
        assert report.overall_missing_proportion == 0.0
        assert report.duplicate_row_count == 0
        assert report.columns == ()

    def test_empty_frame_with_columns(self):
        df = pd.DataFrame({"a": pd.Series([], dtype="int64"), "b": pd.Series([], dtype="object")})
        report = profile(df)
        assert report.row_count == 0
        assert report.column_count == 2
        assert report.total_missing == 0
        assert report.overall_missing_proportion == 0.0
        assert report.duplicate_row_count == 0

    def test_empty_frame_numeric_stats_all_none(self):
        df = pd.DataFrame({"v": pd.Series([], dtype="float64")})
        report = profile(df)
        col = report.columns[0]
        assert col.missing_count == 0
        assert col.missing_proportion == 0.0
        assert col.stats["count"] == 0
        assert col.stats["mean"] is None
        assert col.stats["std"] is None
        assert col.stats["min"] is None
        assert col.stats["max"] is None

    def test_empty_frame_categorical_stats_all_none(self):
        df = pd.DataFrame({"s": pd.Series([], dtype="object")})
        col = profile(df).columns[0]
        assert col.stats["count"] == 0
        assert col.stats["unique"] == 0
        assert col.stats["top"] is None
        assert col.stats["top_freq"] is None

    def test_empty_frame_datetime_stats_all_none(self):
        df = pd.DataFrame({"d": pd.Series([], dtype="datetime64[ns]")})
        col = profile(df).columns[0]
        assert col.stats["count"] == 0
        assert col.stats["min"] is None
        assert col.stats["max"] is None


# ---------------------------------------------------------------------------
# Missing-value metrics
# ---------------------------------------------------------------------------

class TestMissingValues:
    def test_no_missing_values(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        report = profile(df)
        assert report.total_missing == 0
        assert report.overall_missing_proportion == 0.0
        for col in report.columns:
            assert col.missing_count == 0
            assert col.missing_proportion == 0.0

    def test_all_missing_single_column(self):
        df = pd.DataFrame({"a": [None, None, None]})
        report = profile(df)
        col = report.columns[0]
        assert col.missing_count == 3
        assert math.isclose(col.missing_proportion, 1.0)
        assert report.total_missing == 3
        assert math.isclose(report.overall_missing_proportion, 1.0)

    def test_partial_missing_values(self):
        df = pd.DataFrame({"a": [1, None, 3], "b": [None, None, "z"]})
        report = profile(df)
        assert report.total_missing == 3
        assert math.isclose(report.overall_missing_proportion, 3 / 6)
        assert _col(report, "a").missing_count == 1
        assert math.isclose(_col(report, "a").missing_proportion, 1 / 3)
        assert _col(report, "b").missing_count == 2
        assert math.isclose(_col(report, "b").missing_proportion, 2 / 3)

    def test_nan_float_counted_as_missing(self):
        df = pd.DataFrame({"v": [1.0, float("nan"), 3.0]})
        assert _col(profile(df), "v").missing_count == 1

    def test_pd_na_counted_as_missing(self):
        df = pd.DataFrame({"v": pd.array([1, pd.NA, 3], dtype="Int64")})
        assert _col(profile(df), "v").missing_count == 1

    def test_nat_datetime_counted_as_missing(self):
        df = pd.DataFrame({"d": pd.to_datetime(["2024-01-01", None, "2024-03-01"])})
        assert _col(profile(df), "d").missing_count == 1

    def test_column_all_missing_stats_count_is_zero(self):
        df = pd.DataFrame({"v": [None, None, None]}, dtype="float64")
        col = _col(profile(df), "v")
        assert col.stats["count"] == 0
        assert col.stats["mean"] is None

    def test_missing_proportion_zero_when_no_rows(self):
        df = pd.DataFrame({"v": pd.Series([], dtype="float64")})
        assert _col(profile(df), "v").missing_proportion == 0.0


# ---------------------------------------------------------------------------
# Duplicate rows
# ---------------------------------------------------------------------------

class TestDuplicateRows:
    def test_no_duplicates(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        assert profile(df).duplicate_row_count == 0

    def test_one_duplicate(self):
        df = pd.DataFrame({"a": [1, 2, 1], "b": ["x", "y", "x"]})
        assert profile(df).duplicate_row_count == 1

    def test_all_rows_identical(self):
        df = pd.DataFrame({"a": [1, 1, 1]})
        # first occurrence not counted → 2 duplicates
        assert profile(df).duplicate_row_count == 2

    def test_first_occurrence_not_counted(self):
        df = pd.DataFrame({"a": [5, 5, 5, 5]})
        assert profile(df).duplicate_row_count == 3

    def test_multiple_distinct_duplicate_groups(self):
        df = pd.DataFrame({"a": [1, 2, 1, 2, 3]})
        # rows 2 and 3 (0-indexed) duplicate rows 0 and 1
        assert profile(df).duplicate_row_count == 2

    def test_empty_frame_zero_duplicates(self):
        assert profile(pd.DataFrame()).duplicate_row_count == 0

    def test_single_row_zero_duplicates(self):
        assert profile(pd.DataFrame({"a": [42]})).duplicate_row_count == 0


# ---------------------------------------------------------------------------
# Column dtype detection
# ---------------------------------------------------------------------------

class TestColumnDtype:
    def test_int64_dtype_string(self):
        df = pd.DataFrame({"v": pd.array([1, 2, 3], dtype="int64")})
        assert _col(profile(df), "v").dtype == "int64"

    def test_float64_dtype_string(self):
        df = pd.DataFrame({"v": [1.0, 2.5]})
        assert _col(profile(df), "v").dtype == "float64"

    def test_object_dtype_string(self):
        df = pd.DataFrame({"s": ["a", "b"]})
        assert _col(profile(df), "s").dtype == "object"

    def test_nullable_int_dtype_string(self):
        df = pd.DataFrame({"v": pd.array([1, 2], dtype="Int64")})
        assert _col(profile(df), "v").dtype == "Int64"

    def test_datetime_dtype_string(self):
        df = pd.DataFrame({"d": pd.to_datetime(["2024-01-01"])})
        assert "datetime" in _col(profile(df), "d").dtype


# ---------------------------------------------------------------------------
# Numeric statistics
# ---------------------------------------------------------------------------

class TestNumericStats:
    def test_stat_keys_present(self):
        df = pd.DataFrame({"v": [1.0, 2.0, 3.0, 4.0]})
        stats = _col(profile(df), "v").stats
        for key in ("count", "mean", "std", "min", "p25", "p50", "p75", "max"):
            assert key in stats, f"missing key: {key!r}"

    def test_count_excludes_missing(self):
        df = pd.DataFrame({"v": [1.0, None, 3.0]})
        assert _col(profile(df), "v").stats["count"] == 2

    def test_mean_correct(self):
        df = pd.DataFrame({"v": [2.0, 4.0, 6.0]})
        assert math.isclose(_col(profile(df), "v").stats["mean"], 4.0)

    def test_std_correct(self):
        df = pd.DataFrame({"v": [2.0, 4.0, 6.0]})
        # sample std of [2, 4, 6] = 2.0
        assert math.isclose(_col(profile(df), "v").stats["std"], 2.0)

    def test_std_is_zero_for_single_value(self):
        df = pd.DataFrame({"v": [42.0]})
        assert _col(profile(df), "v").stats["std"] == 0.0

    def test_min_max_correct(self):
        df = pd.DataFrame({"v": [3.0, 1.0, 4.0, 1.0, 5.0]})
        stats = _col(profile(df), "v").stats
        assert stats["min"] == 1.0
        assert stats["max"] == 5.0

    def test_percentiles_correct(self):
        df = pd.DataFrame({"v": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]})
        stats = _col(profile(df), "v").stats
        # numpy nanpercentile of [1..8] at [25, 50, 75]
        expected_p25, expected_p50, expected_p75 = np.nanpercentile([1, 2, 3, 4, 5, 6, 7, 8], [25, 50, 75])
        assert math.isclose(stats["p25"], expected_p25)
        assert math.isclose(stats["p50"], expected_p50)
        assert math.isclose(stats["p75"], expected_p75)

    def test_mean_ignores_missing(self):
        df = pd.DataFrame({"v": [10.0, None, 30.0]})
        assert math.isclose(_col(profile(df), "v").stats["mean"], 20.0)

    def test_nullable_int_treated_as_numeric(self):
        df = pd.DataFrame({"v": pd.array([10, 20, 30], dtype="Int64")})
        stats = _col(profile(df), "v").stats
        assert stats["count"] == 3
        assert math.isclose(stats["mean"], 20.0)

    def test_nullable_float_treated_as_numeric(self):
        df = pd.DataFrame({"v": pd.array([1.0, 2.0, 3.0], dtype="Float64")})
        stats = _col(profile(df), "v").stats
        assert stats["count"] == 3

    def test_boolean_dtype_treated_as_numeric(self):
        df = pd.DataFrame({"v": pd.array([True, False, True], dtype="boolean")})
        stats = _col(profile(df), "v").stats
        assert "count" in stats
        assert "mean" in stats

    def test_integer_column_stats_are_float_values(self):
        df = pd.DataFrame({"v": [1, 2, 3, 4]})
        stats = _col(profile(df), "v").stats
        for key in ("mean", "std", "min", "max", "p25", "p50", "p75"):
            assert isinstance(stats[key], float), f"{key} should be float"


# ---------------------------------------------------------------------------
# Categorical / string statistics
# ---------------------------------------------------------------------------

class TestCategoricalStats:
    def test_stat_keys_present(self):
        df = pd.DataFrame({"s": ["a", "b", "a"]})
        stats = _col(profile(df), "s").stats
        for key in ("count", "unique", "top", "top_freq"):
            assert key in stats, f"missing key: {key!r}"

    def test_count_excludes_missing(self):
        df = pd.DataFrame({"s": ["a", None, "b"]})
        assert _col(profile(df), "s").stats["count"] == 2

    def test_unique_correct(self):
        df = pd.DataFrame({"s": ["a", "b", "a", "c"]})
        assert _col(profile(df), "s").stats["unique"] == 3

    def test_top_is_most_frequent(self):
        df = pd.DataFrame({"s": ["a", "b", "a", "a", "b"]})
        stats = _col(profile(df), "s").stats
        assert stats["top"] == "a"
        assert stats["top_freq"] == 3

    def test_all_unique_top_freq_is_one(self):
        df = pd.DataFrame({"s": ["x", "y", "z"]})
        assert _col(profile(df), "s").stats["top_freq"] == 1

    def test_category_dtype_recognized(self):
        df = pd.DataFrame({"c": pd.Categorical(["a", "b", "a"])})
        stats = _col(profile(df), "c").stats
        for key in ("count", "unique", "top", "top_freq"):
            assert key in stats

    def test_string_extension_dtype_recognized(self):
        df = pd.DataFrame({"s": pd.array(["hello", "world", "hello"], dtype="string")})
        stats = _col(profile(df), "s").stats
        for key in ("count", "unique", "top", "top_freq"):
            assert key in stats

    def test_all_missing_categorical_zeros_and_none(self):
        df = pd.DataFrame({"s": pd.array([None, None], dtype="object")})
        stats = _col(profile(df), "s").stats
        assert stats["count"] == 0
        assert stats["unique"] == 0
        assert stats["top"] is None
        assert stats["top_freq"] is None


# ---------------------------------------------------------------------------
# Datetime statistics
# ---------------------------------------------------------------------------

class TestDatetimeStats:
    def test_stat_keys_present(self):
        df = pd.DataFrame({"d": pd.to_datetime(["2024-01-01", "2024-06-15"])})
        stats = _col(profile(df), "d").stats
        for key in ("count", "min", "max"):
            assert key in stats

    def test_count_excludes_nat(self):
        df = pd.DataFrame({"d": pd.to_datetime(["2024-01-01", None, "2024-03-01"])})
        assert _col(profile(df), "d").stats["count"] == 2

    def test_min_max_correct(self):
        dates = pd.to_datetime(["2024-03-01", "2024-01-01", "2024-06-15"])
        df = pd.DataFrame({"d": dates})
        stats = _col(profile(df), "d").stats
        assert stats["min"] == pd.Timestamp("2024-01-01")
        assert stats["max"] == pd.Timestamp("2024-06-15")

    def test_all_nat_stats_count_zero(self):
        df = pd.DataFrame({"d": pd.to_datetime([None, None])})
        stats = _col(profile(df), "d").stats
        assert stats["count"] == 0
        assert stats["min"] is None
        assert stats["max"] is None


# ---------------------------------------------------------------------------
# Other / unsupported dtypes
# ---------------------------------------------------------------------------

class TestOtherDtypes:
    def test_timedelta_returns_empty_stats(self):
        df = pd.DataFrame({"td": pd.to_timedelta(["1 days", "2 days"])})
        stats = _col(profile(df), "td").stats
        assert stats == {}

    def test_complex_dtype_returns_empty_stats(self):
        df = pd.DataFrame({"c": np.array([1 + 2j, 3 + 4j])})
        stats = _col(profile(df), "c").stats
        assert stats == {}


# ---------------------------------------------------------------------------
# Non-mutating contract
# ---------------------------------------------------------------------------

class TestNonMutating:
    def test_profile_does_not_modify_input(self):
        df = pd.DataFrame({"a": [1, None, 3], "b": ["x", "y", "x"]})
        original = df.copy()
        profile(df)
        pd.testing.assert_frame_equal(df, original)

    def test_profile_does_not_modify_input_with_duplicates(self):
        df = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]})
        original = df.copy()
        profile(df)
        pd.testing.assert_frame_equal(df, original)

    def test_profile_via_accessor_does_not_modify_input(self):
        df = pd.DataFrame({"a": [1, None, 3]})
        original = df.copy()
        df.bynat.profile()
        pd.testing.assert_frame_equal(df, original)


# ---------------------------------------------------------------------------
# Accessor integration
# ---------------------------------------------------------------------------

class TestAccessorIntegration:
    def test_accessor_profile_returns_dataqualityreport(self):
        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        report = df.bynat.profile()
        assert isinstance(report, DataQualityReport)

    def test_accessor_matches_standalone_function(self):
        df = pd.DataFrame({"a": [1, None, 3], "b": ["x", "x", "z"]})
        via_accessor = df.bynat.profile()
        via_function = profile(df)
        assert via_accessor.row_count == via_function.row_count
        assert via_accessor.total_missing == via_function.total_missing
        assert via_accessor.duplicate_row_count == via_function.duplicate_row_count
        for ca, cf in zip(via_accessor.columns, via_function.columns):
            assert ca.name == cf.name
            assert ca.missing_count == cf.missing_count
            assert ca.stats == cf.stats

    def test_accessor_is_accessible_on_bynat_class(self):
        assert hasattr(BynatAccessor, "profile")


# ---------------------------------------------------------------------------
# BaseData integration
# ---------------------------------------------------------------------------

class TestBaseDataIntegration:
    def test_basedata_profile_returns_dataqualityreport(self, tmp_path):
        frame = BaseData({"a": [1, None, 3], "b": ["x", "x", "z"]}, cache_dir=tmp_path)
        report = frame.profile()
        assert isinstance(report, DataQualityReport)

    def test_basedata_profile_matches_standalone(self, tmp_path):
        data = {"a": [1, None, 3], "b": ["x", "x", "z"]}
        frame = BaseData(data, cache_dir=tmp_path)
        df = pd.DataFrame(data)
        bd_report = frame.profile()
        df_report = profile(df)
        assert bd_report.row_count == df_report.row_count
        assert bd_report.total_missing == df_report.total_missing
        assert bd_report.duplicate_row_count == df_report.duplicate_row_count

    def test_basedata_profile_does_not_modify_frame(self, tmp_path):
        frame = BaseData({"a": [1, None, 3]}, cache_dir=tmp_path)
        original = pd.DataFrame(frame)
        frame.profile()
        pd.testing.assert_frame_equal(pd.DataFrame(frame), original)


# ---------------------------------------------------------------------------
# Overall missing proportion
# ---------------------------------------------------------------------------

class TestOverallMissingProportion:
    def test_proportion_zero_when_no_missing(self):
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        assert profile(df).overall_missing_proportion == 0.0

    def test_proportion_one_when_all_missing(self):
        df = pd.DataFrame({"a": [None, None], "b": [None, None]})
        assert math.isclose(profile(df).overall_missing_proportion, 1.0)

    def test_proportion_correct_value(self):
        # 4 rows, 2 cols = 8 cells; 2 missing → 0.25
        df = pd.DataFrame({"a": [1, None, 3, 4], "b": [5, 6, None, 8]})
        assert math.isclose(profile(df).overall_missing_proportion, 2 / 8)

    def test_proportion_zero_for_empty_frame(self):
        assert profile(pd.DataFrame()).overall_missing_proportion == 0.0


# ---------------------------------------------------------------------------
# Multiple columns mixed types
# ---------------------------------------------------------------------------

class TestMixedTypes:
    def test_mixed_type_frame_all_columns_profiled(self):
        df = pd.DataFrame({
            "int_col": [1, 2, None],
            "float_col": [1.5, None, 3.5],
            "str_col": ["a", "b", "a"],
            "date_col": pd.to_datetime(["2024-01-01", None, "2024-03-01"]),
        })
        report = profile(df)
        assert report.column_count == 4
        assert report.total_missing == 3  # 1 in int, 1 in float, 1 in date

        int_rep = _col(report, "int_col")
        assert int_rep.stats["count"] == 2

        float_rep = _col(report, "float_col")
        assert float_rep.stats["count"] == 2

        str_rep = _col(report, "str_col")
        assert str_rep.stats["unique"] == 2

        date_rep = _col(report, "date_col")
        assert date_rep.stats["count"] == 2


# ---------------------------------------------------------------------------
# Single-column and single-row frames
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_single_column_frame(self):
        df = pd.DataFrame({"only": [1, 2, 3]})
        report = profile(df)
        assert report.column_count == 1
        assert len(report.columns) == 1

    def test_single_row_frame(self):
        df = pd.DataFrame({"a": [42], "b": ["hello"]})
        report = profile(df)
        assert report.row_count == 1
        assert report.duplicate_row_count == 0

    def test_single_cell_frame(self):
        df = pd.DataFrame({"a": [None]})
        report = profile(df)
        assert report.row_count == 1
        assert report.total_missing == 1
        assert math.isclose(report.overall_missing_proportion, 1.0)

    def test_profile_with_named_index(self):
        df = pd.DataFrame({"a": [1, 2, 3]}, index=["x", "y", "z"])
        report = profile(df)
        assert report.row_count == 3

    def test_profile_with_range_index(self):
        df = pd.DataFrame({"a": range(5)})
        assert profile(df).row_count == 5
