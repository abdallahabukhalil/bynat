"""The `df.bynat` pandas accessor."""

import functools
import inspect
import os
from collections.abc import Callable
from typing import Any

import pandas as pd

from bynat.caching import CacheManager, shared_cache_manager

from . import columns, io, profile, stats, text
from .enrich import currency, geocode, nlp, translate

__all__ = ['BynatAccessor']


def _method_signature(func: Callable) -> inspect.Signature:
    """The accessor-facing signature: `self` replaces the frame, no `cache_manager`."""
    signature = inspect.signature(func)
    parameters = [
        parameter
        for parameter in list(signature.parameters.values())[1:]
        if parameter.name != 'cache_manager'
    ]
    self_parameter = inspect.Parameter('self', inspect.Parameter.POSITIONAL_OR_KEYWORD)
    return signature.replace(parameters = [self_parameter, *parameters])


def _frame_method(func: Callable) -> Callable:
    """Expose a shared implementation as an accessor method on the wrapped frame."""
    @functools.wraps(func)
    def method(self: "BynatAccessor", *args: Any, **kwargs: Any) -> Any:
        return func(self._obj, *args, **kwargs)

    method.__signature__ = _method_signature(func)  # type: ignore[attr-defined]
    return method


def _cached_frame_method(func: Callable) -> Callable:
    """Like `_frame_method`, also passing the accessor's `CacheManager`."""
    @functools.wraps(func)
    def method(self: "BynatAccessor", *args: Any, **kwargs: Any) -> Any:
        return func(self._obj, *args, cache_manager = self.cache_manager, **kwargs)

    method.__signature__ = _method_signature(func)  # type: ignore[attr-defined]
    return method


@pd.api.extensions.register_dataframe_accessor("bynat")
class BynatAccessor:
    """
    bynat cleaning, enrichment, and export methods for any `pandas.DataFrame`.

    Every transformation returns a new DataFrame and never modifies the
    original. Cached methods (translation, geocoding, currency conversion,
    and NLP) use one shared `CacheManager` per cache directory: the
    `BYNAT_CACHE_DIR` environment variable or the default directory, unless
    `with_cache(...)` selects another one.

    Example:
    ```
        import bynat
        import pandas as pd

        df = pd.DataFrame({"user.name": ["Ada"], "status": ["مكتمل"]})

        df = df.bynat.rename_all_DOTS()
        df = df.bynat.with_cache("cache").translate_categorical_column("status")
    ```
    """

    def __init__(self, pandas_obj: pd.DataFrame, cache_manager: CacheManager | None = None) -> None:
        self._obj = pandas_obj
        self._cache_manager = cache_manager

    @property
    def cache_manager(self) -> CacheManager:
        """The `CacheManager` used by cached methods."""
        if self._cache_manager is None:
            self._cache_manager = shared_cache_manager()

        return self._cache_manager

    def with_cache(self, cache: str | os.PathLike | CacheManager | None) -> "BynatAccessor":
        """
        Return an accessor for the same frame that caches in another place.

        Args:
            cache (str | os.PathLike | CacheManager | None): A cache directory
                (resolved to its shared manager), an explicit manager, or None
                for the default directory.

        Returns:
            BynatAccessor: An accessor bound to that cache.
        """
        manager = cache if isinstance(cache, CacheManager) else shared_cache_manager(cache)
        return BynatAccessor(self._obj, cache_manager = manager)

    def clear_cache_for_column(self, func_name: str, target_col: str) -> None:
        """
        Delete a cached column bucket for one of the cached methods.

        Args:
            func_name (str): Name of the cached method.
            target_col (str): Nested cache key to remove, usually a column name.
        """
        self.cache_manager.delete_column_cache(func_name, target_col)

    drop_by_substring = _frame_method(columns.drop_by_substring)
    drop_CLASS_columns = _frame_method(columns.drop_CLASS_columns)
    rename_by_substring = _frame_method(columns.rename_by_substring)
    rename_all_DOTS = _frame_method(columns.rename_all_DOTS)
    reorder_columns = _frame_method(columns.reorder_columns)
    melt_transform_pivot = _frame_method(columns.melt_transform_pivot)
    move_column = _frame_method(columns.move_column)
    align_lat_lng = _frame_method(columns.align_lat_lng)
    split_list_objects = _frame_method(columns.split_list_objects)

    extract_keywords = _frame_method(text.extract_keywords)
    truncate_after_substring = _frame_method(text.truncate_after_substring)
    categorize_by_reference = _frame_method(text.categorize_by_reference)

    handle_outliers = _frame_method(stats.handle_outliers)
    drop_outliers = _frame_method(stats.drop_outliers)
    fill_missing = _frame_method(stats.fill_missing)
    get_hierarchical_stats = _frame_method(stats.get_hierarchical_stats)
    profile = _frame_method(profile.profile)

    impute_by_language = _frame_method(translate.impute_by_language)
    translate_conditional_column = _cached_frame_method(translate.translate_conditional_column)
    translate_categorical_column = _cached_frame_method(translate.translate_categorical_column)
    fill_missing_coordinates = _cached_frame_method(geocode.fill_missing_coordinates)
    convert_to_usd = _cached_frame_method(currency.convert_to_usd)
    extract_entities_nlp = _cached_frame_method(nlp.extract_entities_nlp)

    save_to_json = _frame_method(io.save_to_json)
    save_to_sql = _frame_method(io.save_to_sql)
