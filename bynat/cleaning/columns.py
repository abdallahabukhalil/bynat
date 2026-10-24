"""Column renaming, dropping, reordering, moving, and reshaping helpers."""

from collections.abc import Callable
from typing import Any, cast

import pandas as pd

from ._common import FrameT, _like


def drop_by_substring(df: FrameT, substring: str) -> FrameT:
    """
    Drop all columns that contain a specific substring within their names.

    Args:
        substring (str): The text to search for; any column containing this
            string will be removed.

    Returns:
        A new dataframe without the matching columns.

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

    columns_to_drop = [col for col in df.columns if substring in col]

    if not columns_to_drop:
        return df.copy()

    return df.drop(columns = columns_to_drop)


def drop_CLASS_columns(df: FrameT) -> FrameT:
    """
    Drop all columns that contain the substring "__CLASS__" from the data.

    Returns:
        A new dataframe without the matching columns.

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

    return drop_by_substring(df, "__CLASS__")


def rename_by_substring(df: FrameT, old_substring: str, new_substring: str) -> FrameT:
    """
    Rename columns by replacing a specific substring with a new one.

    Args:
        old_substring (str): The text to search for within the column names.
        new_substring (str): The text to replace the old substring with.

    Returns:
        A new dataframe with renamed columns.

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
        for col in df.columns
        if old_substring in col
    }

    if not columns_to_rename:
        return df.copy()

    return df.rename(columns = columns_to_rename)


def rename_all_DOTS(df: FrameT, unified_separator: str = "_") -> FrameT:
    """
    Rename columns by replacing all occurrences of dots ('.') with a unified separator.

    Args:
        unified_separator (str): The new string to replace dots with.
            Defaults to "_".

    Returns:
        A new dataframe with renamed columns.

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

    return rename_by_substring(df, ".", unified_separator)


def reorder_columns(df: FrameT, *first_cols: Any) -> FrameT:
    """
    Reorder the dataframe by moving a specified list of columns to the front.

    Any columns not mentioned in `first_cols` will be appended to the end
    preserving their original relative order.

    Args:
        first_cols (tuple): Column names that should appear first.

    Returns:
        A new dataframe with reordered columns.

    Example:
    ```
        Initial Columns: ['C', 'A', 'D', 'B']

        Operation: reorder_columns('A', 'B')
        Result:    ['A', 'B', 'C', 'D']

        Usage:
            df.reorder_columns("id", "timestamp")
    ```
    """

    ordered = [col for col in first_cols if col in df.columns]

    if not ordered:
        return df.copy()

    final_order = ordered + [col for col in df.columns if col not in ordered]

    return df[final_order]


def melt_transform_pivot(
    df: FrameT,
    id_vars: list | str,
    value_vars: list | str | None = None,
    transform_func: Callable | None = None,
    aggfunc: str | Callable = 'first',
    var_name: str = 'variable_name',
    value_name: str = 'variable_value',
    pivot_index: list | str | None = None,
    pivot_columns: list | str | None = None,
    **kwargs: Any
) -> FrameT:
    """
    Reshape selected columns to long format, transform their values, and
    pivot them back to wide format.

    Notes:
        This helper is useful when the same vectorized operation should be
        applied to several value columns through one temporary long-form
        series. Duplicate identifier/value-column pairs are resolved with
        `aggfunc`, so the output can have fewer rows than the input and has a
        fresh RangeIndex. By default, the method pivots back using `id_vars`
        as rows and the melted variable column (`var_name`) as output columns.
        Use `pivot_index` and `pivot_columns` only when you want a different
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
        **kwargs: Additional keyword arguments to pass to `transform_func`.

    Returns:
        A new, reshaped dataframe.

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

    missing_id_vars = [col for col in id_vars if col not in df.columns]
    if missing_id_vars:
        raise ValueError(f"id_vars not found in dataframe: {missing_id_vars}")

    if value_vars is None:
        value_vars = [col for col in df.columns if col not in id_vars]

    duplicate_value_vars = pd.Index(value_vars)
    if duplicate_value_vars.has_duplicates:
        duplicates = duplicate_value_vars[duplicate_value_vars.duplicated()].unique().tolist()
        raise ValueError(f"value_vars contains duplicate columns: {duplicates}")

    missing_value_vars = [col for col in value_vars if col not in df.columns]
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

    if value_name in df.columns:
        raise ValueError("value_name cannot match an existing dataframe column name")

    if not isinstance(aggfunc, str) and not callable(aggfunc):
        raise TypeError("aggfunc must be a string or callable")

    if transform_func is not None and not callable(transform_func):
        raise TypeError("transform_func must be callable when provided")

    melted = pd.DataFrame(df).melt(
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

    actual_pivot_columns: str | list
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
            # pandas-stubs lists only a subset of the accepted aggregation names.
            aggfunc = cast(Any, aggfunc)
        ).reset_index()

    except Exception as e:
        raise ValueError(f"Could not pivot melted dataframe with the provided arguments: {e}") from e

    pivoted.columns.name = None

    return _like(df, pivoted)


def move_column(df: FrameT, col_to_move: str, col_target: str, position: str = "after") -> FrameT:
    """
    Move a specific column to a new position relative to a target column.

    Args:
        col_to_move (str): The name of the column you want to relocate.
        col_target (str): The reference column where the move will happen.
        position (str): Whether to place the column "after" or "before" the
            target column. Defaults to "after".

    Returns:
        A new dataframe with the column moved.

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

    if col_to_move not in df.columns or col_target not in df.columns:
        return df.copy()

    if position not in ["after", "before"]:
        raise ValueError("position parameter must be 'after' or 'before'")

    cols = df.columns.to_list()
    cols.remove(col_to_move)
    target_index = cols.index(col_target)

    insert_index = target_index + 1 if position == "after" else target_index

    cols.insert(insert_index, col_to_move)
    return df[cols]


def align_lat_lng(df: FrameT) -> FrameT:
    """
    Standardize the coordinate order by moving the 'longitude' column
    immediately after the 'latitude' column.

    .. note::
    This method strictly expects the column names to be exactly **'latitude'**
    and **'longitude'** (case-sensitive). If the columns are named differently
    (e.g., 'lat' or 'LONG'), the alignment will not occur.

    Returns:
        A new dataframe with aligned coordinate columns.

    Example:
    ```
        Column Reordering:
            ['longitude', 'id', 'latitude']  ->  ['id', 'latitude', 'longitude']

        Usage:
            df.align_lat_lng()
    ```
    """

    return move_column(df, col_to_move = "longitude", col_target = "latitude", position = "after")


def split_list_objects(
    df: FrameT,
    source_col: str,
    remove_source_col: bool = False,
    explode_rows: bool = False,
    cartesian_explode: bool = False,
    **target_cols_and_indices: Any
) -> FrameT:
    """
    Extract specific elements from lists stored within a column into new columns.

    This method allows you to pull items out of list-like objects by their index.
    It can either join multiple indices into a string, explode them into parallel
    separate rows, or perform a sequential cartesian product explosion.

    Notes:
        - **String Joining**: When providing a list of indices with
          `explode_rows=False`, valid extracted elements are joined
          into a single comma-separated string (NaNs are ignored).
        - **Cartesian vs. Parallel Explode**: When exploding multiple columns,
          `cartesian_explode=False` (default) explodes them in parallel (requires
          lists to be of equal length). `cartesian_explode=True` explodes them
          sequentially, creating a cross-join (cartesian product) of all elements.
        - Exploding rows changes the row count and resets the index.

    Args:
        source_col (str): The name of the column containing list-like objects.
        remove_source_col (bool): If True, deletes the original source column after
            extraction. Defaults to False.
        explode_rows (bool): If True, and an index list is provided, expands the
            dataframe so each extracted element gets its own row. Defaults to False.
        cartesian_explode (bool): If True and multiple target columns are exploded,
            performs a sequential explode resulting in a cartesian product. Defaults to False.
        **target_cols_and_indices: Keyword arguments where the key is the new
            column name and the value is the index (int) or list of indices (list)
            to extract from the source list.

    Returns:
        A new dataframe with the extracted columns.

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
            df.split_list_objects("coordinates", remove_source_col=True, lat=[0], long=[1])

            # Extract and explode in parallel
            df.split_list_objects("skills", explode_rows=True, tech=[0, 1], soft=[2, 3])

            # Extract and explode generating all combinations (Cartesian Product)
            df.split_list_objects("attributes", explode_rows=True, cartesian_explode=True, colors=[0, 1], sizes=[2, 3])
    ```
    """

    if source_col not in df.columns:
        return df.copy()

    result = df.copy()

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
        result = result.drop(columns = [source_col])

    return result
