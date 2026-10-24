"""Private helpers shared by the cleaning modules, `BaseData`, and the accessor."""

import contextlib
import contextvars
import importlib
import inspect
import json
import logging
import warnings
from collections.abc import Iterator
from types import ModuleType
from typing import Any, TypeVar, cast

import numpy as np
import pandas as pd

# Keep the pre-0.4 logger name so existing logging configuration still applies.
logger = logging.getLogger("bynat.cleaning.Cleaning")

FrameT = TypeVar("FrameT", bound = pd.DataFrame)

_HIERARCHICAL_STATS_COLUMNS = (
    'hierarchy_level',
    'hierarchy_columns',
    'group_key',
    'target_column',
    'strategy',
    'statistic',
)

# Set while bynat builds legacy wrapper objects itself, so only callers that
# construct `BaseData`, `JsonFile`, or `CsvFile` directly see the deprecation.
_legacy_construction_quiet: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "bynat_legacy_construction_quiet", default = False
)


@contextlib.contextmanager
def _quiet_legacy_construction() -> Iterator[None]:
    """Suppress legacy-class deprecation warnings for internal construction."""
    token = _legacy_construction_quiet.set(True)
    try:
        yield
    finally:
        _legacy_construction_quiet.reset(token)


def _warn_deprecated(message: str, stacklevel: int = 3) -> None:
    """Emit a `DeprecationWarning` attributed to the caller's caller."""
    warnings.warn(message, DeprecationWarning, stacklevel = stacklevel)


def _import_optional(module_name: str, distribution: str, feature: str) -> ModuleType:
    """Import a feature dependency only when the feature is used."""
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise ImportError(
            f"{feature} requires the optional '{distribution}' dependency."
        ) from exc


def _require_positive_int(value: Any, arg_name: str) -> None:
    """
    Validate integer parameters that control sampling or cache checkpointing.

    Raises:
        ValueError: If `value` is not a positive integer.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"The '{arg_name}' parameter must be a positive integer")


def _cache_value_key(value: Any) -> str:
    """Convert a cache lookup value into a stable JSON object key."""
    if isinstance(value, (int, np.integer)):
        return str(int(value))

    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))

    return str(value)


def _cache_has_value(cache: dict, value: Any) -> bool:
    """
    Check whether a cache contains a value under either the normalized JSON
    key or the raw in-memory key.
    """
    if _cache_value_key(value) in cache:
        return True

    try:
        return value in cache

    except TypeError:
        return False


def _cache_lookup_value(cache: dict, value: Any) -> Any:
    """
    Return a cached value using normalized JSON keys with a fallback for
    older raw in-memory cache keys, or the original value when absent.
    """
    normalized_key = _cache_value_key(value)

    if normalized_key in cache:
        return cache[normalized_key]

    try:
        return cache[value]

    except (KeyError, TypeError):
        return value


def _left_merge_aligned(frame: pd.DataFrame, right: pd.DataFrame, on: list) -> pd.DataFrame:
    """
    Left-merge `right` onto the `on` columns of `frame` and return the result
    indexed like `frame`.

    A left merge preserves the row order of the left frame, so the merged rows
    are re-labelled positionally. This works for named, duplicate, and
    multi-level indexes, unlike round-tripping through `reset_index()`.
    """
    merged = frame[on].merge(right, on = on, how = 'left')
    merged.index = frame.index
    return merged


def _dedupe_key(value: Any) -> Any:
    """Return a hashable stand-in for unhashable values such as lists and dicts."""
    if isinstance(value, set):
        value = sorted(value, key = str)
    elif isinstance(value, np.ndarray):
        value = value.tolist()

    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, sort_keys = True, default = str)

    return value


def _validate_output_type(output_type: Any) -> None:
    """Validate the generic tabular output types supported by this package."""
    if output_type is None or output_type is dict or output_type is pd.DataFrame:
        return

    # Imported lazily: `base` imports this module.
    from bynat.cleaning.base import BaseData

    if isinstance(output_type, type) and issubclass(output_type, BaseData):
        return

    raise TypeError(
        "output_type must be None, pandas.DataFrame, dict, or a "
        "BaseData subclass."
    )


def _convert_output(data: pd.DataFrame, output_type: Any) -> Any:
    """Convert a dataframe to a supported output type without schema coupling."""
    if output_type is None or output_type is pd.DataFrame:
        return data

    if output_type is dict:
        return {column: data[column].tolist() for column in data.columns}

    with _quiet_legacy_construction():
        return output_type(data = data)


def _constructor_accepts_data(output_type: Any) -> bool:
    """Return whether a constructor signature can bind the keyword ``data``."""
    try:
        inspect.signature(output_type).bind(data = pd.DataFrame())
    except (TypeError, ValueError):
        return False
    return True


def _convert_default_output(data: pd.DataFrame, output_type: Any) -> Any:
    """Use a custom default constructor only when its signature accepts data."""
    if output_type is pd.DataFrame or not _constructor_accepts_data(output_type):
        return data
    return _convert_output(data, output_type)


def _like(df: FrameT, data: pd.DataFrame) -> FrameT:
    """Return `data` as `df`'s frame type, carrying `df`'s metadata."""
    if type(df) is pd.DataFrame:
        return cast(FrameT, data)

    # pandas-stubs types `_constructor` as an attribute, not a callable.
    constructor: Any = df._constructor
    return cast(FrameT, constructor(data).__finalize__(df))
