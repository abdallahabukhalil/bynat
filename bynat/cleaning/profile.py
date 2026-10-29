"""
DataFrame data quality profiling helpers and report data structures.
"""

import math
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ._common import _dedupe_key

__all__ = [
    "ProfileReport",
    "DatasetProfile",
    "ColumnProfile",
    "profile",
]


def _clean_scalar(val: Any) -> Any:
    """Convert numpy scalars and floating NaN/inf to native Python types or None."""
    if val is None:
        return None
    if isinstance(val, (list, dict, set, tuple)):
        return val
    if isinstance(val, np.ndarray):
        return val.tolist()
    try:
        if pd.isna(val):
            return None
    except (ValueError, TypeError):
        pass
    if isinstance(val, (float, np.floating)):
        f_val = float(val)
        if math.isnan(f_val) or math.isinf(f_val):
            return None
        return f_val
    if isinstance(val, (int, np.integer)):
        return int(val)
    if isinstance(val, (bool, np.bool_)):
        return bool(val)
    if isinstance(val, (pd.Timestamp, np.datetime64)):
        try:
            return pd.Timestamp(val).isoformat()
        except Exception:
            return str(val)
    return val


def _detect_column_kind(series: pd.Series) -> tuple[str, str]:
    """
    Classify a series into a broad kind and a fine-grained inferred type.

    Returns:
        tuple[str, str]: (kind, inferred_type), where kind is one of
        'numeric', 'categorical', 'boolean', 'datetime', or 'other'.
    """
    non_null = series.dropna()
    if non_null.empty:
        if pd.api.types.is_bool_dtype(series.dtype):
            return "boolean", "boolean"
        if pd.api.types.is_numeric_dtype(series.dtype):
            return "numeric", "numeric"
        if pd.api.types.is_datetime64_any_dtype(series.dtype):
            return "datetime", "datetime"
        if isinstance(series.dtype, pd.CategoricalDtype):
            return "categorical", "categorical"
        if isinstance(series.dtype, pd.StringDtype):
            return "categorical", "string"
        return "other", "empty"

    inferred = pd.api.types.infer_dtype(non_null, skipna=True)

    if pd.api.types.is_bool_dtype(series.dtype) or inferred == "boolean":
        return "boolean", inferred

    if pd.api.types.is_datetime64_any_dtype(series.dtype) or inferred in (
        "datetime64",
        "datetime",
        "date",
        "timestamp",
    ):
        return "datetime", inferred

    if pd.api.types.is_numeric_dtype(series.dtype) or inferred in (
        "integer",
        "floating",
        "decimal",
    ):
        return "numeric", inferred

    if isinstance(series.dtype, pd.CategoricalDtype) or inferred in (
        "categorical",
        "string",
        "unicode",
        "bytes",
    ):
        return "categorical", inferred

    if all(isinstance(x, str) for x in non_null):
        return "categorical", inferred

    return "other", inferred


def _compute_numeric_stats(series: pd.Series) -> dict[str, Any]:
    """Compute descriptive statistics for a numeric column."""
    non_null = series.dropna()
    empty_stats: dict[str, Any] = {
        "min": None,
        "max": None,
        "mean": None,
        "std": None,
        "median": None,
        "q25": None,
        "q75": None,
        "iqr": None,
        "zeros_count": 0,
        "zeros_proportion": 0.0,
        "outliers_count": None,
    }
    if non_null.empty:
        return empty_stats

    try:
        numeric = pd.to_numeric(non_null, errors="coerce").dropna()
    except Exception:
        return empty_stats

    if numeric.empty:
        return empty_stats

    n = len(numeric)
    min_val = _clean_scalar(numeric.min())
    max_val = _clean_scalar(numeric.max())
    mean_val = _clean_scalar(numeric.mean())
    median_val = _clean_scalar(numeric.median())
    std_val = _clean_scalar(numeric.std()) if n > 1 else None

    try:
        q25_raw = numeric.quantile(0.25)
        q75_raw = numeric.quantile(0.75)
        q25_val = _clean_scalar(q25_raw)
        q75_val = _clean_scalar(q75_raw)
    except Exception:
        q25_val = None
        q75_val = None

    if q25_val is not None and q75_val is not None:
        iqr_val: float | None = float(q75_val - q25_val)
    else:
        iqr_val = None

    zeros_count = int((numeric == 0).sum())
    zeros_proportion = float(zeros_count / n)

    outliers_count: int | None = None
    if n >= 5 and iqr_val is not None and q25_val is not None and q75_val is not None:
        lower_bound = q25_val - 1.5 * iqr_val
        upper_bound = q75_val + 1.5 * iqr_val
        outliers_count = int(((numeric < lower_bound) | (numeric > upper_bound)).sum())

    return {
        "min": min_val,
        "max": max_val,
        "mean": mean_val,
        "std": std_val,
        "median": median_val,
        "q25": q25_val,
        "q75": q75_val,
        "iqr": iqr_val,
        "zeros_count": zeros_count,
        "zeros_proportion": zeros_proportion,
        "outliers_count": outliers_count,
    }


def _compute_categorical_stats(series: pd.Series) -> dict[str, Any]:
    """Compute descriptive statistics for a categorical or string column."""
    non_null = series.dropna()
    if non_null.empty:
        return {
            "top": None,
            "top_frequency": None,
            "top_proportion": None,
            "min_length": None,
            "max_length": None,
            "mean_length": None,
        }

    n = len(non_null)
    try:
        counts = non_null.value_counts()
        top_val = counts.index[0]
        top_freq = int(counts.iloc[0])
    except TypeError:
        deduped = non_null.map(_dedupe_key)
        counts = deduped.value_counts()
        top_repr = counts.index[0]
        top_freq = int(counts.iloc[0])
        first_idx = deduped.tolist().index(top_repr)
        top_val = non_null.iloc[first_idx]

    top_prop = float(top_freq / n)

    str_items = [x for x in non_null if isinstance(x, str)]
    if len(str_items) == n and n > 0:
        lengths = [len(x) for x in str_items]
        min_len: int | None = int(min(lengths))
        max_len: int | None = int(max(lengths))
        mean_len: float | None = float(sum(lengths) / len(lengths))
    else:
        min_len = None
        max_len = None
        mean_len = None

    return {
        "top": _clean_scalar(top_val),
        "top_frequency": top_freq,
        "top_proportion": top_prop,
        "min_length": min_len,
        "max_length": max_len,
        "mean_length": mean_len,
    }


def _compute_boolean_stats(series: pd.Series) -> dict[str, Any]:
    """Compute descriptive statistics for a boolean column."""
    non_null = series.dropna()
    if non_null.empty:
        return {
            "true_count": 0,
            "false_count": 0,
            "true_proportion": None,
        }

    n = len(non_null)
    true_count = int((non_null == True).sum())  # noqa: E712
    false_count = int((non_null == False).sum())  # noqa: E712
    true_prop = float(true_count / n) if n > 0 else None

    return {
        "true_count": true_count,
        "false_count": false_count,
        "true_proportion": true_prop,
    }


def _compute_datetime_stats(series: pd.Series) -> dict[str, Any]:
    """Compute descriptive statistics for a datetime column."""
    non_null = series.dropna()
    if non_null.empty:
        return {
            "min": None,
            "max": None,
            "range_days": None,
        }

    try:
        dt_series = pd.to_datetime(non_null)
        min_dt = dt_series.min()
        max_dt = dt_series.max()
        if pd.isna(min_dt) or pd.isna(max_dt):
            return {
                "min": None,
                "max": None,
                "range_days": None,
            }
        min_iso = min_dt.isoformat()
        max_iso = max_dt.isoformat()
        range_days = float((max_dt - min_dt).total_seconds() / 86400.0)
        return {
            "min": min_iso,
            "max": max_iso,
            "range_days": range_days,
        }
    except Exception:
        return {
            "min": None,
            "max": None,
            "range_days": None,
        }


def _compute_other_stats(series: pd.Series) -> dict[str, Any]:
    """Compute descriptive statistics for mixed, object, or other column types."""
    non_null = series.dropna()
    if non_null.empty:
        return {
            "top": None,
            "top_frequency": None,
            "top_proportion": None,
        }

    n = len(non_null)
    try:
        counts = non_null.value_counts()
        top_val = counts.index[0]
        top_freq = int(counts.iloc[0])
    except TypeError:
        deduped = non_null.map(_dedupe_key)
        counts = deduped.value_counts()
        top_repr = counts.index[0]
        top_freq = int(counts.iloc[0])
        first_idx = deduped.tolist().index(top_repr)
        top_val = non_null.iloc[first_idx]

    top_prop = float(top_freq / n)
    return {
        "top": _clean_scalar(top_val),
        "top_frequency": top_freq,
        "top_proportion": top_prop,
    }


def _compute_distinct_count(series: pd.Series) -> int:
    """Compute the number of distinct non-null values safely."""
    non_null = series.dropna()
    if non_null.empty:
        return 0
    try:
        return int(non_null.nunique(dropna=True))
    except TypeError:
        return int(non_null.map(_dedupe_key).nunique(dropna=True))


def _compute_duplicate_rows(df: pd.DataFrame) -> int:
    """
    Count duplicate rows using keep='first' semantics.

    The first occurrence of each unique row is preserved; all subsequent
    identical rows are counted as duplicates.
    """
    if df.empty or len(df.columns) == 0:
        return 0
    try:
        return int(df.duplicated(keep="first").sum())
    except TypeError:
        deduped_df = df.map(_dedupe_key)
        return int(deduped_df.duplicated(keep="first").sum())


@dataclass
class DatasetProfile(Mapping[str, Any]):
    """
    Summary metrics for the entire DataFrame.

    Attributes:
        row_count (int): Total number of rows.
        column_count (int): Total number of profiled columns.
        total_cells (int): Total number of data cells (row_count * column_count).
        missing_count (int): Total number of missing cells across all columns.
        missing_proportion (float): Proportion of cells that are missing (0.0 to 1.0).
        duplicate_rows (int): Number of duplicate rows using keep='first' semantics.
        duplicate_proportion (float): Proportion of rows that are duplicates (0.0 to 1.0).
        empty_column_count (int): Number of columns containing zero non-null values.
        memory_bytes (int): Estimated in-memory size of the DataFrame in bytes.
        dtype_counts (dict[str, int]): Count of columns grouped by their dtype string.
    """

    row_count: int
    column_count: int
    total_cells: int
    missing_count: int
    missing_proportion: float
    duplicate_rows: int
    duplicate_proportion: float
    empty_column_count: int
    memory_bytes: int
    dtype_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        """Convert the dataset profile to a primitive Python dictionary."""
        return asdict(self)

    def __getitem__(self, item: str) -> Any:
        try:
            return getattr(self, item)
        except AttributeError:
            raise KeyError(item) from None

    def __iter__(self) -> Iterator[str]:
        return iter(asdict(self))

    def __len__(self) -> int:
        return len(asdict(self))


@dataclass
class ColumnProfile(Mapping[str, Any]):
    """
    Quality metrics and descriptive statistics for a single column.

    Attributes:
        name (Any): Column identifier/name.
        dtype (str): Pandas data type as a string (e.g. 'int64', 'object').
        inferred_type (str): Fine-grained detected type (e.g. 'integer', 'string').
        kind (str): Broad type classification: 'numeric', 'categorical',
            'boolean', 'datetime', or 'other'.
        count (int): Count of non-null values.
        missing_count (int): Count of missing (null/NaN/NA) values.
        missing_proportion (float): Proportion of column values that are missing.
        distinct_count (int): Number of distinct non-null values.
        distinct_proportion (float): Proportion of non-null values that are distinct.
        is_unique (bool): True if all non-null values are distinct and count > 0.
        stats (dict[str, Any]): Type-specific descriptive statistics, with None
            representing inapplicable or unavailable values.
    """

    name: Any
    dtype: str
    inferred_type: str
    kind: str
    count: int
    missing_count: int
    missing_proportion: float
    distinct_count: int
    distinct_proportion: float
    is_unique: bool
    stats: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert the column profile to a primitive Python dictionary."""
        return asdict(self)

    def __getitem__(self, item: str) -> Any:
        try:
            return getattr(self, item)
        except AttributeError:
            raise KeyError(item) from None

    def __iter__(self) -> Iterator[str]:
        return iter(asdict(self))

    def __len__(self) -> int:
        return len(asdict(self))


@dataclass
class ProfileReport(Mapping[str, Any]):
    """
    Comprehensive data quality report for a DataFrame.

    Supports attribute access (`report.dataset`, `report.columns`), dictionary
    access (`report['dataset']`, `report['columns']`), and programmatic conversions
    via `.to_dict()` and `.to_dataframe()`.

    Attributes:
        dataset (DatasetProfile): Aggregate metrics for the whole DataFrame.
        columns (dict[Any, ColumnProfile]): Mapping from column name to ColumnProfile.
        column_list (list[ColumnProfile]): Ordered list of column profiles.
    """

    dataset: DatasetProfile
    columns: dict[Any, ColumnProfile]
    column_list: list[ColumnProfile] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert the entire profile report to a primitive Python dictionary."""
        return {
            "dataset": self.dataset.to_dict(),
            "columns": {k: v.to_dict() for k, v in self.columns.items()},
        }

    def to_dataframe(self) -> pd.DataFrame:
        """
        Convert column profiles into a tabular pandas DataFrame summary.

        Each row corresponds to one profiled column with standard quality
        metrics and flattened statistics columns prefixed with `stat_`.
        """
        rows: list[dict[str, Any]] = []
        for col in self.column_list:
            row: dict[str, Any] = {
                "column": col.name,
                "dtype": col.dtype,
                "inferred_type": col.inferred_type,
                "kind": col.kind,
                "count": col.count,
                "missing_count": col.missing_count,
                "missing_proportion": col.missing_proportion,
                "distinct_count": col.distinct_count,
                "distinct_proportion": col.distinct_proportion,
                "is_unique": col.is_unique,
            }
            for stat_k, stat_v in col.stats.items():
                row[f"stat_{stat_k}"] = stat_v
            rows.append(row)
        return pd.DataFrame(rows)

    def __getitem__(self, key: str) -> Any:
        if key == "dataset":
            return self.dataset
        if key == "columns":
            return self.columns
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        yield "dataset"
        yield "columns"

    def __len__(self) -> int:
        return 2

    def __repr__(self) -> str:
        return (
            f"ProfileReport(rows={self.dataset.row_count}, "
            f"columns={self.dataset.column_count}, "
            f"missing={self.dataset.missing_count} ({self.dataset.missing_proportion:.1%}), "
            f"duplicate_rows={self.dataset.duplicate_rows})"
        )


def profile(
    df: pd.DataFrame,
    *columns: Any,
    output_type: Any = None,
) -> Any:
    """
    Generate a structured, non-mutating data quality profile report for a DataFrame.

    Parameters:
        df (pd.DataFrame): The DataFrame to profile. The input DataFrame is never
            mutated.
        *columns: Optional specific column name(s) to profile. Can be passed as
            multiple arguments or a single sequence of column names. When omitted,
            all columns in the DataFrame are profiled.
        output_type: The format of the returned report. Options:
            - None (default) or ProfileReport: returns a structured ProfileReport
              object supporting attribute access, mapping access, and programmatic
              conversion via `.to_dict()` and `.to_dataframe()`.
            - dict: returns a pure Python dictionary of primitive types.
            - pandas.DataFrame: returns a tabular summary DataFrame with one row
              per profiled column.
            - BaseData subclass: returns the summary DataFrame wrapped in that
              subclass.

    Returns:
        ProfileReport | dict | pd.DataFrame: The data quality report in the
        requested format.

    Raises:
        KeyError: If any specified column name is not present in the DataFrame.
        TypeError: If an unsupported output_type is specified.
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"Expected a pandas DataFrame, got {type(df).__name__}")

    # Determine columns to profile
    if columns:
        if len(columns) == 1 and isinstance(columns[0], (list, tuple, set)):
            target_cols = list(columns[0])
        else:
            target_cols = list(columns)
        for col in target_cols:
            if col not in df.columns:
                raise KeyError(f"Column '{col}' not found in DataFrame.")
        frame = df[target_cols]
    else:
        target_cols = list(df.columns)
        frame = df

    row_count = len(frame)
    column_count = len(target_cols)
    total_cells = row_count * column_count

    total_missing = int(frame.isna().sum().sum()) if column_count > 0 else 0
    missing_proportion = float(total_missing / total_cells) if total_cells > 0 else 0.0

    duplicate_rows = _compute_duplicate_rows(frame)
    duplicate_proportion = float(duplicate_rows / row_count) if row_count > 0 else 0.0

    empty_column_count = 0
    dtype_counts: dict[str, int] = {}
    col_profiles_dict: dict[Any, ColumnProfile] = {}
    col_profiles_list: list[ColumnProfile] = []

    for i, col in enumerate(target_cols):
        series = frame.iloc[:, i]
        dtype_str = str(series.dtype)
        dtype_counts[dtype_str] = dtype_counts.get(dtype_str, 0) + 1

        kind, inferred_type = _detect_column_kind(series)
        non_null_count = int(series.count())
        missing_count = int(series.isna().sum())
        col_missing_prop = float(missing_count / row_count) if row_count > 0 else 0.0

        if non_null_count == 0:
            empty_column_count += 1

        distinct_count = _compute_distinct_count(series)
        distinct_prop = float(distinct_count / non_null_count) if non_null_count > 0 else 0.0
        is_unique = bool(distinct_count == non_null_count and non_null_count > 0)

        # Compute kind-specific stats
        if kind == "numeric":
            stats = _compute_numeric_stats(series)
        elif kind == "categorical":
            stats = _compute_categorical_stats(series)
        elif kind == "boolean":
            stats = _compute_boolean_stats(series)
        elif kind == "datetime":
            stats = _compute_datetime_stats(series)
        else:
            stats = _compute_other_stats(series)

        col_prof = ColumnProfile(
            name=col,
            dtype=dtype_str,
            inferred_type=inferred_type,
            kind=kind,
            count=non_null_count,
            missing_count=missing_count,
            missing_proportion=col_missing_prop,
            distinct_count=distinct_count,
            distinct_proportion=distinct_prop,
            is_unique=is_unique,
            stats=stats,
        )
        col_profiles_dict[col] = col_prof
        col_profiles_list.append(col_prof)

    try:
        memory_bytes = int(frame.memory_usage(deep=True).sum()) if not frame.empty else 0
    except Exception:
        memory_bytes = 0

    dataset_prof = DatasetProfile(
        row_count=row_count,
        column_count=column_count,
        total_cells=total_cells,
        missing_count=total_missing,
        missing_proportion=missing_proportion,
        duplicate_rows=duplicate_rows,
        duplicate_proportion=duplicate_proportion,
        empty_column_count=empty_column_count,
        memory_bytes=memory_bytes,
        dtype_counts=dtype_counts,
    )

    report = ProfileReport(
        dataset=dataset_prof,
        columns=col_profiles_dict,
        column_list=col_profiles_list,
    )

    # Format according to requested output_type
    if output_type is None or output_type is ProfileReport:
        return report

    if output_type is dict:
        return report.to_dict()

    if output_type is pd.DataFrame:
        return report.to_dataframe()

    from bynat.cleaning.base import BaseData

    if isinstance(output_type, type) and issubclass(output_type, BaseData):
        return output_type(data=report.to_dataframe())

    raise TypeError(
        f"Unsupported output_type: {output_type}. Expected None, ProfileReport, "
        "dict, pandas.DataFrame, or a BaseData subclass."
    )
