"""Hierarchical outlier handling, imputation, and statistics helpers."""

from collections.abc import Iterable, Iterator, Sequence
from typing import Any

import numpy as np
import pandas as pd

from ._common import (
    _HIERARCHICAL_STATS_COLUMNS,
    FrameT,
    _convert_default_output,
    _convert_output,
    _left_merge_aligned,
    _validate_output_type,
    logger,
)


def _hierarchical_outlier_masks(
    frame: pd.DataFrame,
    target_cols: Sequence[Any],
    hierarchy_cols: Sequence[Any],
    min_samples: int,
) -> Iterator[tuple[Any, np.ndarray]]:
    """
    Yield `(target_col, outlier_mask)` for every target and hierarchy level.

    The full hierarchy is evaluated first, then its last column is dropped at
    each level. A row is judged only at the most specific level where its
    group has at least `min_samples` non-null values; values outside the
    group's 1.5 IQR bounds are flagged. Quartiles come from one grouped
    `quantile([0.25, 0.75])` call per level and are mapped back to rows by
    group number, with no merge.

    `frame` is re-read at every level, so a caller that blanks the yielded
    outliers before resuming excludes them from later levels
    (`handle_outliers`); a caller that leaves `frame` untouched keeps them in
    later quartiles (`drop_outliers`).
    """
    row_count = len(frame)

    for target_col in target_cols:
        if target_col not in frame.columns:
            continue

        processed = np.zeros(row_count, dtype = bool)
        current_hierarchy = list(hierarchy_cols)

        while current_hierarchy:
            values = frame[target_col]
            unprocessed = ~processed & values.notna().to_numpy()

            if not unprocessed.any():
                break

            grouped = values.groupby(
                [frame[col] for col in current_hierarchy],
                dropna = False,
                observed = True,
                sort = False,
            )

            group_ids = grouped.ngroup().to_numpy()
            valid = grouped.count().to_numpy()[group_ids] >= min_samples

            if valid.any():
                quartiles = grouped.quantile([0.25, 0.75]).to_numpy(dtype = float).reshape(-1, 2)
                q1 = quartiles[group_ids, 0]
                q3 = quartiles[group_ids, 1]
                iqr = q3 - q1

                lower_bound = q1 - 1.5 * iqr
                upper_bound = q3 + 1.5 * iqr
                numeric = values.to_numpy(dtype = float, na_value = np.nan)

                with np.errstate(invalid = 'ignore'):
                    outliers = (
                        valid & unprocessed & ~np.isnan(lower_bound) &
                        ((numeric < lower_bound) | (numeric > upper_bound))
                    )

                yield target_col, outliers

            processed |= valid
            current_hierarchy.pop()


def _normalize_hierarchy(hierarchy_cols: Any) -> list:
    """Return the hierarchy as a list, treating one string as one column."""
    if isinstance(hierarchy_cols, str):
        return [hierarchy_cols]

    return list(hierarchy_cols) if hierarchy_cols else []


def handle_outliers(
    df: FrameT,
    *target_cols: Any,
    hierarchy_cols: list,
    min_samples: int = 5,
    get_hierarchical_stats: bool = False
) -> Any:
    """
    Replace outlier values with missing values using hierarchical IQR rules.

    Notes:
        The method starts with the full grouping hierarchy and progressively
        relaxes it by removing the last grouping column at each pass. Within
        every valid group, values outside the 1.5 IQR bounds are converted
        to `NaN`. Values replaced at one level are excluded from the
        quartiles of later levels.

    Args:
        *target_cols: Numeric columns to evaluate for outliers.
        hierarchy_cols (list): Ordered grouping columns used to define peer
            groups.
        min_samples (int): Minimum number of rows required for a group to be
            considered valid. Defaults to 5.
        get_hierarchical_stats (bool): If True, also return the existing
            hierarchical median statistics computed from the original data
            before outliers are replaced. Defaults to False.

    Returns:
        A new cleaned dataframe, or `(cleaned_dataframe, statistics)` when
        `get_hierarchical_stats=True`.
    """

    hierarchical_stats = (
        _compute_hierarchical_stats(
            df,
            *target_cols,
            hierarchy_cols=hierarchy_cols,
        )
        if get_hierarchical_stats
        else None
    )

    result = df.copy()

    if not hierarchy_cols:
        logger.warning("No hierarchy columns provided.")
    else:
        masks = _hierarchical_outlier_masks(
            result, target_cols, _normalize_hierarchy(hierarchy_cols), min_samples
        )

        for target_col, outliers in masks:
            if outliers.any():
                result.loc[outliers, target_col] = np.nan

    if get_hierarchical_stats:
        return result, hierarchical_stats
    return result


def drop_outliers(
    df: FrameT,
    *target_cols: Any,
    hierarchy_cols: list,
    min_samples: int = 5,
    get_hierarchical_stats: bool = False
) -> Any:
    """
    Remove rows that contain outlier values based on hierarchical IQR rules.

    Notes:
        This method shares the same grouping logic as `handle_outliers`, but
        instead of replacing detected outliers with `NaN`, it drops the
        affected rows after scanning all requested target columns. Rows are
        dropped by position, so a duplicated index label never removes its
        twin row.

    Args:
        *target_cols: Numeric columns to inspect for outliers.
        hierarchy_cols (list): Ordered grouping columns used to define peer
            groups.
        min_samples (int): Minimum number of rows required for a group to be
            considered valid. Defaults to 5.
        get_hierarchical_stats (bool): If True, also return the existing
            hierarchical median statistics computed from the original data
            before outlier rows are dropped. Defaults to False.

    Returns:
        A new filtered dataframe, or `(filtered_dataframe, statistics)` when
        `get_hierarchical_stats=True`.
    """

    hierarchical_stats = (
        _compute_hierarchical_stats(
            df,
            *target_cols,
            hierarchy_cols=hierarchy_cols,
        )
        if get_hierarchical_stats
        else None
    )

    result = df.copy()

    if not hierarchy_cols:
        logger.warning("No hierarchy columns provided.")
    else:
        drop_mask = np.zeros(len(result), dtype = bool)
        masks = _hierarchical_outlier_masks(
            result, target_cols, _normalize_hierarchy(hierarchy_cols), min_samples
        )

        for _, outliers in masks:
            drop_mask |= outliers

        if drop_mask.any():
            result = result.iloc[np.flatnonzero(~drop_mask)]

    if get_hierarchical_stats:
        return result, hierarchical_stats
    return result


def fill_missing(
    df: FrameT,
    *target_cols: Any,
    strategy: str = 'median',
    hierarchy_cols: list | str | None = None,
    get_hierarchical_stats: bool = False
) -> Any:
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
        get_hierarchical_stats (bool): If True, also return the existing
            hierarchical statistics computed from the original data before
            imputation. The requested `strategy` is used. Defaults to
            False.

    Returns:
        A new imputed dataframe, or `(imputed_dataframe, statistics)` when
        `get_hierarchical_stats=True`.

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
    result = df.copy()

    valid_cols = [col for col in target_cols if col in result.columns]
    if not valid_cols:
        logger.warning("No valid target columns found for imputation.")
        if get_hierarchical_stats:
            hierarchical_stats = _compute_hierarchical_stats(
                df,
                *target_cols,
                strategy=strategy,
                hierarchy_cols=hierarchy_cols,
            )
            return result, hierarchical_stats
        return result

    strategy = str(strategy).lower()
    if strategy not in ['median', 'mean', 'mode', 'mod']:
        raise ValueError(
            f"Invalid strategy '{strategy}'. Use 'median', 'mean', or 'mode'."
        )

    if isinstance(hierarchy_cols, str):
        hierarchy_cols = [hierarchy_cols]

    valid_group = [col for col in hierarchy_cols if col in result.columns] if hierarchy_cols else []

    hierarchical_stats = (
        _compute_hierarchical_stats(
            df,
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
        return result, hierarchical_stats
    return result

def get_hierarchical_stats(
    df: pd.DataFrame,
    *target_cols: Any,
    strategy: str | Iterable[str] = 'median',
    hierarchy_cols: list | str | None = None,
    output_type: Any = None,
) -> Any:
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
    ``BaseData``-family type as ``df`` when its constructor signature can
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
            the type of ``df`` when supported, with a plain DataFrame
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
    resolved_output_type = type(df) if default_output else output_type
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

    valid_cols = list(dict.fromkeys(col for col in target_cols if col in df.columns))
    if not valid_cols:
        logger.warning("No valid target columns found for statistics extraction.")
        if default_output:
            return _convert_default_output(stats_df, resolved_output_type)
        return _convert_output(stats_df, resolved_output_type)

    if isinstance(hierarchy_cols, str):
        hierarchy_cols = [hierarchy_cols]
    valid_group = list(
        dict.fromkeys(col for col in hierarchy_cols if col in df.columns)
    ) if hierarchy_cols else []
    overlapping_cols = [col for col in valid_cols if col in valid_group]
    if overlapping_cols:
        raise ValueError(
            "target columns cannot overlap hierarchy columns: "
            f"{overlapping_cols}"
        )
    if not valid_group or df.empty:
        if default_output:
            return _convert_default_output(stats_df, resolved_output_type)
        return _convert_output(stats_df, resolved_output_type)

    source_df = pd.DataFrame(df)
    strategy_rows: dict[str, list[dict[str, Any]]] = {
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

# The hierarchy methods take a `get_hierarchical_stats` flag that shadows the
# function name inside their bodies.
_compute_hierarchical_stats = get_hierarchical_stats
