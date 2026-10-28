"""Data quality profiling: a structured report from a single `profile()` call."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

__all__ = ["DataQualityReport", "ColumnReport", "profile"]


# ---------------------------------------------------------------------------
# Public data-contract types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ColumnReport:
    """Per-column data quality metrics.

    Attributes:
        name (str): Column label.
        dtype (str): pandas dtype string (e.g. ``"int64"``, ``"object"``).
        missing_count (int): Number of missing values (``NaN`` / ``None`` /
            ``pd.NA``) in the column.
        missing_proportion (float): ``missing_count / total_rows``.  ``0.0``
            when ``total_rows`` is zero.
        stats (dict[str, Any]): Descriptive statistics keyed by statistic name.
            The set of keys depends on the column dtype:

            * **Numeric columns** (``numpy.number`` subtypes, including
              nullable ``Int*`` / ``Float*`` and ``boolean``):
              ``count``, ``mean``, ``std``, ``min``, ``p25``, ``p50``,
              ``p75``, ``max``.
            * **Categorical / string columns** (``object``, ``string``,
              ``category``): ``count``, ``unique``, ``top``, ``top_freq``.
            * **Datetime columns** (``datetime64``, ``DatetimeTZDtype``):
              ``count``, ``min``, ``max``.
            * **Other dtypes**: an empty dict ``{}``.

            Statistics derived from non-null values only.  When a column
            contains only missing values, ``count`` is ``0`` and all remaining
            statistics are ``None``.
    """

    name: str
    dtype: str
    missing_count: int
    missing_proportion: float
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DataQualityReport:
    """Structured data quality report produced by :func:`profile`.

    Attributes:
        row_count (int): Total number of rows in the DataFrame.
        column_count (int): Total number of columns in the DataFrame.
        total_missing (int): Sum of missing values across all cells
            (``row_count * column_count`` minus non-missing cells).
        overall_missing_proportion (float): ``total_missing / (row_count *
            column_count)``.  ``0.0`` when the DataFrame is empty.
        duplicate_row_count (int): Number of rows that are exact duplicates of
            a preceding row.  Counted by ``pandas.DataFrame.duplicated()``
            with ``keep='first'``, so the first occurrence of a duplicated
            value is *not* counted.
        columns (tuple[ColumnReport, ...]): One :class:`ColumnReport` per
            column, in column order.
    """

    row_count: int
    column_count: int
    total_missing: int
    overall_missing_proportion: float
    duplicate_row_count: int
    columns: tuple[ColumnReport, ...]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_numeric(series: pd.Series) -> bool:
    """Return True for numpy numeric and nullable numeric/boolean dtypes."""
    dtype = series.dtype
    if pd.api.types.is_numeric_dtype(dtype):
        # Exclude complex numbers: profiling numeric stats on complex is
        # meaningless (pandas itself warns about discarding imaginary parts).
        if pd.api.types.is_complex_dtype(dtype):
            return False
        return True
    # Nullable pandas extension types: Int8–Int64, UInt8–UInt64, Float32/64, boolean
    if isinstance(dtype, pd.api.extensions.ExtensionDtype):
        kind = getattr(dtype, "kind", None)
        if kind in ("i", "u", "f", "b"):          # int, uint, float, bool
            return True
    return False


def _is_datetime(series: pd.Series) -> bool:
    """Return True for datetime64 and DatetimeTZDtype columns."""
    return pd.api.types.is_datetime64_any_dtype(series.dtype)


def _is_categorical(series: pd.Series) -> bool:
    """Return True for object, string extension, and category dtype columns."""
    dtype = series.dtype
    if pd.api.types.is_object_dtype(dtype):
        return True
    if pd.api.types.is_string_dtype(dtype):
        return True
    if isinstance(dtype, pd.CategoricalDtype):
        return True
    return False


def _numeric_stats(series: pd.Series) -> dict[str, Any]:
    """Return count/mean/std/min/p25/p50/p75/max for a numeric series."""
    # Cast to float so that nullable Int/Float/boolean extension types work
    # uniformly with np.nanpercentile and related functions.
    values = series.dropna()

    count = len(values)
    if count == 0:
        return {
            "count": 0,
            "mean": None,
            "std": None,
            "min": None,
            "p25": None,
            "p50": None,
            "p75": None,
            "max": None,
        }

    arr = values.to_numpy(dtype=float, na_value=np.nan)
    with np.errstate(all="ignore"):
        mean = float(np.nanmean(arr))
        std = float(np.nanstd(arr, ddof=1)) if count > 1 else 0.0
        min_val = float(np.nanmin(arr))
        max_val = float(np.nanmax(arr))
        p25, p50, p75 = np.nanpercentile(arr, [25, 50, 75]).tolist()

    return {
        "count": count,
        "mean": mean,
        "std": std,
        "min": min_val,
        "p25": float(p25),
        "p50": float(p50),
        "p75": float(p75),
        "max": max_val,
    }


def _categorical_stats(series: pd.Series) -> dict[str, Any]:
    """Return count/unique/top/top_freq for a categorical-like series."""
    values = series.dropna()
    count = len(values)
    if count == 0:
        return {"count": 0, "unique": 0, "top": None, "top_freq": None}

    n_unique = int(values.nunique())

    try:
        vc = values.value_counts(sort=True, ascending=False, dropna=True)
        if vc.empty:
            top: Any = None
            top_freq: int | None = None
        else:
            top = vc.index[0]
            top_freq = int(vc.iloc[0])
    except TypeError:
        # Unhashable element types: count uniques but leave top as None
        n_unique = len({str(v) for v in values})
        top = None
        top_freq = None

    return {"count": count, "unique": n_unique, "top": top, "top_freq": top_freq}


def _datetime_stats(series: pd.Series) -> dict[str, Any]:
    """Return count/min/max for a datetime series."""
    values = series.dropna()
    count = len(values)
    if count == 0:
        return {"count": 0, "min": None, "max": None}

    return {
        "count": count,
        "min": values.min(),
        "max": values.max(),
    }


def _column_stats(series: pd.Series) -> dict[str, Any]:
    """Dispatch to the appropriate stats helper based on dtype."""
    if _is_numeric(series):
        return _numeric_stats(series)
    if _is_datetime(series):
        return _datetime_stats(series)
    if _is_categorical(series):
        return _categorical_stats(series)
    return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def profile(df: pd.DataFrame) -> DataQualityReport:
    """Generate a structured data quality report from a DataFrame.

    The source DataFrame is never modified.  All metrics are computed
    from a read-only view of the frame.

    Args:
        df (pandas.DataFrame): The DataFrame to profile.

    Returns:
        DataQualityReport: A frozen, structured report with the following
        top-level fields:

        * **row_count** — total rows.
        * **column_count** — total columns.
        * **total_missing** — missing cells across the entire frame.
        * **overall_missing_proportion** — ``total_missing /
          (row_count * column_count)``; ``0.0`` for empty frames.
        * **duplicate_row_count** — rows that duplicate a preceding row
          (``DataFrame.duplicated(keep='first').sum()``).
        * **columns** — a tuple of :class:`ColumnReport` objects, one
          per column in column order.

    Each :class:`ColumnReport` covers:

    * **name** — column label.
    * **dtype** — pandas dtype string.
    * **missing_count** — missing values in that column.
    * **missing_proportion** — ``missing_count / row_count``; ``0.0``
      when ``row_count`` is zero.
    * **stats** — descriptive statistics; see :class:`ColumnReport` for
      the full key table by dtype.

    **Supported dtypes and their stat keys:**

    +---------------------------------+-------------------------------------------+
    | Dtype family                    | Stat keys                                 |
    +=================================+===========================================+
    | Numeric (``int*``, ``float*``,  | ``count``, ``mean``, ``std``, ``min``,   |
    | ``uint*``, ``boolean``,         | ``p25``, ``p50``, ``p75``, ``max``        |
    | nullable ``Int*`` / ``Float*``) |                                           |
    +---------------------------------+-------------------------------------------+
    | String / object / category      | ``count``, ``unique``, ``top``,           |
    |                                 | ``top_freq``                              |
    +---------------------------------+-------------------------------------------+
    | Datetime (``datetime64``,       | ``count``, ``min``, ``max``               |
    | ``DatetimeTZDtype``)            |                                           |
    +---------------------------------+-------------------------------------------+
    | Other                           | ``{}`` (empty dict)                       |
    +---------------------------------+-------------------------------------------+

    **Missing-value semantics:** ``NaN``, ``None``, and ``pd.NA`` are all
    counted as missing via ``pandas.Series.isna()``.

    **Duplicate-row semantics:** a row is counted as a duplicate when it
    is an exact match (all column values equal) of a row that appeared
    earlier in the frame.  The first occurrence is never counted.

    Example:
    ```
        import bynat
        import pandas as pd

        df = pd.DataFrame({
            "age":  [25, 30, None, 30],
            "city": ["Cairo", "London", "Cairo", "London"],
        })

        report = df.bynat.profile()

        print(report.row_count)                   # 4
        print(report.duplicate_row_count)         # 1
        print(report.columns[0].missing_count)    # 1  (age)
        print(report.columns[0].stats["mean"])    # 28.333...
        print(report.columns[1].stats["top"])     # "Cairo" or "London"
    ```
    """
    row_count = len(df)
    column_count = len(df.columns)

    # --- missing values ---
    # isna() works on all extension dtypes (pd.NA, NaT, NaN, None)
    missing_per_column: pd.Series = df.isna().sum()
    total_missing = int(missing_per_column.sum())
    cell_count = row_count * column_count
    overall_missing_proportion = total_missing / cell_count if cell_count > 0 else 0.0

    # --- duplicate rows ---
    # duplicated(keep='first'): marks every occurrence after the first as True
    duplicate_row_count = int(df.duplicated(keep="first").sum())

    # --- per-column reports ---
    column_reports: list[ColumnReport] = []
    for col in df.columns:
        series = df[col]
        missing_count = int(missing_per_column[col])
        missing_proportion = missing_count / row_count if row_count > 0 else 0.0
        col_stats = _column_stats(series)

        column_reports.append(
            ColumnReport(
                name=col,
                dtype=str(series.dtype),
                missing_count=missing_count,
                missing_proportion=missing_proportion,
                stats=col_stats,
            )
        )

    return DataQualityReport(
        row_count=row_count,
        column_count=column_count,
        total_missing=total_missing,
        overall_missing_proportion=overall_missing_proportion,
        duplicate_row_count=duplicate_row_count,
        columns=tuple(column_reports),
    )
