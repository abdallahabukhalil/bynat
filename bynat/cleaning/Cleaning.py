import importlib
import inspect
import json
import logging
import os
import re
import time
import urllib.parse
from collections.abc import Callable, Iterable
from typing import Self

import numpy as np
import pandas as pd

from bynat.caching import CacheManager

logger = logging.getLogger(__name__)


def _import_optional(module_name: str, distribution: str, feature: str):
    """Import a feature dependency only when the feature is used."""
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise ImportError(
            f"{feature} requires the optional '{distribution}' dependency."
        ) from exc


__all__ = ['BaseData', 'JsonFile', 'CsvFile']

_HIERARCHICAL_STATS_COLUMNS = (
    'hierarchy_level',
    'hierarchy_columns',
    'group_key',
    'target_column',
    'strategy',
    'statistic',
)


def _validate_output_type(output_type) -> None:
    """Validate the generic tabular output types supported by this module."""
    if output_type is None or output_type is dict or output_type is pd.DataFrame:
        return

    if isinstance(output_type, type) and issubclass(output_type, BaseData):
        return

    raise TypeError(
        "output_type must be None, pandas.DataFrame, dict, or a "
        "BaseData subclass."
    )


def _convert_output(data: pd.DataFrame, output_type):
    """Convert a dataframe to a supported output type without schema coupling."""
    if output_type is None or output_type is pd.DataFrame:
        return data

    if output_type is dict:
        return {column: data[column].tolist() for column in data.columns}

    return output_type(data=data)


def _constructor_accepts_data(output_type) -> bool:
    """Return whether a constructor signature can bind the keyword ``data``."""
    try:
        inspect.signature(output_type).bind(data=pd.DataFrame())
    except (TypeError, ValueError):
        return False
    return True


def _convert_default_output(data: pd.DataFrame, output_type):
    """Use a custom default constructor only when its signature accepts data."""
    if output_type is pd.DataFrame or not _constructor_accepts_data(output_type):
        return data
    return _convert_output(data, output_type)


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


def _dedupe_key(value):
    """Return a hashable stand-in for unhashable values such as lists and dicts."""
    if isinstance(value, set):
        value = sorted(value, key = str)
    elif isinstance(value, np.ndarray):
        value = value.tolist()

    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, sort_keys = True, default = str)

    return value



class BaseData(pd.DataFrame):
    """
    Extend `pandas.DataFrame` with project-specific cleaning, translation,
    enrichment, and export helpers.

    Notes:
        Instances keep a `CacheManager` in metadata so cached helper methods can
        reuse translation, geocoding, and currency results across runs.
    """

    _metadata = ['cache_manager']

    @property
    def _constructor(self):
        """
        Preserve the custom dataframe subclass during pandas operations.

        Returns:
            type: The current subclass type so chained operations continue to
            produce `BaseData`-derived objects instead of plain dataframes.
        """
        return type(self)


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

        self.cache_manager = CacheManager(cache_dir = cache_dir)

        super().__init__(data = data, *args, **kwargs)


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


    @staticmethod
    def _cache_value_key(value) -> str:
        """
        Convert a cache lookup value into a stable JSON object key.

        Args:
            value: Raw value from a dataframe column.

        Returns:
            str: String key that remains stable after JSON save/load cycles.
        """
        if isinstance(value, (int, np.integer)):
            return str(int(value))

        if isinstance(value, (float, np.floating)) and float(value).is_integer():
            return str(int(value))

        return str(value)


    @classmethod
    def _cache_has_value(cls, cache: dict, value) -> bool:
        """
        Check whether a cache contains a value under either the normalized JSON
        key or the raw in-memory key.

        Args:
            cache (dict): Cache bucket to inspect.
            value: Raw dataframe value to check.

        Returns:
            bool: True when the value already has a cached entry.
        """
        normalized_key = cls._cache_value_key(value)

        if normalized_key in cache:
            return True

        try:
            return value in cache

        except TypeError:
            return False


    @classmethod
    def _cache_lookup_value(cls, cache: dict, value):
        """
        Return a cached value using normalized JSON keys with a fallback for
        older raw in-memory cache keys.

        Args:
            cache (dict): Cache bucket to read from.
            value: Raw dataframe value to resolve.

        Returns:
            The cached value when present, otherwise the original value.
        """
        normalized_key = cls._cache_value_key(value)

        if normalized_key in cache:
            return cache[normalized_key]

        try:
            return cache[value]

        except (KeyError, TypeError):
            return value


    @staticmethod
    def _require_positive_int(value, arg_name: str) -> None:
        """
        Validate integer parameters that control sampling or cache checkpointing.

        Args:
            value: Argument value to validate.
            arg_name (str): Argument name for the error message.

        Returns:
            None

        Raises:
            ValueError: If `value` is not a positive integer.
        """
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"The '{arg_name}' parameter must be a positive integer")


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


    def drop_by_substring(self, substring: str, inplace: bool = False) -> (Self | None):
        """
        Drop all columns that contain a specific substring within their names.

        Args:
            substring (str): The text to search for; any column containing this
                string will be removed.
            inplace (bool): If True, modifies the object directly and returns None.
                Defaults to False.

        Returns:
            The modified object (self) if `inplace` is False, otherwise None.

        Example:
        ```
            Column Removal:
                'user_id'         ->  (dropped)
                'user_metadata'   ->  (dropped)
                'created_at'      ->  'created_at' (kept)

            Usage:
                df.drop_by_substring("user")
        ```
        """

        columns_to_drop = [col for col in self.columns if substring in col]

        if not columns_to_drop:
            return self if not inplace else None

        return self.drop(columns = columns_to_drop, inplace = inplace)


    def drop_CLASS_columns(self, inplace: bool = False) -> (Self | None):
        """
        Drop all columns that contain the substring "__CLASS__" from the data.

        Args:
            inplace (bool): If True, modifies the object directly and returns None.
                Defaults to False.

        Returns:
            The modified object (self) if `inplace` is False, otherwise None.

        Example:
        ```
            Column Removal:
                'metadata.__CLASS__'  ->  (dropped)
                'object.__CLASS__id'  ->  (dropped)
                'user_name'           ->  'user_name' (kept)

            Usage:
                df.drop_CLASS_columns()
        ```
        """

        return self.drop_by_substring("__CLASS__", inplace = inplace)


    def rename_by_substring(self, old_substring: str, new_substring: str, inplace: bool = False) -> (Self | None):
        """
        Rename columns by replacing a specific substring with a new one.

        Args:
            old_substring (str): The text to search for within the column names.
            new_substring (str): The text to replace the old substring with.
            inplace (bool): If True, modifies the object directly and returns None.
                Defaults to False.

        Returns:
            The modified object (self) if `inplace` is False, otherwise None.

        Example:
        ```
            Column Transformation:
                'user_display_name'  ->  'user_name'
                'post_display_name'  ->  'post_name'
                'created_at'         ->  'created_at' (unchanged)

            Usage:
                df.rename_by_substring("display_name", "name")
        ```
        """

        columns_to_rename = {
            col: col.replace(old_substring, new_substring)
            for col in self.columns
            if old_substring in col
        }

        if not columns_to_rename:
            return self if not inplace else None

        return self.rename(columns = columns_to_rename, inplace = inplace)


    def rename_all_DOTS(self, unified_separator: str = "_", inplace: bool = False) -> (Self | None):
        """
        Rename columns by replacing all occurrences of dots ('.') with a unified separator.

        Args:
            unified_separator (str): The new string to replace dots with.
                Defaults to "_".
            inplace (bool): If True, modifies the object directly and returns None.
                Defaults to False.

        Returns:
            The modified object (self) if `inplace` is False, otherwise None.

        Example:
        ```
            Column Transformation:
                'location.display_name'  ->  'location_display_name'
                'user.id'                ->  'user_id'
                'status'                 ->  'status' (unchanged)

            Usage:
                df.rename_all_DOTS(unified_separator = "_")
        ```
        """

        return self.rename_by_substring(".", unified_separator, inplace = inplace)


    def reorder_columns(self, *first_cols, inplace: bool = False) -> (Self | None):
        """
        Reorder the dataframe by moving a specified list of columns to the front.

        Any columns not mentioned in `first_cols` will be appended to the end
        preserving their original relative order.

        Args:
            first_cols (tuple): A list of column names that should appear first.
            inplace (bool): If True, modifies the object directly and returns None.
                Defaults to False.

        Returns:
            The modified object (self) if `inplace` is False, otherwise None.

        Example:
        ```
            Initial Columns: ['C', 'A', 'D', 'B']

            Operation: reorder_columns(first_cols=['A', 'B'])
            Result:    ['A', 'B', 'C', 'D']

            Usage:
                df.reorder_columns(["id", "timestamp"], inplace=True)
        ```
        """

        ordered = [col for col in first_cols if col in self.columns]

        if not ordered:
            return self if not inplace else None

        final_order = ordered + [col for col in self.columns if col not in ordered]

        if inplace:
            for i, col in enumerate(final_order):
                col_data = self.pop(col)
                self.insert(i, col, col_data)

            return None

        else:
            return self[final_order]


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
    ) -> (Self | None):
        """
        Reshape selected columns to long format, transform their values, and
        pivot them back to wide format.

        Notes:
            This helper is useful when the same vectorized operation should be
            applied to several value columns through one temporary long-form
            series. Duplicate identifier/value-column pairs are resolved with
            `aggfunc`, so the output can have fewer rows than the input. When
            `inplace=True`, the current dataframe is replaced with the pivoted
            result, including any changed row count and a fresh RangeIndex.
            By default, the method pivots back using `id_vars` as rows and the
            melted variable column (`var_name`) as output columns. Use
            `pivot_index` and `pivot_columns` only when you want a different
            pivot layout from the melted dataframe.

        Args:
            id_vars (list | str): Column(s) to use as identifier variables.
            value_vars (list | str | None): Column(s) to unpivot. If None, uses all columns
                that are not set as `id_vars`.
            transform_func (Callable | None): Function applied to the melted
                value series before pivoting back. The function should accept a
                pandas Series and return a same-length array-like or Series.
                Defaults to None.
            aggfunc (str | callable): Aggregation function to use when pivoting back
                in case of duplicate identifiers. Defaults to 'first'.
            var_name (str): Name to use for the 'variable' column. Defaults to 'variable_name'.
            value_name (str): Name to use for the 'value' column. Defaults to 'variable_value'.
            pivot_index (list | str | None): Column(s) from the melted dataframe
                to use as pivot index. Allowed columns are `id_vars` and
                `var_name`. Defaults to `id_vars`, unless `pivot_columns` is
                provided, then it uses the remaining `id_vars` plus `var_name`.
            pivot_columns (list | str | None): Column(s) from the melted
                dataframe to use as pivot columns. Allowed columns are `id_vars`
                and `var_name`. Defaults to `var_name`.
            inplace (bool): If True, modifies the object directly. Defaults to False.
            **kwargs: Additional keyword arguments to pass to `transform_func`.

        Returns:
            Self | None: The reshaped dataframe when `inplace=False`, otherwise None.

        Raises:
            TypeError: If a column argument has an invalid type, if `aggfunc`
                is invalid, or if `transform_func` is provided but is not
                callable.
            ValueError: If `id_vars`, `value_vars`, `var_name`, or
                `value_name` is invalid, if identifier/value/pivot columns
                overlap incorrectly, or if `transform_func` returns invalid
                output.
            RuntimeError: If `transform_func` raises an exception.

        Example:
        ```
            # Bulk clean 3 columns simultaneously by making them uppercase
            df.melt_transform_pivot(
                id_vars="user_id",
                value_vars=["col1", "col2", "col3"],
                transform_func=lambda s: s.str.upper()
            )
        ```
        """

        if isinstance(id_vars, str):
            id_vars = [id_vars]

        elif id_vars is None:
            id_vars = []

        else:
            try:
                id_vars = list(id_vars)

            except TypeError as e:
                raise TypeError("id_vars must be a column name or an iterable of column names") from e

        if isinstance(value_vars, str):
            value_vars = [value_vars]

        elif value_vars is not None:
            try:
                value_vars = list(value_vars)

            except TypeError as e:
                raise TypeError("value_vars must be a column name, an iterable of column names, or None") from e

        if not id_vars:
            raise ValueError("id_vars must contain at least one column name")

        if not isinstance(var_name, str) or not var_name.strip():
            raise ValueError("var_name must be a non-empty string")

        if not isinstance(value_name, str) or not value_name.strip():
            raise ValueError("value_name must be a non-empty string")

        if var_name == value_name:
            raise ValueError("var_name and value_name must be different")

        duplicate_id_vars = pd.Index(id_vars)
        if duplicate_id_vars.has_duplicates:
            duplicates = duplicate_id_vars[duplicate_id_vars.duplicated()].unique().tolist()

            raise ValueError(f"id_vars contains duplicate columns: {duplicates}")

        missing_id_vars = [col for col in id_vars if col not in self.columns]
        if missing_id_vars:
            raise ValueError(f"id_vars not found in dataframe: {missing_id_vars}")

        if value_vars is None:
            value_vars = [col for col in self.columns if col not in id_vars]

        duplicate_value_vars = pd.Index(value_vars)
        if duplicate_value_vars.has_duplicates:
            duplicates = duplicate_value_vars[duplicate_value_vars.duplicated()].unique().tolist()
            raise ValueError(f"value_vars contains duplicate columns: {duplicates}")

        missing_value_vars = [col for col in value_vars if col not in self.columns]
        if missing_value_vars:
            raise ValueError(f"value_vars not found in dataframe: {missing_value_vars}")

        if not value_vars:
            raise ValueError("value_vars must contain at least one column after excluding id_vars")

        overlapping_vars = [col for col in value_vars if col in id_vars]
        if overlapping_vars:
            raise ValueError(f"value_vars cannot include id_vars columns: {overlapping_vars}")

        reserved_name_conflicts = [name for name in [var_name, value_name] if name in id_vars]
        if reserved_name_conflicts:
            raise ValueError(f"var_name and value_name cannot conflict with id_vars: {reserved_name_conflicts}")

        if value_name in self.columns:
            raise ValueError("value_name cannot match an existing dataframe column name")

        if not isinstance(aggfunc, str) and not callable(aggfunc):
            raise TypeError("aggfunc must be a string or callable")

        if transform_func is not None and not callable(transform_func):
            raise TypeError("transform_func must be callable when provided")

        melted = self.melt(
            id_vars = id_vars,
            value_vars = value_vars,
            var_name = var_name,
            value_name = value_name
        )

        if transform_func is not None:
            try:
                transformed_values = transform_func(melted[value_name], **kwargs)

            except Exception as e:
                raise RuntimeError(f"transform_func failed while processing '{value_name}': {e}") from e

            try:
                transformed_length = len(transformed_values)

            except TypeError:
                raise ValueError("transform_func must return a same-length array-like or Series")

            if transformed_length != len(melted):
                raise ValueError("transform_func must return the same number of values as the melted dataframe")

            try:
                transformed_values = pd.Series(transformed_values)

            except Exception as e:
                raise ValueError("transform_func must return a one-dimensional array-like or Series") from e

            if transformed_values.ndim != 1:
                raise ValueError("transform_func must return a one-dimensional array-like or Series")

            melted[value_name] = transformed_values.to_numpy()

        if isinstance(pivot_columns, str):
            actual_pivot_columns = pivot_columns
            pivot_columns_list = [pivot_columns]

        elif pivot_columns is None:
            actual_pivot_columns = var_name
            pivot_columns_list = [var_name]

        else:
            try:
                pivot_columns_list = list(pivot_columns)

            except TypeError as e:
                raise TypeError("pivot_columns must be a column name, an iterable of column names, or None") from e

            actual_pivot_columns = pivot_columns_list

        if pivot_index is not None:
            if isinstance(pivot_index, str):
                actual_pivot_index = [pivot_index]

            else:
                try:
                    actual_pivot_index = list(pivot_index)

                except TypeError as e:
                    raise TypeError("pivot_index must be a column name, an iterable of column names, or None") from e
        else:
            if pivot_columns is not None:
                actual_pivot_index = [col for col in id_vars if col not in pivot_columns_list]

                if var_name not in pivot_columns_list:
                    actual_pivot_index.append(var_name)

            else:
                actual_pivot_index = id_vars

        if not actual_pivot_index:
            raise ValueError("pivot_index must contain at least one column name")

        if not pivot_columns_list:
            raise ValueError("pivot_columns must contain at least one column name")

        duplicate_pivot_index = pd.Index(actual_pivot_index)
        if duplicate_pivot_index.has_duplicates:
            duplicates = duplicate_pivot_index[duplicate_pivot_index.duplicated()].unique().tolist()
            raise ValueError(f"pivot_index contains duplicate columns: {duplicates}")

        duplicate_pivot_columns = pd.Index(pivot_columns_list)
        if duplicate_pivot_columns.has_duplicates:
            duplicates = duplicate_pivot_columns[duplicate_pivot_columns.duplicated()].unique().tolist()
            raise ValueError(f"pivot_columns contains duplicate columns: {duplicates}")

        valid_pivot_columns = set(id_vars + [var_name])
        missing_pivot_index = [col for col in actual_pivot_index if col not in valid_pivot_columns]
        if missing_pivot_index:
            raise ValueError(f"pivot_index columns are not available after melting: {missing_pivot_index}")

        missing_pivot_columns = [col for col in pivot_columns_list if col not in valid_pivot_columns]
        if missing_pivot_columns:
            raise ValueError(f"pivot_columns are not available after melting: {missing_pivot_columns}")

        overlapping_pivot_columns = [col for col in pivot_columns_list if col in actual_pivot_index]
        if overlapping_pivot_columns:
            raise ValueError(f"pivot_index and pivot_columns cannot contain the same columns: {overlapping_pivot_columns}")

        try:
            pivoted = melted.pivot_table(
                index = actual_pivot_index,
                columns = actual_pivot_columns,
                values = value_name,
                aggfunc = aggfunc
            ).reset_index()

        except Exception as e:
            raise ValueError(f"Could not pivot melted dataframe with the provided arguments: {e}") from e

        pivoted.columns.name = None

        if inplace:
            self.drop(index = self.index, inplace = True)
            self.drop(columns = self.columns, inplace = True)

            for col in pivoted.columns:
                self[col] = pivoted[col].to_numpy()

            return None

        return self._constructor(pivoted)


    def move_column(self, col_to_move: str, col_target: str, position: str = "after", inplace: bool = False) -> (Self | None):
        """
        Move a specific column to a new position relative to a target column.

        Args:
            col_to_move (str): The name of the column you want to relocate.
            col_target (str): The reference column where the move will happen.
            position (str): Whether to place the column "after" or "before" the
                target column. Defaults to "after".
            inplace (bool): If True, modifies the object directly and returns None.
                Defaults to False.

        Returns:
            The modified object (self) if `inplace` is False, otherwise None.

        Raises:
            ValueError: If the `position` argument is not 'after' or 'before'.

        Example:
        ```
            Initial Columns: ['A', 'B', 'C', 'D']

            Operation: move_column(col_to_move='A', col_target='C', position='after')
            Result:    ['B', 'C', 'A', 'D']

            Usage:
                df.move_column("user_id", "user_name", position="before")
        ```
        """

        if col_to_move not in self.columns or col_target not in self.columns:
            return self if not inplace else None

        if position not in ["after", "before"]:
            raise ValueError("position parameter must be 'after' or 'before'")

        if inplace:
            col_data = self.pop(col_to_move)
            target_index = self.columns.get_loc(col_target)

            insert_index = target_index + 1 if position == "after" else target_index

            self.insert(insert_index, col_to_move, col_data)
            return None

        else:
            cols = self.columns.to_list()
            cols.remove(col_to_move)
            target_index = cols.index(col_target)

            insert_index = target_index + 1 if position == "after" else target_index

            cols.insert(insert_index, col_to_move)
            return self[cols]


    def align_lat_lng(self, inplace: bool = False) -> (Self | None):
        """
        Standardize the coordinate order by moving the 'longitude' column
        immediately after the 'latitude' column.

        .. note::
        This method strictly expects the column names to be exactly **'latitude'**
        and **'longitude'** (case-sensitive). If the columns are named differently
        (e.g., 'lat' or 'LONG'), the alignment will not occur.

        Args:
            inplace (bool): If True, modifies the object directly and returns None.
                Defaults to False.

        Returns:
            The modified object (self) if `inplace` is False, otherwise None.

        Example:
        ```
            Column Reordering:
                ['longitude', 'id', 'latitude']  ->  ['id', 'latitude', 'longitude']

            Usage:
                df.align_lat_lng(inplace=True)
        ```
        """

        return self.move_column(col_to_move = "longitude", col_target = "latitude", position = "after", inplace = inplace)


    def extract_keywords(
            self,
            *source_cols,
            inplace: bool = False,
            remove_extracted: bool = False,
            use_regex: bool = False,
            **target_cols_and_keywords
    ) -> (Self | None):
        """
        Search for keywords or regular expressions across source columns and extract them into new target columns.
        Matches are case-insensitive and are formatted to Title Case in the target column.

        Notes:
            - **Keyword Priority**: Keywords are extracted based on list order. The first item in the list has the highest precedence.
            - **Non-Destructive Updates**: If this method is called multiple times targeting the exact same output column.
                Subsequent calls will only populate empty cells (NaNs) and will NEVER overwrite previously extracted values.
            - **Literal Matching**: Keywords are treated as literal text (e.g., `C++`, `.NET`),
              and will not be interpreted as regular expressions unless `use_regex` is set to True.
            - **Text Coercion**: Source columns are converted to strings only
              for matching, so numeric/object columns do not break text search.

        Args:
            *source_cols: Variable number of column names to search within.
            inplace (bool): If True, modifies the object directly and returns None.
                Defaults to False.
            remove_extracted (bool): If True, removes the matched keywords from the
                original source columns to clean the text. Defaults to False.
            use_regex (bool): If True, treats the input strings in **target_cols_and_keywords
                as regular expressions for pattern extraction. Defaults to False.
            **target_cols_and_keywords: Keyword arguments where the key is the new
                column name and the value is a list of strings to search for.

        Returns:
            The modified object (self) if `inplace` is False, otherwise None.

        Example:
        ```
            Initial Column:
                'description': ["Blue Suede Shoes", "Red Cotton Shirt"]

            Operation:
                extract_keywords('description', colors=['blue', 'red'], remove_extracted=True)

            Result:
                'description': ["Suede Shoes", "Cotton Shirt"]
                'colors':      ["Blue", "Red"]

            Usage:
                df.extract_keywords("comments", "tags", categories=["urgent", "review"], inplace=True)
        ```
        """

        sources = list(source_cols)
        valid_sources = [col for col in sources if col in self.columns]

        if not valid_sources:
            return self if not inplace else None

        combined_text = self[valid_sources].fillna('').astype(str).agg(' '.join, axis = 1)

        result = self.copy() if not inplace else self

        for new_col_name, keywords_list in target_cols_and_keywords.items():
            if isinstance(keywords_list, (str, bytes)) or not hasattr(keywords_list, '__iter__'):
                keywords_list = [keywords_list]

            cleaned_keywords = [
                str(word).strip()
                for word in keywords_list
                if word is not None and str(word).strip()
            ]

            if not cleaned_keywords:
                continue

            extracted = pd.Series(index = combined_text.index, dtype = str)

            for word in reversed(cleaned_keywords):
                if use_regex:
                    try:
                        extracted_match = combined_text.str.extract(word, expand = True)

                    except ValueError:
                        extracted_match = combined_text.str.extract(rf"({word})", expand = True)

                    if not extracted_match.empty:
                        match_col = extracted_match.iloc[:, 0]

                        mask = match_col.notna()
                        extracted.loc[mask] = match_col[mask]

                else:
                    pattern = rf"(?i)(?<!\w){re.escape(word)}(?!\w)"

                    mask = combined_text.str.contains(pattern, na = False, regex = True)
                    extracted.loc[mask] = word.title()

            if new_col_name in result.columns:
                result[new_col_name] = result[new_col_name].astype('object')
                result[new_col_name] = result[new_col_name].where(result[new_col_name].notna(), extracted)

            else:
                result[new_col_name] = extracted

            mask_extracted = result[new_col_name].notna()
            result.loc[mask_extracted, new_col_name] = result.loc[mask_extracted, new_col_name].astype(str).str.title()

            if remove_extracted:
                if use_regex:
                    for col in valid_sources:
                        mask = result[col].notna()

                        for pattern in cleaned_keywords:
                            result.loc[mask, col] = result.loc[mask, col].astype(str).str.replace(pattern, '', regex = True)

                        result.loc[mask, col] = result.loc[mask, col].str.replace(r'\s+', ' ', regex = True).str.strip()

                else:
                    escaped_keywords = [re.escape(word) for word in cleaned_keywords]
                    remove_pattern = rf"(?i)(?<!\w)({'|'.join(escaped_keywords)})(?!\w)"

                    for col in valid_sources:
                        mask = result[col].notna()

                        result.loc[mask, col] = result.loc[mask, col].astype(str).str.replace(remove_pattern, '', regex = True)
                        result.loc[mask, col] = result.loc[mask, col].str.replace(r'\s+', ' ', regex = True).str.strip()

        return None if inplace else result


    def split_list_objects(
            self,
            source_col: str,
            remove_source_col: bool = False,
            explode_rows: bool = False,
            cartesian_explode: bool = False,
            inplace: bool = False,
            **target_cols_and_indices
    ) -> (Self | None):
        """
        Extract specific elements from lists stored within a column into new columns.

        This method allows you to pull items out of list-like objects by their index.
        It can either join multiple indices into a string, explode them into parallel
        separate rows, or perform a sequential cartesian product explosion.

        Notes:
            - **Inplace Restriction**:
              Using `inplace=True` alongside `explode_rows=True` will fail to modify the original
              dataframe's shape due to pandas memory reallocation.
              Always use `inplace=False` and reassign when exploding.
            - **String Joining**: When providing a list of indices with
              `explode_rows=False`, valid extracted elements are joined
              into a single comma-separated string (NaNs are ignored).
            - **Cartesian vs. Parallel Explode**: When exploding multiple columns,
              `cartesian_explode=False` (default) explodes them in parallel (requires
              lists to be of equal length). `cartesian_explode=True` explodes them
              sequentially, creating a cross-join (cartesian product) of all elements.

        Args:
            source_col (str): The name of the column containing list-like objects.
            remove_source_col (bool): If True, deletes the original source column after
                extraction. Defaults to False.
            explode_rows (bool): If True, and an index list is provided, expands the
                dataframe so each extracted element gets its own row. Defaults to False.
            cartesian_explode (bool): If True and multiple target columns are exploded,
                performs a sequential explode resulting in a cartesian product. Defaults to False.
            inplace (bool): If True, modifies the object directly and returns None.
                Defaults to False.
            **target_cols_and_indices: Keyword arguments where the key is the new
                column name and the value is the index (int) or list of indices (list)
                to extract from the source list.

        Returns:
            The modified object (self) if `inplace` is False, otherwise None.

        Example:
        ```
            Initial Data:
                'data_list': [['A', 'B', 'C'], ['X', 'Y']]

            Operation:
                split_list_objects('data_list', first_val=0, second_val=1)

            Result:
                'data_list': [['A', 'B', 'C'], ['X', 'Y']]
                'first_val': ['A', 'X']
                'second_val': ['B', 'Y']

            Usage:
                # Extract index 0 and 1 into 2 new columns 'lat' and 'long'
                df.split_list_objects("coordinates", remove_source_col=True, inplace=True, lat=[0],long=[1])

                # Extract and explode in parallel
                df.split_list_objects("skills", explode_rows=True, tech=[0, 1], soft=[2, 3])

                # Extract and explode generating all combinations (Cartesian Product)
                df.split_list_objects("attributes", explode_rows=True, cartesian_explode=True, colors=[0, 1], sizes=[2, 3])
        ```
        """

        if source_col not in self.columns:
            return self if not inplace else None

        result = self.copy() if not inplace else self

        if explode_rows and not target_cols_and_indices:
            result = result.explode(source_col, ignore_index = True)

        elif target_cols_and_indices:
            cols_to_explode = []

            for col, index in target_cols_and_indices.items():
                if index is None or index == 'all':
                    result[col] = result[source_col]

                    if explode_rows:
                        cols_to_explode.append(col)

                elif isinstance(index, list):
                    extracted_parts = [result[source_col].str[i] for i in index]

                    if explode_rows:
                        result[col] = [[item for item in row if pd.notna(item)] for row in zip(*extracted_parts)]

                        cols_to_explode.append(col)

                    else:
                        result[col] = [
                            ', '.join([str(item) for item in row if pd.notna(item) and str(item).strip() != ''])
                            for row in zip(*extracted_parts)
                        ]

                else:
                    result[col] = result[source_col].str[index]

            if explode_rows and cols_to_explode:
                if cartesian_explode:
                    for col in cols_to_explode:
                        result = result.explode(col, ignore_index = True)

                else:
                    result = result.explode(cols_to_explode, ignore_index = True)

        if remove_source_col:
            result.drop(columns = [source_col], inplace = True)

        return None if inplace else result


    def impute_by_language(
            self,
            reference_col: str,
            *target_cols,
            target_lang: str = 'en',
            inplace: bool = False
    ) -> (Self | None):
        """
        Impute missing or inconsistent values in target columns based on a reference column
        and a preferred language.

        Notes:
            - **Dependencies**: This method requires the `langdetect` library.
                It will raise an ImportError if not installed.
            - **Resolution Hierarchy**: The method determines the correct translation using a 3-pass fallback strategy:
                1. Exact Match: Checks if the target value matches the reference column (ignoring hyphens and case).
                2. AI Detection: Uses `langdetect` to verify if the text matches the `target_lang`.
                3. Auto-Generation: If no valid translation is found, it generates a fallback name by title-casing the reference column.
            - **Deterministic Output**:
                The `langdetect` seed is fixed to 0 to ensure consistent results across multiple runs.

        Args:
            reference_col (str): The column used as a key (e.g., 'category_slug').
            *target_cols: One or more columns to be updated/imputed (e.g., 'category_name').
            target_lang (str): The ISO 639-1 language code to prefer. Defaults to 'en'.
            inplace (bool): If True, modifies the object directly and returns None.
                Defaults to False.

        Returns:
            The modified object (self) if `inplace` is False, otherwise None.

        Example:
        ```
            Initial Data:
                'slug': ['red-apple', 'red-apple', 'green-pear']
                'name': [NaN,         'Red Apple', 'Pear (FR)']

            Operation:
                df.impute_by_language('slug', 'name', target_lang='en')

            Result:
                'name': ['Red Apple', 'Red Apple', 'Green Pear']

            Usage:
                df.impute_by_language("city_id", "city_name", target_lang="ar")
        ```
        """
        if reference_col not in self.columns:
            return self if not inplace else None

        langdetect = _import_optional(
            "langdetect", "langdetect", "impute_by_language"
        )
        langdetect.DetectorFactory.seed = 0
        detect_error = getattr(langdetect, "LangDetectException", Exception)
        detect = langdetect.detect

        result = self.copy() if not inplace else self

        target_cols = list(target_cols)

        for col in target_cols:
            if col not in result.columns:
                continue

            unique_pairs = result[[reference_col, col]].dropna().drop_duplicates()

            mapping_dict = {}
            for ref, val in zip(unique_pairs[reference_col], unique_pairs[col]):
                if ref in mapping_dict:
                    continue

                text = str(val).strip()
                ref_clean = str(ref).replace('-', ' ').lower()

                if text.lower() == ref_clean:
                    mapping_dict[ref] = val
                    continue

            for ref, val in zip(unique_pairs[reference_col], unique_pairs[col]):
                if ref in mapping_dict:
                    continue

                text = str(val).strip()

                if not text or text.isnumeric():
                    continue

                try:
                    if detect(text) == target_lang:
                        mapping_dict[ref] = val
                except detect_error:
                    continue

            all_unique_refs = result[reference_col].dropna().unique()
            for ref in all_unique_refs:
                if ref not in mapping_dict:
                    mapping_dict[ref] = str(ref).replace('-', ' ').title()

            result[col] = result[reference_col].map(mapping_dict).fillna(result[col])

        return None if inplace else result


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
        inplace: bool = False
    ) -> (Self | None):
        """
        Conditionally translate values in a target column based on language detection of a sample.

        This method groups data by `partition_col`, samples text from `detect_col` to determine
        if the language matches `target_lang`. If the percentage of matches is below the
        `threshold`, the unique values in `target_col` for that partition are translated.
        Results are cached to minimize API calls.

        Args:
            partition_col (str): The column used to group or partition the data (e.g., 'category' or 'group_id').
            detect_col (str): The column used to sample text for language detection.
            target_col (str): The column containing the values that need translation.
            target_lang (str): The ISO language code to check for and translate into. Defaults to 'en'.
            sample_size (int): Number of rows to sample from each partition for detection. Defaults to 10.
            number_of_samples (int): Number of independent language-detection
                samples to run per partition. Defaults to 3.
            threshold (float): The required ratio (0.0 to 1.0) of `target_lang` matches to skip translation.
                Defaults to 0.5.
            save_every (int): Cache checkpoint frequency while translating.
                Defaults to 1000.
            inplace (bool): If True, modifies the object directly and returns None. Defaults to False.

        Returns:
            The modified object (self) if `inplace` is False, otherwise None.

        Raises:
            ValueError: If `threshold` is not between 0.0 and 1.0.
            ValueError: If `sample_size`, `number_of_samples`, or `save_every`
                is not a positive integer.

        Example:
        ```
            Scenario:
                Translate 'category_name' only for groups where the 'description'
                is not primarily in English.

            Usage:
                df.translate_conditional_column(
                    partition_col="group_id",
                    detect_col="description",
                    target_col="category_name",
                    target_lang="en",
                    threshold=0.7
                )
        ```
        """

        if not (0.0 <= threshold <= 1.0):
            raise ValueError("The 'threshold' parameter must be a float between 0.0 and 1.0")

        self._require_positive_int(sample_size, "sample_size")
        self._require_positive_int(number_of_samples, "number_of_samples")
        self._require_positive_int(save_every, "save_every")

        required_cols = [partition_col, detect_col, target_col]

        if not all(col in self.columns for col in required_cols):
            return self if not inplace else None

        langdetect = _import_optional(
            "langdetect", "langdetect", "translate_conditional_column"
        )
        langdetect.DetectorFactory.seed = 0
        detect_error = getattr(langdetect, "LangDetectException", Exception)
        detect = langdetect.detect

        result = self.copy() if not inplace else self

        safe_partitions = []
        unique_partitions = result[partition_col].dropna().unique()

        for partition in unique_partitions:
            available_texts = result[result[partition_col] == partition][detect_col].dropna().drop_duplicates()

            if available_texts.empty:
                continue

            actual_sample_size = min(sample_size, len(available_texts.unique().tolist()))

            lang_match_count = 0
            for _ in range(number_of_samples):
                sample_texts = available_texts.sample(n = actual_sample_size)

                combined_text = " . ".join(sample_texts.astype(str).tolist())

                try:
                    if detect(str(combined_text)) == target_lang:
                        lang_match_count += 1

                except detect_error:
                    pass

            if lang_match_count >= (number_of_samples * threshold):
                safe_partitions.append(partition)

        mask_foreign = ~result[partition_col].isin(safe_partitions)
        foreign_unique_values = result.loc[mask_foreign, target_col].dropna().unique()

        if len(foreign_unique_values) == 0:
            return None if inplace else result

        func_name = "translate_conditional_column"

        current_cache = self.cache_manager.get_column_cache(func_name, target_col)
        values_to_translate = [
            val for val in foreign_unique_values
            if not self._cache_has_value(current_cache, val)
        ]

        if values_to_translate:
            num_of_values = len(values_to_translate)
            logger.info("preparing %s values to be translated...", num_of_values)

            translator_module = _import_optional(
                "deep_translator", "deep-translator", "translate_conditional_column"
            )
            translator = translator_module.GoogleTranslator(
                source = 'auto', target = target_lang
            )
            cache_updated = False

            for i, val in enumerate(values_to_translate):
                try:
                    translated = translator.translate(str(val))

                    if not translated or "Error 500" in translated or "Server Error" in translated:
                        continue

                    current_cache[self._cache_value_key(val)] = translated

                    cache_updated = True

                    time.sleep(0.2)

                    if (i + 1) % save_every == 0:
                        self.cache_manager.save_function_cache(func_name)
                        logger.info("Checkpoint: Safely saved %s / %s translations to cache...", i + 1, num_of_values)

                except Exception as e:
                    logger.warning("Failed to translate: '%s'. Skipping cache for this item. Error: %s", val, e)
                    continue

            if cache_updated:
                self.cache_manager.save_function_cache(func_name)

            logger.info("All translations completed and safely cached!")

        unique_vals = result[target_col].dropna().unique()

        lookup_dict = {
            val: self._cache_lookup_value(current_cache, val)
            for val in unique_vals
        }

        result[target_col] = result[target_col].map(lookup_dict).fillna(result[target_col])

        mask_translated = result[target_col].notna()
        result.loc[mask_translated, target_col] = result.loc[mask_translated, target_col].astype(str).str.title()

        return None if inplace else result


    def translate_categorical_column(
            self,
            target_col: str,
            target_lang: str = 'en',
            save_every: int = 1000,
            inplace: bool = False
    ) -> (Self | None):
        """
        Translate all unique categories in a specific column using an automated translator.

        This method optimizes performance and API usage by translating only unique
        values (categories) and utilizing a caching mechanism to avoid re-translating
        previously processed strings.

        Notes:
            Cache keys are stored as strings so non-string categories remain
            reusable after the JSON cache is saved and loaded again.

        Args:
            target_col (str): The name of the column containing categorical text to translate.
            target_lang (str): The ISO language code for the target language.
                Defaults to 'en'.
            save_every (int): Cache checkpoint frequency while translating.
                Defaults to 1000.
            inplace (bool): If True, modifies the object directly and returns None.
                Defaults to False.

        Returns:
            The modified object (self) if `inplace` is False, otherwise None.

        Raises:
            ValueError: If `save_every` is not a positive integer.

        Example:
        ```
            Initial Column:
                'status': ["مكتمل", "قيد الانتظار", "مكتمل"]

            Operation:
                translate_categorical_column(target_col='status', target_lang='en')

            Result:
                'status': ["Completed", "Pending", "Completed"]

            Usage:
                df.translate_categorical_column("product_category", target_lang="fr")
        ```
        """

        if target_col not in self.columns:
            return self if not inplace else None

        self._require_positive_int(save_every, "save_every")

        result = self.copy() if not inplace else self

        unique_values = result[target_col].dropna().unique()

        if len(unique_values) == 0:
            return None if inplace else result

        func_name = "translate_categorical_column"

        current_cache = self.cache_manager.get_column_cache(func_name, target_col)

        values_to_translate = [
            val for val in unique_values
            if not self._cache_has_value(current_cache, val)
        ]

        if values_to_translate:
            cache_updated = False

            translator_module = _import_optional(
                "deep_translator", "deep-translator", "translate_categorical_column"
            )
            translator = translator_module.GoogleTranslator(
                source = 'auto', target = target_lang
            )

            num_of_values = len(values_to_translate)

            for i, val in enumerate(values_to_translate):
                try:
                    translated = translator.translate(str(val))

                    if not translated:
                        continue

                    current_cache[self._cache_value_key(val)] = translated

                    cache_updated = True

                    time.sleep(0.2)

                    if (i + 1) % save_every == 0:
                        self.cache_manager.save_function_cache(func_name)
                        logger.info("Checkpoint: Safely saved %s / %s translations to cache...", i + 1, num_of_values)

                except Exception as e:
                    logger.warning("Failed to translate: %s. Skipping cache for this item. Error: %s", val, e)

                    continue

            if cache_updated:
                self.cache_manager.save_function_cache(func_name)

        unique_vals = result[target_col].dropna().unique()

        lookup_dict = {
            val: self._cache_lookup_value(current_cache, val)
            for val in unique_vals
        }

        result[target_col] = result[target_col].map(lookup_dict).fillna(result[target_col])

        return None if inplace else result


    def truncate_after_substring(self, inplace: bool = False, regex: bool = True, **target_cols_and_substrings) -> (Self | None):
        """
        Remove specific substrings and everything following them from target columns.

        Args:
            inplace (bool): If True, modifies the object directly and returns None.
                Defaults to False.
            regex (bool): Passed to `pandas.Series.str.split`. When True,
                substring values are interpreted as regular expressions.
                Defaults to True.
            **target_cols_and_substrings: Keyword arguments where the key is the column
                name and the value is a string or a list of strings at which to cut off the text.

        Returns:
            The modified object (self) if `inplace` is False, otherwise None.

        Example:
        ```
            Initial Data:
                'title': ['Software Engineer - Remote', 'Data Analyst (Junior)']
                'company': ['Tech Corp, LLC', 'Data Inc.']

            Operation:
                df.truncate_after_substring(
                    title=['-', '('],
                    company=','
                )

            Result:
                'title': ['Software Engineer', 'Data Analyst']
                'company': ['Tech Corp', 'Data Inc']
        ```
        """

        if not target_cols_and_substrings:
            return self if not inplace else None

        result = self.copy() if not inplace else self

        for col, substrings in target_cols_and_substrings.items():
            if col not in result.columns:
                continue

            if not isinstance(substrings, list):
                substrings = [substrings]

            if result[col].notna().any():
                result[col] = result[col].astype('object')

            for sub in substrings:
                mask = result[col].notna()
                result.loc[mask, col] = result.loc[mask, col].astype(str).str.split(sub, n = 1, regex = regex).str[0].str.strip()

        return None if inplace else result


    def fill_missing_coordinates(
        self,
        *fallback_cols: str,
        lat_col: str,
        long_col: str,
        save_every: int = 1000,
        user_agent: str = "data_cleaning_core_geocoder",
        retry_not_found: bool = False,
        inplace: bool = False
    ) -> (Self | None):
        """
        Fill missing latitude and longitude values by geocoding fallback location
        text.

        Notes:
            The method builds a location query from the supplied fallback
            columns, caches geocoding results, and progressively drops the
            leftmost (most specific) part of a failed query, e.g.
            `"street, city, country"` -> `"city, country"` -> `"country"`.
            Existing valid coordinates are preserved and only missing
            coordinate fields are updated.

            Queries that Nominatim cannot resolve are cached as `null`, so later
            runs do not re-query them. Network or service errors are not cached.

        Args:
            *fallback_cols: Ordered columns used to build the geocoding query.
            lat_col (str): Latitude column name.
            long_col (str): Longitude column name.
            save_every (int): Cache checkpoint frequency while geocoding.
                Defaults to 1000.
            user_agent (str): User agent sent to Nominatim. Its usage policy
                asks for one that identifies your application. Defaults to
                `"data_cleaning_core_geocoder"`.
            retry_not_found (bool): If True, re-query locations previously
                cached as not found. Defaults to False.
            inplace (bool): If True, modifies the object directly and returns
                None. Defaults to False.

        Returns:
            Self | None: The dataframe with completed coordinates when
            `inplace=False`, otherwise None.

        Raises:
            ValueError: If `save_every` is not a positive integer.
        """

        self._require_positive_int(save_every, "save_every")

        required_cols = [lat_col, long_col]
        if not all(col in self.columns for col in required_cols):
            logger.warning("Missing coordinate columns. Skipping geocoding.")
            return self if not inplace else None

        result = self.copy() if not inplace else self

        valid_fallbacks = [col for col in fallback_cols if col in result.columns]
        if not valid_fallbacks:
            logger.warning("No valid fallback columns found. Skipping geocoding.")
            return None if inplace else result

        mask_missing = result[lat_col].isna() | result[long_col].isna()
        temp_query_col = "__temp_geocode_query__"

        result[temp_query_col] = None
        result.loc[mask_missing, temp_query_col] = ""

        for col in valid_fallbacks:
            col_str = result[col].astype(str).str.strip()

            col_lower = col_str.str.lower()
            mask_valid_col = (
                result[col].notna() &
                (col_str != "") &
                (col_lower != "nan") &
                (col_lower != "none") &
                (~col_str.str.isnumeric())
            )

            update_mask = mask_missing & mask_valid_col

            empty_mask = update_mask & (result[temp_query_col] == "")
            result.loc[empty_mask, temp_query_col] = col_str[empty_mask]

            append_mask = update_mask & (result[temp_query_col] != "") & (~empty_mask)
            result.loc[append_mask, temp_query_col] += ", " + col_str[append_mask]

        result.loc[result[temp_query_col] == "", temp_query_col] = None

        unique_queries = result.loc[mask_missing, temp_query_col].dropna().unique()

        if len(unique_queries) == 0:
            result.drop(columns = [temp_query_col], inplace = True)
            return None if inplace else result

        func_name = "fill_missing_coordinates"
        current_cache = self.cache_manager.get_column_cache(func_name, "geocode_queries")

        queries_to_fetch = [
            q for q in unique_queries
            if q not in current_cache or (retry_not_found and current_cache[q] is None)
        ]

        if queries_to_fetch:
            cache_updated = False

            num_of_queries = len(queries_to_fetch)
            logger.info("preparing %s locations to be geocoded...", num_of_queries)

            geopy_geocoders = _import_optional(
                "geopy.geocoders", "geopy", "fill_missing_coordinates"
            )
            geopy_rate_limiter = _import_optional(
                "geopy.extra.rate_limiter", "geopy", "fill_missing_coordinates"
            )
            geolocator = geopy_geocoders.Nominatim(user_agent = user_agent)
            geocode = geopy_rate_limiter.RateLimiter(geolocator.geocode, min_delay_seconds=1)

            for i, query in enumerate(queries_to_fetch):
                try:
                    current_search = query
                    location = None

                    while current_search:
                        location = geocode(current_search)

                        if location:
                            break

                        if ',' in current_search:
                            current_search = current_search.split(',', 1)[1].strip()
                        else:
                            break

                    if location:
                        current_cache[query] = (location.latitude, location.longitude)

                    else:
                        current_cache[query] = None
                        logger.info("Location not found for '%s'. Cached as not found.", query)

                    cache_updated = True

                    if (i + 1) % save_every == 0:
                        self.cache_manager.save_function_cache(func_name)
                        logger.info("Checkpoint: Safely saved %s / %s locations to cache...", i + 1, num_of_queries)

                except Exception as e:
                    logger.warning("Failed to geocode: '%s'. Skipping cache for this item. Error: %s", query, e)
                    continue

            if cache_updated:
                self.cache_manager.save_function_cache(func_name)

            logger.info("All geocoding completed and safely cached!")

        mapped_coords = result[temp_query_col].map(current_cache)

        valid_mapped = mapped_coords.notna()
        lat_update_mask = result[lat_col].isna() & valid_mapped
        long_update_mask = result[long_col].isna() & valid_mapped

        if lat_update_mask.any():
            result.loc[lat_update_mask, lat_col] = mapped_coords[lat_update_mask].str[0]

        if long_update_mask.any():
            result.loc[long_update_mask, long_col] = mapped_coords[long_update_mask].str[1]

        result.drop(columns = [temp_query_col], inplace = True)

        return None if inplace else result


    def convert_to_usd(
        self,
        *target_cols,
        country_col: str,
        year_col: str,
        new_cols: bool = True,
        round_decimals: int | None = None,
        inplace: bool = False
    ):
        """
        Convert numeric salary-like columns into USD using country currency codes
        and year-based exchange rates.

        Notes:
            Currency codes and exchange rates are cached to reduce repeated
            lookups. For non-USD currencies, the method requests historical
            exchange-rate data by year and can optionally fall back to earlier
            years when data for the requested year is unavailable.

        Args:
            *target_cols: Numeric columns to convert.
            country_col (str): Column containing the country name used to infer
                the local currency.
            year_col (str): Column containing the reference year for historical
                exchange rates.
            new_cols (bool): If True, create new `<column>_usd` columns.
                Otherwise overwrite the original columns. Defaults to True.
            round_decimals (int | None): Optional number of decimal places to
                round the converted values to. Defaults to None.
            inplace (bool): If True, modifies the object directly and returns
                None. Defaults to False.

        Returns:
            Self | None: The converted dataframe when `inplace=False`,
            otherwise None.
        """
        required_cols = [country_col, year_col]
        if not all(col in self.columns for col in required_cols):
            logger.warning("Missing required columns: %s. Skipping conversion.", required_cols)

            return self if not inplace else None

        result = self.copy() if not inplace else self

        valid_cols = [col for col in target_cols if col in result.columns]
        if not valid_cols:
            logger.warning("No valid target columns found.")

            return None if inplace else result

        if result.empty:
            return None if inplace else result

        pycountry = _import_optional("pycountry", "pycountry", "convert_to_usd")
        babel_numbers = _import_optional(
            "babel.numbers", "Babel", "convert_to_usd"
        )
        yfinance = _import_optional("yfinance", "yfinance", "convert_to_usd")

        func_name = "convert_to_usd"

        currency_cache = self.cache_manager.get_column_cache(func_name, "currency_codes")
        rates_cache = self.cache_manager.get_column_cache(func_name, "exchange_rates")

        cache_updated = False

        unique_pairs = result[[country_col, year_col]].drop_duplicates().dropna()

        valid_years = pd.to_numeric(result[year_col], errors = 'coerce').dropna()
        if valid_years.empty or unique_pairs.empty:
            return None if inplace else result

        min_year_limit = int(valid_years.min())
        current_year = time.localtime().tm_year

        rates_mapping = {}

        for orig_c, orig_y in zip(unique_pairs[country_col], unique_pairs[year_col]):
            c_name = str(orig_c).strip()

            curr_code = currency_cache.get(c_name)
            if not curr_code and c_name not in currency_cache:
                try:
                    country = pycountry.countries.search_fuzzy(c_name)[0]
                    country_alpha2 = country.alpha_2

                    currencies = babel_numbers.get_territory_currencies(country_alpha2)

                    curr_code = currencies[0] if currencies else None

                except LookupError:
                    # pycountry found no matching country; remember the miss.
                    curr_code = None

                currency_cache[c_name] = curr_code
                cache_updated = True


            rate = None

            if curr_code:
                if curr_code.upper() == 'USD':
                    rate = 1.0

                else:
                    try:
                        current_lookback_year = int(float(orig_y))
                    except (TypeError, ValueError):
                        rates_mapping[(orig_c, orig_y)] = None
                        continue

                    while current_lookback_year >= min_year_limit:
                        y_str = str(current_lookback_year)

                        rate_key = f"{curr_code}_{y_str}"
                        rate = rates_cache.get(rate_key)

                        if rate:
                            break

                        if rate_key in rates_cache:
                            # A past year already known to have no data.
                            current_lookback_year -= 1
                            continue

                        try:
                            logger.info("Fetching rate for %s in %s...", curr_code, y_str)

                            ticker = yfinance.Ticker(f"{curr_code}=X")
                            hist = ticker.history(start = f"{y_str}-01-01", end = f"{y_str}-12-31")

                            if not hist.empty:
                                rate = hist['Close'].mean()
                                rates_cache[rate_key] = rate

                                cache_updated = True
                                break

                            if current_lookback_year < current_year:
                                # Past years will not gain data later, so cache the miss.
                                rates_cache[rate_key] = None
                                cache_updated = True

                        except Exception as e:
                            # Network or service errors are transient; do not cache them.
                            logger.warning("Failed to fetch rate for %s in %s: %s", curr_code, y_str, e)

                        current_lookback_year -= 1

            rates_mapping[(orig_c, orig_y)] = rate

        if cache_updated:
            self.cache_manager.save_function_cache(func_name)

        rates_df = pd.Series(rates_mapping).reset_index()
        if not rates_df.empty:
            rates_df.columns = [country_col, year_col, '_conversion_rate']

            temp_df = _left_merge_aligned(result, rates_df, [country_col, year_col])

            for col in valid_cols:
                col_name = f"{col}_usd" if new_cols else col
                result[col_name] = result[col] / temp_df['_conversion_rate']

                if round_decimals is not None:
                    result[col_name] = result[col_name].round(round_decimals)

        return None if inplace else result


    def handle_outliers(
        self,
        *target_cols,
        hierarchy_cols: list,
        min_samples: int = 5,
        inplace: bool = False,
        get_hierarchical_stats: bool = False
    ) -> (Self | tuple[Self, Self] | None):
        """
        Replace outlier values with missing values using hierarchical IQR rules.

        Notes:
            The method starts with the full grouping hierarchy and progressively
            relaxes it by removing the last grouping column at each pass. Within
            every valid group, values outside the 1.5 IQR bounds are converted
            to `NaN`.

        Args:
            *target_cols: Numeric columns to evaluate for outliers.
            hierarchy_cols (list): Ordered grouping columns used to define peer
                groups.
            min_samples (int): Minimum number of rows required for a group to be
                considered valid. Defaults to 5.
            inplace (bool): If True, modifies the object directly and returns
                None. Defaults to False.
            get_hierarchical_stats (bool): If True, also return the existing
                hierarchical median statistics computed from the original data
                before outliers are replaced. Defaults to False.

        Returns:
            Self | tuple[Self, Self] | None: The cleaned dataframe when
            `inplace=False`; when `get_hierarchical_stats=True`, return
            `(cleaned_dataframe, statistics)` for non-inplace calls and the
            statistics directly for inplace calls. Otherwise return None for
            inplace calls.
        """

        hierarchical_stats = (
            self.get_hierarchical_stats(
                *target_cols,
                hierarchy_cols=hierarchy_cols,
            )
            if get_hierarchical_stats
            else None
        )

        if not hierarchy_cols:
            logger.warning("No hierarchy columns provided.")
            if get_hierarchical_stats:
                return hierarchical_stats if inplace else (self, hierarchical_stats)
            return self if not inplace else None

        result = self.copy() if not inplace else self

        for target_col in target_cols:
            if target_col not in result.columns:
                continue

            processed_mask = pd.Series(False, index = result.index)
            col_outliers_count = 0

            current_hierarchy = hierarchy_cols.copy()
            level = 1

            while current_hierarchy:
                unprocessed_mask = (~processed_mask) & result[target_col].notna()

                if unprocessed_mask.sum() == 0:
                    break

                grouped = result.groupby(current_hierarchy, dropna = False)[target_col]

                group_sizes = grouped.transform('count')
                valid_group_mask = group_sizes >= min_samples

                valid_data = result[valid_group_mask]

                if not valid_data.empty:
                    valid_grouped = valid_data.groupby(current_hierarchy, dropna = False)[target_col]

                    q1 = valid_grouped.quantile(0.25).rename('q1')
                    q3 = valid_grouped.quantile(0.75).rename('q3')

                    bounds = pd.concat([q1, q3], axis = 1).reset_index()
                    iqr = bounds['q3'] - bounds['q1']

                    bounds['lower_bound'] = bounds['q1'] - 1.5 * iqr
                    bounds['upper_bound'] = bounds['q3'] + 1.5 * iqr

                    merged_df = _left_merge_aligned(
                            result,
                            bounds.drop(columns = ['q1', 'q3']),
                            current_hierarchy
                        )

                    is_outlier = (
                        valid_group_mask & unprocessed_mask &
                        merged_df['lower_bound'].notna() &
                        ((result[target_col] < merged_df['lower_bound']) |
                        (result[target_col] > merged_df['upper_bound']))
                    )

                    new_outliers_count = is_outlier.sum()
                    col_outliers_count += new_outliers_count

                    if new_outliers_count > 0:
                        result.loc[is_outlier, target_col] = np.nan

                processed_mask = processed_mask | valid_group_mask

                current_hierarchy.pop()
                level += 1

        if get_hierarchical_stats:
            return hierarchical_stats if inplace else (result, hierarchical_stats)
        return None if inplace else result


    def drop_outliers(
        self,
        *target_cols,
        hierarchy_cols: list,
        min_samples: int = 5,
        inplace: bool = False,
        get_hierarchical_stats: bool = False
    ) -> (Self | tuple[Self, Self] | None):
        """
        Remove rows that contain outlier values based on hierarchical IQR rules.

        Notes:
            This method shares the same grouping logic as `handle_outliers`, but
            instead of replacing detected outliers with `NaN`, it drops the
            affected rows after scanning all requested target columns.

        Args:
            *target_cols: Numeric columns to inspect for outliers.
            hierarchy_cols (list): Ordered grouping columns used to define peer
                groups.
            min_samples (int): Minimum number of rows required for a group to be
                considered valid. Defaults to 5.
            inplace (bool): If True, modifies the object directly and returns
                None. Defaults to False.
            get_hierarchical_stats (bool): If True, also return the existing
                hierarchical median statistics computed from the original data
                before outlier rows are dropped. Defaults to False.

        Returns:
            Self | tuple[Self, Self] | None: The filtered dataframe when
            `inplace=False`; when `get_hierarchical_stats=True`, return
            `(filtered_dataframe, statistics)` for non-inplace calls and the
            statistics directly for inplace calls. Otherwise return None for
            inplace calls.
        """

        hierarchical_stats = (
            self.get_hierarchical_stats(
                *target_cols,
                hierarchy_cols=hierarchy_cols,
            )
            if get_hierarchical_stats
            else None
        )

        if not hierarchy_cols:
            logger.warning("No hierarchy columns provided.")
            if get_hierarchical_stats:
                return hierarchical_stats if inplace else (self, hierarchical_stats)
            return self if not inplace else None

        result = self.copy() if not inplace else self
        drop_mask = np.zeros(len(result), dtype = bool)

        for target_col in target_cols:
            if target_col not in result.columns:
                continue

            processed_mask = pd.Series(False, index = result.index)
            col_outliers_count = 0

            current_hierarchy = hierarchy_cols.copy()
            level = 1

            while current_hierarchy:
                unprocessed_mask = (~processed_mask) & result[target_col].notna()

                if unprocessed_mask.sum() == 0:
                    break

                grouped = result.groupby(current_hierarchy, dropna = False)[target_col]

                group_sizes = grouped.transform('count')
                valid_group_mask = group_sizes >= min_samples

                valid_data = result[valid_group_mask]

                if not valid_data.empty:
                    valid_grouped = valid_data.groupby(current_hierarchy, dropna = False)[target_col]

                    q1 = valid_grouped.quantile(0.25).rename('q1')
                    q3 = valid_grouped.quantile(0.75).rename('q3')

                    bounds = pd.concat([q1, q3], axis = 1).reset_index()
                    iqr = bounds['q3'] - bounds['q1']
                    bounds['lower_bound'] = bounds['q1'] - 1.5 * iqr
                    bounds['upper_bound'] = bounds['q3'] + 1.5 * iqr

                    merged_df = _left_merge_aligned(
                            result,
                            bounds.drop(columns = ['q1', 'q3']),
                            current_hierarchy
                        )

                    is_outlier = (
                        valid_group_mask & unprocessed_mask &
                        merged_df['lower_bound'].notna() &
                        ((result[target_col] < merged_df['lower_bound']) |
                         (result[target_col] > merged_df['upper_bound']))
                    )

                    new_outliers_count = is_outlier.sum()
                    col_outliers_count += new_outliers_count

                    if new_outliers_count > 0:
                        drop_mask |= is_outlier.to_numpy(dtype = bool)

                processed_mask = processed_mask | valid_group_mask

                current_hierarchy.pop()
                level += 1

        if drop_mask.any():
            # Drop by position so a duplicated index label never removes its twin row.
            original_index = result.index
            result.index = pd.RangeIndex(len(result))
            result.drop(index = np.flatnonzero(drop_mask), inplace = True)
            result.index = original_index[~drop_mask]

        if get_hierarchical_stats:
            return hierarchical_stats if inplace else (result, hierarchical_stats)
        return None if inplace else result


    def fill_missing(
        self,
        *target_cols,
        strategy: str = 'median',
        hierarchy_cols: list | str = None,
        inplace: bool = False,
        get_hierarchical_stats: bool = False
    ) -> (Self | tuple[Self, Self] | None):
        """
        Impute missing values by progressively relaxing a grouping hierarchy.

        Notes:
            Supported strategies are `median`, `mean`, and `mode`. The method
            attempts imputation using the full hierarchy first, then removes the
            last grouping column one level at a time until no grouping columns
            remain or the column is fully imputed.

        Args:
            *target_cols: Columns whose missing values should be filled.
            strategy (str): Aggregation strategy to use. Supported values are
                `median`, `mean`, `mode`, and `mod`. Defaults to `median`.
            hierarchy_cols (list | str, optional): Grouping columns that define
                the imputation hierarchy. A single string is treated as a
                one-item list.
            inplace (bool): If True, modifies the object directly and returns
                None. Defaults to False.
            get_hierarchical_stats (bool): If True, also return the existing
                hierarchical statistics computed from the original data before
                imputation. The requested `strategy` is used. Defaults to
                False.

        Returns:
            Self | tuple[Self, Self] | None: The imputed dataframe when
            `inplace=False`; when `get_hierarchical_stats=True`, return
            `(imputed_dataframe, statistics)` for non-inplace calls and the
            statistics directly for inplace calls. Otherwise return None for
            inplace calls.

        Example:
        ```
            df.fill_missing(
                "salary_min",
                "salary_max",
                strategy="median",
                hierarchy_cols=["country", "title", "company_name"]
            )
        ```
        """
        result = self.copy() if not inplace else self

        valid_cols = [col for col in target_cols if col in result.columns]
        if not valid_cols:
            logger.warning("No valid target columns found for imputation.")
            if get_hierarchical_stats:
                hierarchical_stats = self.get_hierarchical_stats(
                    *target_cols,
                    strategy=strategy,
                    hierarchy_cols=hierarchy_cols,
                )
                return hierarchical_stats if inplace else (result, hierarchical_stats)
            return None if inplace else result

        strategy = str(strategy).lower()
        if strategy not in ['median', 'mean', 'mode', 'mod']:
            raise ValueError(
                f"Invalid strategy '{strategy}'. Use 'median', 'mean', or 'mode'."
            )

        if isinstance(hierarchy_cols, str):
            hierarchy_cols = [hierarchy_cols]

        valid_group = [col for col in hierarchy_cols if col in result.columns] if hierarchy_cols else []

        hierarchical_stats = (
            self.get_hierarchical_stats(
                *target_cols,
                strategy=strategy,
                hierarchy_cols=valid_group,
            )
            if get_hierarchical_stats
            else None
        )

        for col in valid_cols:
            if result[col].isna().sum() == 0:
                continue

            current_group = valid_group.copy()

            while len(current_group) > 0 and result[col].isna().sum() > 0:
                if strategy in ['median', 'mean']:
                    fill_vals = result.groupby(current_group, dropna = False)[col].transform(strategy)

                    result[col] = result[col].fillna(fill_vals)

                else:
                    counts = (
                        result[result[col].notna()]
                        .groupby(current_group + [col], dropna = False)
                        .size()
                        .reset_index(name = '_count')
                    )

                    if counts.empty:
                        current_group.pop()
                        continue

                    counts = counts.sort_values(current_group + ['_count'], ascending = [True] * len(current_group) + [False])

                    modes_df = counts.drop_duplicates(subset = current_group).rename(columns = {col: '_mode_val'})

                    temp_df = _left_merge_aligned(result, modes_df[current_group + ['_mode_val']], current_group)
                    result[col] = result[col].fillna(temp_df['_mode_val'])

                current_group.pop()

        if get_hierarchical_stats:
            return hierarchical_stats if inplace else (result, hierarchical_stats)
        return None if inplace else result


    def get_hierarchical_stats(
        self,
        *target_cols,
        strategy: str | Iterable[str] = 'median',
        hierarchy_cols: list | str = None,
        output_type=None,
    ):
        """
        Extract statistics for each progressively relaxed hierarchy level.

        The full valid hierarchy is aggregated first, then its last grouping
        column is removed for each subsequent level. No global level is added,
        matching :meth:`fill_missing`. ``group_key`` is always a tuple,
        including for one-column hierarchies. The source object is not modified.

        ``strategy`` accepts one string or an iterable of strings. Supported
        values are ``median``, ``mean``, ``mode``, and the ``mod`` alias for
        ``mode``. Requested strategies are normalized to canonical values,
        deduplicated in first-seen order, and emitted in strategy-then-target-
        then-hierarchy order. Every emitted row has one unique logical key:
        ``(hierarchy_level, hierarchy_columns, group_key, target_column,
        strategy)``.

        By default, ``output_type`` is ``None`` and the result uses the same
        ``BaseData``-family type as ``self`` when its constructor signature can
        bind ``data=``. A signature-incompatible default subclass falls back to
        a plain ``pandas.DataFrame``. If the signature accepts ``data=`` but the
        constructor raises, that original constructor exception propagates.
        Explicit output types propagate construction errors directly. Supported
        explicit types are ``pandas.DataFrame``, ``dict``, and BaseData-family
        classes. Dict conversion derives keys from the actual dataframe columns.

        Missing target values are ignored by ``mean`` and ``median``; groups
        containing only null targets still produce a row with a null statistic.
        ``mode`` excludes null target values and performs one grouped count per
        target and hierarchy level, avoiding an N*T melt intermediate at the
        cost of additional groupby operations. Groups without a non-null target
        have no mode row, and ties use deterministic group-count ordering.
        Null hierarchy values remain valid grouping keys.

        Args:
            *target_cols: Target columns from which statistics are extracted.
                Columns that are not present are ignored; if none are valid,
                an empty result is returned.
            strategy (str | iterable[str]): One strategy or an ordered iterable
                of strategies. ``mod`` is normalized to ``mode`` and repeated
                canonical values are emitted once. Defaults to ``median``.
            hierarchy_cols (list | str, optional): Grouping columns defining
                the hierarchy. A single string is normalized to a one-item
                list, and missing columns are ignored. Duplicate valid names
                are removed while preserving their first-seen order. Each
                successive level removes the last remaining grouping column.
                Target columns may not overlap the valid hierarchy columns.
            output_type (type | None, optional): Result type. ``None`` uses
                the type of ``self`` when supported, with a plain DataFrame
                fallback. ``pandas.DataFrame``, ``dict``, and BaseData-family
                classes are supported. Defaults to ``None``.

        Returns:
            pandas.DataFrame | BaseData | dict: Statistics in the requested
            output schema. Empty and invalid-result cases use the requested
            output type whenever possible.

        Examples:
        ```
            default_stats = df.get_hierarchical_stats(
                "salary_min",
                hierarchy_cols=["country", "title", "company_name"],
            )
            dataframe_stats = df.get_hierarchical_stats(
                "salary_min",
                strategy="mean",
                hierarchy_cols="country",
                output_type=pd.DataFrame,
            )
            dict_stats = df.get_hierarchical_stats(
                "salary_min",
                hierarchy_cols="country",
                output_type=dict,
            )
        ```
        """
        _validate_output_type(output_type)
        default_output = output_type is None
        resolved_output_type = type(self) if default_output else output_type
        _validate_output_type(resolved_output_type)

        stats_df = pd.DataFrame(columns=_HIERARCHICAL_STATS_COLUMNS)

        if isinstance(strategy, str):
            requested_strategies = [strategy]
        else:
            if not isinstance(strategy, Iterable):
                raise TypeError(
                    "strategy must be a string or an iterable of strings."
                )
            requested_strategies = list(strategy)

        if not requested_strategies:
            raise ValueError("strategy iterable must contain at least one strategy.")

        for requested_strategy in requested_strategies:
            if not isinstance(requested_strategy, str):
                raise TypeError(
                    "strategy must be a string or an iterable of strings."
                )

        canonical_strategies = []
        for requested_strategy in requested_strategies:
            normalized_strategy = requested_strategy.lower()
            if normalized_strategy == 'mod':
                normalized_strategy = 'mode'
            if normalized_strategy not in ['median', 'mean', 'mode']:
                raise ValueError(
                    f"Invalid strategy '{normalized_strategy}'. Use 'median', 'mean', or 'mode'."
                )
            if normalized_strategy not in canonical_strategies:
                canonical_strategies.append(normalized_strategy)

        valid_cols = list(dict.fromkeys(col for col in target_cols if col in self.columns))
        if not valid_cols:
            logger.warning("No valid target columns found for statistics extraction.")
            if default_output:
                return _convert_default_output(stats_df, resolved_output_type)
            return _convert_output(stats_df, resolved_output_type)

        if isinstance(hierarchy_cols, str):
            hierarchy_cols = [hierarchy_cols]
        valid_group = list(
            dict.fromkeys(col for col in hierarchy_cols if col in self.columns)
        ) if hierarchy_cols else []
        overlapping_cols = [col for col in valid_cols if col in valid_group]
        if overlapping_cols:
            raise ValueError(
                "target columns cannot overlap hierarchy columns: "
                f"{overlapping_cols}"
            )
        if not valid_group or self.empty:
            if default_output:
                return _convert_default_output(stats_df, resolved_output_type)
            return _convert_output(stats_df, resolved_output_type)

        source_df = pd.DataFrame(self)
        strategy_rows = {
            current_strategy: [] for current_strategy in canonical_strategies
        }
        numeric_strategies = [
            current_strategy
            for current_strategy in canonical_strategies
            if current_strategy in ['median', 'mean']
        ]

        if numeric_strategies:
            aggregation_cols = valid_cols.copy()
            level_aggregates = []
            current_group = valid_group.copy()
            while current_group:
                grouped = (
                    source_df.groupby(current_group, dropna=False)[aggregation_cols]
                    .agg(numeric_strategies)
                    .reset_index()
                )
                level_aggregates.append((tuple(current_group), grouped))
                current_group.pop()

            for current_strategy in canonical_strategies:
                if current_strategy not in numeric_strategies:
                    continue
                for col in valid_cols:
                    for hierarchy_columns, grouped in level_aggregates:
                        group_keys = list(
                            zip(*(grouped[group_col].tolist() for group_col in hierarchy_columns))
                        )
                        statistics = grouped[(col, current_strategy)].tolist()
                        for group_key, statistic in zip(group_keys, statistics):
                            strategy_rows[current_strategy].append(
                                {
                                    'hierarchy_level': len(hierarchy_columns),
                                    'hierarchy_columns': hierarchy_columns,
                                    'group_key': group_key,
                                    'target_column': col,
                                    'strategy': current_strategy,
                                    'statistic': statistic,
                                }
                            )

        if 'mode' in canonical_strategies:
            internal_count_column = '_hierarchical_stats_count'
            while internal_count_column in source_df.columns:
                internal_count_column += '_'

            for col in valid_cols:
                current_group = valid_group.copy()
                while current_group:
                    grouping_columns = current_group + [col]
                    non_null_values = source_df.loc[
                        source_df[col].notna(), grouping_columns
                    ]
                    if not non_null_values.empty:
                        counts = (
                            non_null_values.groupby(
                                grouping_columns,
                                dropna=False,
                            )
                            .size()
                            .reset_index(name=internal_count_column)
                        )
                        counts = counts.sort_values(
                            current_group + [internal_count_column],
                            ascending=[True] * len(current_group) + [False],
                            kind='mergesort',
                        )
                        modes_df = counts.drop_duplicates(subset=current_group)
                        group_keys = list(
                            zip(*(modes_df[group_col].tolist() for group_col in current_group))
                        )
                        statistics = modes_df[col].tolist()
                        for group_key, statistic in zip(group_keys, statistics):
                            strategy_rows['mode'].append(
                                {
                                    'hierarchy_level': len(current_group),
                                    'hierarchy_columns': tuple(current_group),
                                    'group_key': group_key,
                                    'target_column': col,
                                    'strategy': 'mode',
                                    'statistic': statistic,
                                }
                            )
                    current_group.pop()

        rows = []
        for current_strategy in canonical_strategies:
            rows.extend(strategy_rows[current_strategy])

        stats_df = pd.DataFrame(rows, columns=_HIERARCHICAL_STATS_COLUMNS)
        logical_key_columns = [
            'hierarchy_level',
            'hierarchy_columns',
            'group_key',
            'target_column',
            'strategy',
        ]
        if stats_df.duplicated(subset=logical_key_columns).any():
            raise AssertionError(
                "hierarchical statistics produced duplicate logical keys"
            )
        if default_output:
            return _convert_default_output(stats_df, resolved_output_type)
        return _convert_output(stats_df, resolved_output_type)


    def categorize_by_reference(
        self,
        *source_cols: str,
        target_col: str,
        reference: dict,
        default_category: str = 'Other',
        inplace: bool = False
    ):
        """
        Categorize text data into broader groups based on a reference lexicon dictionary.

        This method scans one or more source text columns for specific keywords defined in a
        lexicon dictionary. If a match is found, the dictionary's key (the category)
        is assigned to a new target column.

        Notes:
            - The search uses word boundaries (\b) to prevent partial word matches
              (e.g., matching 'React' will not trigger on 'Reactor').
            - The implementation builds an Aho-Corasick automaton and evaluates
              each unique combined text once, then maps results back to all rows.
            - Longer keywords take precedence over shorter ones (e.g., "Data Analyst"
              matches before "Data").
            - Empty keyword lists and blank keyword values are ignored safely.

        Args:
            *source_cols (str): Source columns containing the raw text to search.
            target_col (str): The new or existing column where categories will be saved.
            reference (dict): A dictionary where keys are category names and values
                are lists of keywords to search for.
            default_category (str): The fallback value if no keywords are matched.
                Defaults to 'Other'.
            inplace (bool): If True, modifies the object directly and returns None.
                Defaults to False.

        Returns:
            The modified object (self) if `inplace` is False, otherwise None.

        Example:
        ```
            reference = {
                "Data": ["data analyst", "business intelligence"],
                "Software": ["python developer", "backend engineer"]
            }

            jobs.categorize_by_reference(
                "title",
                "description",
                target_col = "field_label",
                reference = reference,
                inplace = True
            )
        ```
        """
        sources = list(source_cols)
        valid_sources = [col for col in sources if col in self.columns]

        if not valid_sources:
            return self if not inplace else None

        result = self.copy() if not inplace else self

        combined_series = result[valid_sources[0]].fillna('').astype(str)

        if len(valid_sources) > 1:
            for col in valid_sources[1:]:
                combined_series += " " + result[col].fillna('').astype(str)

        if not isinstance(reference, dict) or not reference:
            result[target_col] = default_category

            return None if inplace else result

        ahocorasick = _import_optional(
            "ahocorasick", "pyahocorasick", "categorize_by_reference"
        )
        automaton = ahocorasick.Automaton()

        category_priority = {cat: idx for idx, cat in enumerate(reversed(list(reference.keys())))}
        valid_keyword_found = False

        for category, keywords in reference.items():
            if not isinstance(keywords, list):
                keywords = [keywords]

            for kw in keywords:
                if kw is not None and str(kw).strip():
                    clean_kw = str(kw).strip().lower()

                    automaton.add_word(clean_kw, (len(clean_kw), category_priority[category], category))
                    valid_keyword_found = True

        if not valid_keyword_found:
            result[target_col] = default_category

            return None if inplace else result

        automaton.make_automaton()

        unique_texts = combined_series.unique()
        mapping_dict = {}

        for text in unique_texts:
            if not text:
                mapping_dict[text] = default_category
                continue

            text_lower = text.lower()
            text_len = len(text_lower)

            best_match = None
            best_len = -1
            best_priority = float('inf')

            for end_index, (kw_len, priority, category) in automaton.iter(text_lower):
                start_index = end_index - kw_len + 1

                if start_index > 0 and text_lower[start_index - 1].isalnum():
                    continue

                if end_index + 1 < text_len and text_lower[end_index + 1].isalnum():
                    continue

                if kw_len > best_len or (kw_len == best_len and priority < best_priority):
                    best_match = category
                    best_len = kw_len
                    best_priority = priority

            mapping_dict[text] = best_match if best_match else default_category

        result[target_col] = combined_series.map(mapping_dict)

        return None if inplace else result


    def extract_entities_nlp(
        self,
        *source_cols,
        lexicon_source: str | os.PathLike | dict,
        spacy_model: str = "en_core_web_sm",
        exclude_pos: set | list | tuple | str | None = None,
        save_every: int = 1000,
        inplace: bool = False,
        noisy_terms: set | None = None,
        noisey_terms: set | None = None,
        **target_cols_and_json_keys
    ) -> (Self | None):
        """
        Extract entities from free text by matching against a cached lexicon with
        spaCy tokenization and lemmatization.

        Notes:
            Terms are loaded from a JSON file or dictionary, grouped by target
            output column, and matched across the combined source text. Results
            are cached per unique text snippet so repeat runs avoid reprocessing
            the same content.

        Args:
            *source_cols: Text columns whose combined content will be searched.
            lexicon_source (str | os.PathLike | dict): Path to a lexicon JSON
                file or a preloaded dictionary with equivalent structure.
            spacy_model (str): Installed spaCy model name or model path.
                Defaults to `"en_core_web_sm"`.
            exclude_pos (set | list | tuple | str | None): Optional POS tag or
                tags to ignore for single-token matches, for example
                `{"VERB", "ADV"}`. Defaults to None.
            save_every (int): Cache checkpoint frequency during NLP processing.
                Defaults to 1000.
            inplace (bool): If True, modifies the object directly and returns
                None. Defaults to False.
            noisy_terms (set | None): Terms that should be ignored even if they
                are matched. Terms are compared case-insensitively. Defaults to
                None.
            noisey_terms (set | None): Deprecated spelling of `noisy_terms`.
                Kept for backward compatibility.
            **target_cols_and_json_keys: Mapping where each key is an output
                column name and each value is a lexicon key or list of keys to
                collect terms from.

        Returns:
            Self | None: The dataframe with extracted entity-list columns when
            `inplace=False`, otherwise None.

        Raises:
            ValueError: If `save_every` is not a positive integer.

        Example:
        ```
            search_dataframe.extract_entities_nlp(
                "title",
                "description",
                lexicon_source="skills_lexicon.json",
                basic_skills=["skills", "technologies"]
            )
        ```
        """

        ignored_terms = set()

        if noisy_terms is not None:
            ignored_terms.update(str(term).strip().lower() for term in noisy_terms if str(term).strip())

        if noisey_terms is not None:
            ignored_terms.update(str(term).strip().lower() for term in noisey_terms if str(term).strip())

        if exclude_pos is None:
            exclude_pos = set()

        elif isinstance(exclude_pos, str):
            exclude_pos = {exclude_pos.strip().upper()} if exclude_pos.strip() else set()

        else:
            exclude_pos = {str(pos).strip().upper() for pos in exclude_pos}

        self._require_positive_int(save_every, "save_every")

        if not target_cols_and_json_keys:
            raise ValueError("Provide at least one target column and JSON key as keyword arguments.")

        if isinstance(lexicon_source, (str, os.PathLike)):
            with open(os.fspath(lexicon_source), encoding = 'utf-8') as f:
                lexicon_data = json.load(f)
        else:
            lexicon_data = lexicon_source

        term_dict = {}

        for target_col, json_key in target_cols_and_json_keys.items():
            keys_to_match = json_key if isinstance(json_key, list) else [json_key]
            extracted_terms = set()

            stack = [(lexicon_data, False)]

            while stack:
                current_node, is_inside = stack.pop()

                if isinstance(current_node, dict):
                    for k, v in current_node.items():
                        current_is_target = is_inside or (str(k) in keys_to_match)

                        if current_is_target and isinstance(v, bool) and v is True:
                            term = str(k).strip().lower()

                            if term:
                                extracted_terms.add(term)

                        elif current_is_target and isinstance(v, str):
                            term = str(v).strip().lower()

                            if term:
                                extracted_terms.add(term)

                        elif isinstance(v, (dict, list)):
                            stack.append((v, current_is_target))

                elif isinstance(current_node, list):
                    for item in current_node:
                        if is_inside and isinstance(item, str):
                            term = str(item).strip().lower()

                            if term:
                                extracted_terms.add(term)

                        elif isinstance(item, (dict, list)):
                            stack.append((item, is_inside))

            if extracted_terms:
                term_dict[target_col] = list(extracted_terms)

            else:
                logger.warning("No data found for keys '%s'. Skipping '%s'.", keys_to_match, target_col)

        if not term_dict:
            raise ValueError("No valid terms were extracted from the requested lexicon keys.")


        valid_sources = [col for col in source_cols if col in self.columns]
        if not valid_sources:
            logger.warning("No valid source columns found.")

            return None if inplace else self.copy()

        result = self.copy() if not inplace else self
        combined_series = result[valid_sources[0]].fillna('').astype(str)

        for col in valid_sources[1:]:
            combined_series += " " + result[col].fillna('').astype(str)

        combined_text = combined_series.str.strip()
        valid_mask = combined_text != ""
        all_valid_texts = combined_text[valid_mask].unique().tolist()

        if not all_valid_texts:
            logger.warning("No valid text found in the specified columns.")
            return None if inplace else result


        func_name = "extract_entities_nlp"
        caches = {}

        for target_col in term_dict.keys():
            cache_obj = self.cache_manager.get_column_cache(func_name, target_col)

            caches[target_col] = cache_obj if isinstance(cache_obj, dict) else {}

        texts_to_process = [
            text for text in all_valid_texts
            if any(text not in caches[col]
            for col in term_dict.keys())
        ]

        if texts_to_process:
            spacy = _import_optional("spacy", "spacy", "extract_entities_nlp")
            matcher_module = _import_optional(
                "spacy.matcher", "spacy", "extract_entities_nlp"
            )
            logger.info("Loading NLP Model and building Multi-Label Matcher...")

            if not isinstance(spacy_model, (str, os.PathLike)) or not os.fspath(spacy_model).strip():
                raise ValueError("spacy_model must be a non-empty model name or path.")

            nlp = spacy.load(os.fspath(spacy_model))

            matcher = matcher_module.Matcher(nlp.vocab)

            for col_name, terms in term_dict.items():
                all_patterns = []

                for term_doc in nlp.pipe(terms, disable = ["parser", "ner"]):
                    pattern_lemma = []
                    pattern_lower = []

                    for token in term_doc:
                        pattern_lemma.append({"LEMMA": {"IN": [token.lemma_.lower(), token.lemma_.title()]}})

                        pattern_lower.append({"LOWER": token.text.lower()})

                    if pattern_lemma:
                        all_patterns.append(pattern_lemma)

                    if pattern_lower:
                        all_patterns.append(pattern_lower)

                if all_patterns:
                    matcher.add(col_name, all_patterns)

            num_texts = len(texts_to_process)
            logger.info("Extracting smart entities from %s rows simultaneously...", num_texts)

            cache_updated = False

            for i, doc in enumerate(nlp.pipe(texts_to_process, batch_size = 100, disable = ["parser", "ner"])):
                original_text = texts_to_process[i]
                found_entities = {col: set() for col in term_dict.keys()}

                try:
                    matches = matcher(doc)

                    for match_id, start, end in matches:
                        col_name = nlp.vocab.strings[match_id]
                        span = doc[start:end]
                        clean_skill = " ".join([t.lemma_ for t in span]).title()

                        is_valid = True

                        if len(clean_skill) == 1:
                            if not span.text.isupper() or not span[0].is_alpha:
                                is_valid = False

                        if len(span) == 1 and is_valid:
                            if span[0].pos_ in exclude_pos:
                                is_valid = False

                        if clean_skill.lower() in ignored_terms:
                            is_valid = False

                        if is_valid:
                            found_entities[col_name].add(clean_skill)

                    for col in term_dict.keys():
                        caches[col][original_text] = list(found_entities[col])

                    cache_updated = True

                except Exception as e:
                    logger.warning("Error processing text snippet: %s", e)

                if (i + 1) % save_every == 0 and cache_updated:
                    self.cache_manager.save_function_cache(func_name)

                    logger.info("Checkpoint: Safely saved %s / %s...", i + 1, num_texts)

                    cache_updated = False

            if cache_updated:
                self.cache_manager.save_function_cache(func_name)

                logger.info("All NLP extraction completed and safely cached!")
        else:
            logger.info("All requested data is already cached. No NLP processing needed.")

        for col in term_dict.keys():
            result[col] = [caches[col].get(text, []) for text in combined_text]

        return None if inplace else result


    def save_to_json(
            self,
            file_path: str,
            orient: str = 'records',
            append: bool = False,
            force_ascii: bool = False,
            indent: int = 4
        ) -> None:
        """
        Save the dataframe to a JSON file only when content has changed.

        Notes:
            Before writing, the method compares the current dataframe payload to
            the existing file contents when possible. This avoids unnecessary
            rewrites and keeps the output directory stable across unchanged runs.
            Datetime columns are serialized as `%Y-%m-%d %H:%M:%S` strings to
            keep the JSON output human-readable and stable across environments.
            Paths without a directory component, such as `output.json`, are
            written to the current working directory.

        Args:
            file_path (str): Destination JSON file path.
            orient (str): JSON orientation passed to `pandas.DataFrame.to_json`.
                Defaults to `"records"`.
            append (bool): If True and the target file exists, appends current
                rows to the existing JSON data and removes duplicate rows before
                writing. Defaults to False.
            force_ascii (bool): Whether to escape non-ASCII characters in the
                output. Defaults to False.
            indent (int): Indentation width used when writing to disk. Defaults
                to 4.

        Returns:
            None
        """
        file_path = os.fspath(file_path).strip() if file_path is not None else ""

        if not file_path:
            raise ValueError("file_path cannot be empty.")

        export_df = self.copy()

        for col in export_df.select_dtypes(include = ['datetime', 'datetimetz']).columns:
            export_df[col] = export_df[col].dt.strftime('%Y-%m-%d %H:%M:%S')

        is_append_successful = False
        new_rows_count = len(export_df)

        duplicates_removed = 0

        if os.path.exists(file_path):
            if append:
                try:
                    existing_df = pd.read_json(file_path, orient = orient)

                except ValueError as e:
                    # Overwriting here would silently destroy the existing file.
                    raise ValueError(
                        f"Cannot append to '{file_path}': it is not readable as orient='{orient}' JSON."
                    ) from e

                combined_df = pd.concat([existing_df, export_df], ignore_index = True)

                initial_count = len(combined_df)

                try:
                    duplicate_mask = combined_df.duplicated()

                except TypeError:
                    # Lists and dicts are unhashable, so compare object columns by a JSON key.
                    dedupe_view = combined_df.copy(deep = False)

                    for col in dedupe_view.select_dtypes(include = 'object').columns:
                        dedupe_view[col] = dedupe_view[col].map(_dedupe_key)

                    duplicate_mask = dedupe_view.duplicated()

                combined_df = combined_df[~duplicate_mask].reset_index(drop = True)
                duplicates_removed = initial_count - len(combined_df)

                export_df = combined_df
                is_append_successful = True

            else:
                try:
                    json_str = export_df.to_json(orient = orient, force_ascii = force_ascii)
                    current_data = json.loads(json_str)

                    with open(file_path, encoding = 'utf-8') as f:
                        existing_data = json.load(f)

                    if current_data == existing_data:
                        logger.info("[SKIP] No changes detected. '%s' is already up to date.", os.path.basename(file_path))

                        return None

                except Exception as e:
                    logger.warning("Could not read existing file for comparison (%s). Overwriting...", e)

        output_dir = os.path.dirname(file_path)

        if output_dir:
            os.makedirs(output_dir, exist_ok = True)

        export_df.to_json(file_path, orient = orient, force_ascii = force_ascii, indent = indent)

        if is_append_successful:
            logger.info("[APPEND] Added %s new rows. Removed %s duplicates. Total jobs: %s.", new_rows_count, duplicates_removed, len(export_df))

        else:
            logger.info("[OVERWRITE] Save completed successfully! Total rows: %s.", len(export_df))

        return None


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
        """
        Save the dataframe to a SQL database table using SQLAlchemy.

        Notes:
            The method supports standard SQLAlchemy URL styles and includes
            dedicated handling for SQL Server drivers and SQLite paths. For
            SQL Server, `database_driver` is required and is URL-encoded.
            For SQLite, only `database_name` is required.

        Args:
            table_name (str): The name of the SQL table to write to.
            database_type (str): SQLAlchemy dialect/driver, for example
                `"postgresql+psycopg2"`, `"mysql+pymysql"`, `"mssql+pyodbc"`,
                or `"sqlite"`.
            database_user (str): Database username.
            database_password (str): Database password.
            database_host (str): Database host address.
            database_port (str): Database port.
            database_name (str): Database/schema name, or SQLite file path.
            database_driver (str | None): ODBC driver name for SQL Server
                dialects. Defaults to None.
            columns_types (dict | None): Optional SQLAlchemy dtype mapping to
                control target SQL column types. Defaults to None.
            chunk_size (int | None): Optional number of rows per write chunk.
                Defaults to None (single batch).
            fast_executemany (bool): Enables `fast_executemany` for SQL Server
                pyodbc connections when supported. Defaults to False.
            if_exists (str): How to behave if the table already exists.
                Options: 'fail', 'replace', 'append'. Defaults to 'append'.
            index (bool): Whether to write the DataFrame index as a SQL column.
                Defaults to False.

        Returns:
            None

        Example:
        ```
            df.save_to_sql(
                table_name="cleaned_search_data",
                database_type="postgresql+psycopg2",
                database_user="postgres",
                database_password="secret",
                database_host="localhost",
                database_port="5432",
                database_name="analytics_db",
                if_exists="replace",
                index=False
            )
        ```
        """

        db_type = str(database_type or '').strip().lower()
        db_user = str(database_user or '').strip()
        db_pass = str(database_password or '').strip()
        db_host = str(database_host or '').strip()
        db_port = str(database_port or '').strip()
        db_name = str(database_name or '').strip()
        db_driver = str(database_driver or '').strip() or None
        table_name = str(table_name or '').strip()
        engine_kwargs = {}

        if not table_name:
            raise ValueError("table_name cannot be empty.")

        if if_exists not in ['fail', 'replace', 'append']:
            raise ValueError(f"Invalid if_exists value '{if_exists}'. Use 'fail', 'replace', or 'append'.")

        if not db_type:
            raise ValueError("database_type cannot be empty.")

        if db_type.startswith('sqlite'):
            if not db_name:
                raise ValueError("database_name is required for SQLite.")

            if db_name == ':memory:':
                engine_url = "sqlite:///:memory:"

            else:
                engine_url = f"sqlite:///{db_name}"

        elif 'mssql' in db_type or 'sqlserver' in db_type:
            if not all([db_user, db_pass, db_host, db_port, db_name]):
                raise ValueError("SQL Server needs database_user, database_password, database_host, database_port, and database_name.")

            if not db_driver:
                raise ValueError("database_driver is required for SQL Server.")

            encoded_driver = urllib.parse.quote_plus(db_driver)
            encoded_user = urllib.parse.quote_plus(db_user)
            encoded_pass = urllib.parse.quote_plus(db_pass)

            engine_url = f"{db_type}://{encoded_user}:{encoded_pass}@{db_host}:{db_port}/{db_name}?driver={encoded_driver}"
            engine_kwargs['fast_executemany'] = fast_executemany

        else:

            if not all([db_user, db_pass, db_host, db_port, db_name]):
                raise ValueError("database_user, database_password, database_host, database_port, and database_name are required.")

            encoded_user = urllib.parse.quote_plus(db_user)
            encoded_pass = urllib.parse.quote_plus(db_pass)

            engine_url = f"{db_type}://{encoded_user}:{encoded_pass}@{db_host}:{db_port}/{db_name}"

        sqlalchemy = _import_optional("sqlalchemy", "SQLAlchemy", "save_to_sql")

        export_df = self.copy()

        # Normalize timezone-aware datetimes for SQL drivers that do not accept timezone-aware pandas dtypes.
        for col in export_df.select_dtypes(include = ['datetimetz']).columns:
            export_df[col] = export_df[col].dt.tz_localize(None)

        engine = sqlalchemy.create_engine(engine_url, **engine_kwargs)

        try:
            logger.info("Pushing data to SQL table '%s' at %s...", table_name, db_host)

            export_df.to_sql(
                table_name,
                con = engine,
                if_exists = if_exists,
                index = index,
                dtype = columns_types,
                chunksize = chunk_size if chunk_size else None
            )

            logger.info("Database save completed successfully!")

        finally:
            engine.dispose()

        return None



class JsonFile(BaseData):
    def __init__(
        self,
        data = None,
        json_file_path: str = None,
        key: str = None,
        cache_dir: str | os.PathLike | None = None,
        *args,
        **kwargs
    ):
        if data is not None and json_file_path is None:
            if isinstance(data, (str, os.PathLike)):
                path = os.fspath(data)

                if os.path.isfile(path):
                    json_file_path = path
                    data = None

                else:
                    raise FileNotFoundError(f"JSON file not found: {path}")

        if data is None and json_file_path is not None:
            path = os.fspath(json_file_path)

            if not os.path.isfile(path):
                raise FileNotFoundError(f"JSON file not found: {path}")

            with open(path, encoding = "utf-8") as file:
                raw_data = json.load(file)

            if key:
                if not isinstance(raw_data, dict):
                    raise TypeError("A JSON key can only be selected from a dictionary payload.")

                if key not in raw_data:
                    raise KeyError(f"Key '{key}' was not found in JSON payload.")

                raw_data = raw_data[key]

            data = pd.json_normalize(raw_data)

        super().__init__(data = data, cache_dir = cache_dir, *args, **kwargs)


class CsvFile(BaseData):
    def __init__(
        self,
        data = None,
        csv_file_path: str = None,
        cache_dir: str | os.PathLike | None = None,
        read_kwargs: dict | None = None,
        *args,
        **kwargs
    ):
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
            path = os.fspath(csv_file_path)

            if not os.path.isfile(path):
                raise FileNotFoundError(f"CSV file not found: {path}")

            data = pd.read_csv(path, **read_kwargs)

        super().__init__(data = data, cache_dir = cache_dir, *args, **kwargs)
