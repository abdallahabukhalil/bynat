"""
Deprecated `pandas.DataFrame` subclasses kept for one minor version.

`BaseData`, `JsonFile`, and `CsvFile` are thin wrappers over the functions in
the `columns`, `text`, `stats`, `enrich`, and `io` modules, which the
`df.bynat` accessor shares. New code should use plain DataFrames with the
accessor, and `bynat.read_json` / `bynat.read_csv` for loading.
"""

import functools
import os
from collections.abc import Callable, Iterable
from typing import Any, Self

import numpy as np
import pandas as pd

from bynat.caching import CacheManager

from . import columns, io, stats, text
from ._common import (  # noqa: F401  (re-exported for `bynat.cleaning.Cleaning` imports)
    _HIERARCHICAL_STATS_COLUMNS,
    _cache_has_value,
    _cache_lookup_value,
    _cache_value_key,
    _constructor_accepts_data,
    _convert_default_output,
    _convert_output,
    _dedupe_key,
    _import_optional,
    _left_merge_aligned,
    _legacy_construction_quiet,
    _quiet_legacy_construction,
    _require_positive_int,
    _validate_output_type,
    _warn_deprecated,
    logger,
)
from .enrich import currency, geocode, nlp, translate
from .enrich.nlp import _nlp_cache_bucket, _normalize_nlp_disable  # noqa: F401
from .enrich.providers import Geocoder, RateSource, Translator

__all__ = ['BaseData', 'JsonFile', 'CsvFile']

_INPLACE_DEPRECATION = (
    "inplace=True is deprecated and will be removed in the next minor release; "
    "use the returned dataframe instead."
)

_INPLACE_NOTE = """
    Deprecated:
        `inplace=True` still modifies the object and returns None, but emits
        a `DeprecationWarning` and will be removed in the next minor release.
        Without it, a new object is always returned, including when the
        method has nothing to change.
"""


def _shares_doc(func: Callable, inplace: bool = True) -> Callable[[Callable], Callable]:
    """Reuse a shared implementation's docstring on a `BaseData` method."""
    def decorate(method: Callable) -> Callable:
        method.__doc__ = (func.__doc__ or "").rstrip() + "\n" + (_INPLACE_NOTE if inplace else "")
        return method

    return decorate


def _construct_quietly(cls: type, *args: Any, **kwargs: Any) -> Any:
    """Construct a legacy class for pandas internals without a deprecation warning."""
    with _quiet_legacy_construction():
        return cls(*args, **kwargs)


class BaseData(pd.DataFrame):
    """
    Extend `pandas.DataFrame` with project-specific cleaning, translation,
    enrichment, and export helpers.

    Deprecated:
        Use a plain `pandas.DataFrame` with the `df.bynat` accessor instead.
        `BaseData` will be removed in the next minor release.

    Notes:
        Instances keep a `CacheManager` in metadata so cached helper methods can
        reuse translation, geocoding, and currency results across runs. It is
        created on first use and shared with the frames pandas derives from
        this one, so pandas operations do not build new managers.
    """

    _metadata = ['cache_manager']

    @property
    def _constructor(self) -> Callable[..., Self]:
        """
        Preserve the custom dataframe subclass during pandas operations.

        Returns:
            Callable: Builds the current subclass, so chained operations keep
            producing `BaseData`-derived objects, without the deprecation
            warning meant for direct construction.
        """
        return functools.partial(_construct_quietly, type(self))


    def __init__(self, data = None, cache_dir: str | os.PathLike | None = None, *args, **kwargs) -> None:
        """
        Initialize the custom dataframe from in-memory tabular data.

        Args:
            data: Optional tabular data passed directly to `pandas.DataFrame`.
            cache_dir: Directory for caching data. When None, uses the
                `BYNAT_CACHE_DIR` environment variable if set, otherwise
                "Settings/Cache Data/Automated".
            *args: Additional positional arguments forwarded to
                `pandas.DataFrame`.
            **kwargs: Additional keyword arguments forwarded to
                `pandas.DataFrame`.

        Returns:
            None

        Notes:
            File-specific loading is implemented by `JsonFile` and `CsvFile`.
        """
        if not _legacy_construction_quiet.get():
            _warn_deprecated(
                f"{type(self).__name__} is deprecated and will be removed in the next minor "
                "release; use a pandas DataFrame with the `df.bynat` accessor instead."
            )

        object.__setattr__(self, '_bynat_cache_dir', cache_dir)
        object.__setattr__(self, '_bynat_cache_manager', None)

        super().__init__(data = data, *args, **kwargs)


    @property
    def cache_manager(self) -> CacheManager:
        """The `CacheManager` used by cached helper methods, created on first use."""
        manager = self.__dict__.get('_bynat_cache_manager')

        if manager is None:
            manager = CacheManager(cache_dir = self.__dict__.get('_bynat_cache_dir'))
            object.__setattr__(self, '_bynat_cache_manager', manager)

        return manager


    @cache_manager.setter
    def cache_manager(self, manager: CacheManager | None) -> None:
        object.__setattr__(self, '_bynat_cache_manager', manager)


    def _finish(self, result: pd.DataFrame, inplace: bool) -> Self | None:
        """Return `result`, or copy it into this object for deprecated `inplace=True`."""
        if not inplace:
            return result

        _warn_deprecated(_INPLACE_DEPRECATION, stacklevel = 4)
        self._update_inplace(result)
        return None


    def _finish_with_stats(self, outcome: Any, inplace: bool, with_stats: bool) -> Any:
        """`_finish` for methods that can also return hierarchical statistics."""
        result, hierarchical_stats = outcome if with_stats else (outcome, None)

        if not inplace:
            return (result, hierarchical_stats) if with_stats else result

        _warn_deprecated(_INPLACE_DEPRECATION, stacklevel = 4)
        self._update_inplace(result)
        return hierarchical_stats


    def clear_cache_for_column(self, func_name: str, target_col: str):
        """
        Delete a cached column bucket for one of the helper methods.

        Args:
            func_name (str): Name of the cached helper function.
            target_col (str): Nested cache key to remove, usually a column name.

        Returns:
            None
        """
        self.cache_manager.delete_column_cache(func_name, target_col)


    _cache_value_key = staticmethod(_cache_value_key)
    _cache_has_value = staticmethod(_cache_has_value)
    _cache_lookup_value = staticmethod(_cache_lookup_value)
    _require_positive_int = staticmethod(_require_positive_int)


    def view(self) -> pd.DataFrame:
        """
        Return the object as a standard pandas DataFrame for inspection and
        rich display.

        Returns:
            pandas.DataFrame: A DataFrame representation of the current object.

        Example:
        ```
            Usage:
                # Viewing a slice of the custom object as a dynamic table
                search_dataframe.iloc[0:100].view()

                # Using pandas-specific plotting on the view
                search_dataframe.view().plot(kind='bar')
        ```
        """

        return pd.DataFrame(self)


    @_shares_doc(columns.drop_by_substring)
    def drop_by_substring(self, substring: str, inplace: bool = False) -> Self | None:
        return self._finish(columns.drop_by_substring(self, substring), inplace)


    @_shares_doc(columns.drop_CLASS_columns)
    def drop_CLASS_columns(self, inplace: bool = False) -> Self | None:
        return self._finish(columns.drop_CLASS_columns(self), inplace)


    @_shares_doc(columns.rename_by_substring)
    def rename_by_substring(self, old_substring: str, new_substring: str, inplace: bool = False) -> Self | None:
        return self._finish(columns.rename_by_substring(self, old_substring, new_substring), inplace)


    @_shares_doc(columns.rename_all_DOTS)
    def rename_all_DOTS(self, unified_separator: str = "_", inplace: bool = False) -> Self | None:
        return self._finish(columns.rename_all_DOTS(self, unified_separator), inplace)


    @_shares_doc(columns.reorder_columns)
    def reorder_columns(self, *first_cols, inplace: bool = False) -> Self | None:
        return self._finish(columns.reorder_columns(self, *first_cols), inplace)


    @_shares_doc(columns.melt_transform_pivot)
    def melt_transform_pivot(
        self,
        id_vars: list | str,
        value_vars: list | str | None = None,
        transform_func: Callable | None = None,
        aggfunc: str | Callable = 'first',
        var_name: str = 'variable_name',
        value_name: str = 'variable_value',
        pivot_index: list | str | None = None,
        pivot_columns: list | str | None = None,
        inplace: bool = False,
        **kwargs
    ) -> Self | None:
        result = columns.melt_transform_pivot(
            self,
            id_vars,
            value_vars = value_vars,
            transform_func = transform_func,
            aggfunc = aggfunc,
            var_name = var_name,
            value_name = value_name,
            pivot_index = pivot_index,
            pivot_columns = pivot_columns,
            **kwargs
        )
        return self._finish(result, inplace)


    @_shares_doc(columns.move_column)
    def move_column(self, col_to_move: str, col_target: str, position: str = "after", inplace: bool = False) -> Self | None:
        return self._finish(columns.move_column(self, col_to_move, col_target, position), inplace)


    @_shares_doc(columns.align_lat_lng)
    def align_lat_lng(self, inplace: bool = False) -> Self | None:
        return self._finish(columns.align_lat_lng(self), inplace)


    @_shares_doc(text.extract_keywords)
    def extract_keywords(
            self,
            *source_cols,
            inplace: bool = False,
            remove_extracted: bool = False,
            use_regex: bool = False,
            **target_cols_and_keywords
    ) -> Self | None:
        result = text.extract_keywords(
            self,
            *source_cols,
            remove_extracted = remove_extracted,
            use_regex = use_regex,
            **target_cols_and_keywords
        )
        return self._finish(result, inplace)


    @_shares_doc(columns.split_list_objects)
    def split_list_objects(
            self,
            source_col: str,
            remove_source_col: bool = False,
            explode_rows: bool = False,
            cartesian_explode: bool = False,
            inplace: bool = False,
            **target_cols_and_indices
    ) -> Self | None:
        result = columns.split_list_objects(
            self,
            source_col,
            remove_source_col = remove_source_col,
            explode_rows = explode_rows,
            cartesian_explode = cartesian_explode,
            **target_cols_and_indices
        )
        return self._finish(result, inplace)


    @_shares_doc(translate.impute_by_language)
    def impute_by_language(
            self,
            reference_col: str,
            *target_cols,
            target_lang: str = 'en',
            inplace: bool = False
    ) -> Self | None:
        result = translate.impute_by_language(self, reference_col, *target_cols, target_lang = target_lang)
        return self._finish(result, inplace)


    @_shares_doc(translate.translate_conditional_column)
    def translate_conditional_column(
        self,
        partition_col: str,
        detect_col: str,
        target_col: str,
        target_lang: str = 'en',
        sample_size: int = 10,
        number_of_samples: int = 3,
        threshold: float = 0.5,
        save_every: int = 1000,
        inplace: bool = False,
        random_state: int | np.random.Generator | np.random.RandomState | None = 0,
        provider: Translator | None = None,
    ) -> Self | None:
        result = translate.translate_conditional_column(
            self,
            partition_col,
            detect_col,
            target_col,
            target_lang = target_lang,
            sample_size = sample_size,
            number_of_samples = number_of_samples,
            threshold = threshold,
            save_every = save_every,
            random_state = random_state,
            provider = provider,
            cache_manager = self.cache_manager,
        )
        return self._finish(result, inplace)


    @_shares_doc(translate.translate_categorical_column)
    def translate_categorical_column(
            self,
            target_col: str,
            target_lang: str = 'en',
            save_every: int = 1000,
            inplace: bool = False,
            provider: Translator | None = None,
    ) -> Self | None:
        result = translate.translate_categorical_column(
            self,
            target_col,
            target_lang = target_lang,
            save_every = save_every,
            provider = provider,
            cache_manager = self.cache_manager,
        )
        return self._finish(result, inplace)


    @_shares_doc(text.truncate_after_substring)
    def truncate_after_substring(self, inplace: bool = False, regex: bool = True, **target_cols_and_substrings) -> Self | None:
        return self._finish(text.truncate_after_substring(self, regex = regex, **target_cols_and_substrings), inplace)


    @_shares_doc(geocode.fill_missing_coordinates)
    def fill_missing_coordinates(
        self,
        *fallback_cols: str,
        lat_col: str,
        long_col: str,
        save_every: int = 1000,
        user_agent: str = "data_cleaning_core_geocoder",
        retry_not_found: bool = False,
        inplace: bool = False,
        provider: Geocoder | None = None,
    ) -> Self | None:
        result = geocode.fill_missing_coordinates(
            self,
            *fallback_cols,
            lat_col = lat_col,
            long_col = long_col,
            save_every = save_every,
            user_agent = user_agent,
            retry_not_found = retry_not_found,
            provider = provider,
            cache_manager = self.cache_manager,
        )
        return self._finish(result, inplace)


    @_shares_doc(currency.convert_to_usd)
    def convert_to_usd(
        self,
        *target_cols,
        country_col: str,
        year_col: str,
        new_cols: bool = True,
        round_decimals: int | None = None,
        inplace: bool = False,
        provider: RateSource | None = None,
    ) -> Self | None:
        result = currency.convert_to_usd(
            self,
            *target_cols,
            country_col = country_col,
            year_col = year_col,
            new_cols = new_cols,
            round_decimals = round_decimals,
            provider = provider,
            cache_manager = self.cache_manager,
        )
        return self._finish(result, inplace)


    @_shares_doc(stats.handle_outliers)
    def handle_outliers(
        self,
        *target_cols,
        hierarchy_cols: list,
        min_samples: int = 5,
        inplace: bool = False,
        get_hierarchical_stats: bool = False
    ) -> Self | tuple[Self, Self] | None:
        outcome = stats.handle_outliers(
            self,
            *target_cols,
            hierarchy_cols = hierarchy_cols,
            min_samples = min_samples,
            get_hierarchical_stats = get_hierarchical_stats,
        )
        return self._finish_with_stats(outcome, inplace, get_hierarchical_stats)


    @_shares_doc(stats.drop_outliers)
    def drop_outliers(
        self,
        *target_cols,
        hierarchy_cols: list,
        min_samples: int = 5,
        inplace: bool = False,
        get_hierarchical_stats: bool = False
    ) -> Self | tuple[Self, Self] | None:
        outcome = stats.drop_outliers(
            self,
            *target_cols,
            hierarchy_cols = hierarchy_cols,
            min_samples = min_samples,
            get_hierarchical_stats = get_hierarchical_stats,
        )
        return self._finish_with_stats(outcome, inplace, get_hierarchical_stats)


    @_shares_doc(stats.fill_missing)
    def fill_missing(
        self,
        *target_cols,
        strategy: str = 'median',
        hierarchy_cols: list | str | None = None,
        inplace: bool = False,
        get_hierarchical_stats: bool = False
    ) -> Self | tuple[Self, Self] | None:
        outcome = stats.fill_missing(
            self,
            *target_cols,
            strategy = strategy,
            hierarchy_cols = hierarchy_cols,
            get_hierarchical_stats = get_hierarchical_stats,
        )
        return self._finish_with_stats(outcome, inplace, get_hierarchical_stats)


    @_shares_doc(stats.get_hierarchical_stats, inplace = False)
    def get_hierarchical_stats(
        self,
        *target_cols,
        strategy: str | Iterable[str] = 'median',
        hierarchy_cols: list | str | None = None,
        output_type = None,
    ):
        return stats.get_hierarchical_stats(
            self,
            *target_cols,
            strategy = strategy,
            hierarchy_cols = hierarchy_cols,
            output_type = output_type,
        )


    @_shares_doc(text.categorize_by_reference)
    def categorize_by_reference(
        self,
        *source_cols: str,
        target_col: str,
        reference: dict,
        default_category: str = 'Other',
        inplace: bool = False
    ) -> Self | None:
        result = text.categorize_by_reference(
            self,
            *source_cols,
            target_col = target_col,
            reference = reference,
            default_category = default_category,
        )
        return self._finish(result, inplace)


    @_shares_doc(nlp.extract_entities_nlp)
    def extract_entities_nlp(
        self,
        *source_cols,
        lexicon_source: str | os.PathLike | dict,
        spacy_model: str = "en_core_web_sm",
        exclude_pos: set | list | tuple | str | None = None,
        disable: Iterable[str] | str | None = ('parser', 'ner'),
        save_every: int = 1000,
        inplace: bool = False,
        noisy_terms: set | None = None,
        **target_cols_and_json_keys
    ) -> Self | None:
        result = nlp.extract_entities_nlp(
            self,
            *source_cols,
            lexicon_source = lexicon_source,
            spacy_model = spacy_model,
            exclude_pos = exclude_pos,
            disable = disable,
            save_every = save_every,
            noisy_terms = noisy_terms,
            cache_manager = self.cache_manager,
            **target_cols_and_json_keys
        )
        return self._finish(result, inplace)


    @_shares_doc(io.save_to_json, inplace = False)
    def save_to_json(
            self,
            file_path: str | os.PathLike,
            orient: str = 'records',
            append: bool = False,
            force_ascii: bool = False,
            indent: int = 4
    ) -> None:
        return io.save_to_json(self, file_path, orient = orient, append = append, force_ascii = force_ascii, indent = indent)


    @_shares_doc(io.save_to_sql, inplace = False)
    def save_to_sql(
            self,
            table_name: str,
            database_type: str,
            database_user: str,
            database_password: str,
            database_host: str,
            database_port: str,
            database_name: str,
            database_driver: str | None = None,
            columns_types: dict | None = None,
            chunk_size: int | None = None,
            fast_executemany: bool = False,
            if_exists: str = 'append',
            index: bool = False
    ) -> None:
        return io.save_to_sql(
            self,
            table_name,
            database_type,
            database_user,
            database_password,
            database_host,
            database_port,
            database_name,
            database_driver = database_driver,
            columns_types = columns_types,
            chunk_size = chunk_size,
            fast_executemany = fast_executemany,
            if_exists = if_exists,
            index = index,
        )



class JsonFile(BaseData):
    """
    Load a JSON file into a `BaseData` frame.

    Deprecated:
        Use `bynat.read_json(...)` and the `df.bynat` accessor instead.
        `JsonFile` will be removed in the next minor release.
    """

    def __init__(
        self,
        data = None,
        json_file_path: str | os.PathLike | None = None,
        key: str | None = None,
        cache_dir: str | os.PathLike | None = None,
        *args,
        **kwargs
    ):
        if not _legacy_construction_quiet.get():
            _warn_deprecated(
                "JsonFile is deprecated and will be removed in the next minor release; "
                "use bynat.read_json() with the `df.bynat` accessor instead."
            )

        if data is not None and json_file_path is None:
            if isinstance(data, (str, os.PathLike)):
                path = os.fspath(data)

                if os.path.isfile(path):
                    json_file_path = path
                    data = None

                else:
                    raise FileNotFoundError(f"JSON file not found: {path}")

        if data is None and json_file_path is not None:
            data = io.read_json(json_file_path, key = key)

        with _quiet_legacy_construction():
            super().__init__(data = data, cache_dir = cache_dir, *args, **kwargs)


class CsvFile(BaseData):
    """
    Load a CSV file into a `BaseData` frame.

    Deprecated:
        Use `bynat.read_csv(...)` and the `df.bynat` accessor instead.
        `CsvFile` will be removed in the next minor release.
    """

    def __init__(
        self,
        data = None,
        csv_file_path: str | os.PathLike | None = None,
        cache_dir: str | os.PathLike | None = None,
        read_kwargs: dict | None = None,
        *args,
        **kwargs
    ):
        if not _legacy_construction_quiet.get():
            _warn_deprecated(
                "CsvFile is deprecated and will be removed in the next minor release; "
                "use bynat.read_csv() with the `df.bynat` accessor instead."
            )

        if read_kwargs is None:
            read_kwargs = {}

        if data is not None and csv_file_path is None:
            if isinstance(data, (str, os.PathLike)):
                path = os.fspath(data)

                if os.path.isfile(path):
                    csv_file_path = path
                    data = None

                else:
                    raise FileNotFoundError(f"CSV file not found: {path}")

        if data is None and csv_file_path is not None:
            data = io.read_csv(csv_file_path, **read_kwargs)

        with _quiet_legacy_construction():
            super().__init__(data = data, cache_dir = cache_dir, *args, **kwargs)
