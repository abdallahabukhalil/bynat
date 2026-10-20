import ast
import inspect
import textwrap

import pandas as pd
import pytest

from bynat.cleaning import BaseData, CsvFile, JsonFile
from bynat.cleaning.Cleaning import (
    _HIERARCHICAL_STATS_COLUMNS,
    _convert_output,
    _validate_output_type,
)

OUTPUT_COLUMNS = [
    "hierarchy_level",
    "hierarchy_columns",
    "group_key",
    "target_column",
    "strategy",
    "statistic",
]


def test_get_hierarchical_stats_returns_median_levels_and_preserves_source(tmp_path):
    frame = BaseData(
        {
            "region": ["East", "East", "East", "West", None, None],
            "department": ["A", "A", "B", "A", "A", None],
            "score": [1, None, 5, 3, None, 7],
        },
        cache_dir=tmp_path,
    )
    original = frame.copy(deep=True)
    original_columns = frame.columns.copy()
    original_index = frame.index.copy()

    result = frame.get_hierarchical_stats(
        "score", strategy="median", hierarchy_cols=["region", "department"]
    )

    assert type(result) is BaseData
    assert list(result.columns) == OUTPUT_COLUMNS
    assert result["target_column"].unique().tolist() == ["score"]
    assert result["strategy"].unique().tolist() == ["median"]
    assert result["hierarchy_level"].tolist() == [2, 2, 2, 2, 2, 1, 1, 1]
    assert result["hierarchy_columns"].tolist() == [
        ("region", "department"),
        ("region", "department"),
        ("region", "department"),
        ("region", "department"),
        ("region", "department"),
        ("region",),
        ("region",),
        ("region",),
    ]
    assert result["group_key"].tolist()[:3] == [
        ("East", "A"),
        ("East", "B"),
        ("West", "A"),
    ]
    assert pd.isna(result.iloc[3]["group_key"][0])
    assert result.iloc[3]["group_key"][1] == "A"
    assert pd.isna(result.iloc[4]["group_key"][0])
    assert pd.isna(result.iloc[4]["group_key"][1])
    assert result["statistic"].tolist()[:3] == [1.0, 5.0, 3.0]
    assert pd.isna(result.iloc[3]["statistic"])
    assert result.iloc[4]["statistic"] == 7.0
    assert result["statistic"].tolist()[5:] == [3.0, 3.0, 7.0]
    pd.testing.assert_frame_equal(frame, original)
    assert frame.columns.equals(original_columns)
    assert frame.index.equals(original_index)


def test_get_hierarchical_stats_supports_mean_multiple_targets_and_string_hierarchy(tmp_path):
    frame = BaseData(
        {
            "group": ["a", "a", "b"],
            "first": [1, None, 5],
            "second": [10, 20, None],
        },
        cache_dir=tmp_path,
    )

    result = frame.get_hierarchical_stats(
        "first", "second", strategy="mean", hierarchy_cols="group"
    )

    assert list(result.columns) == OUTPUT_COLUMNS
    assert result["hierarchy_level"].tolist() == [1, 1, 1, 1]
    assert result["hierarchy_columns"].tolist() == [("group",)] * 4
    assert result["group_key"].tolist() == [("a",), ("b",), ("a",), ("b",)]
    assert result["target_column"].tolist() == ["first", "first", "second", "second"]
    assert result["strategy"].tolist() == ["mean"] * 4
    assert result["statistic"].tolist()[:3] == [1.0, 5.0, 15.0]
    assert pd.isna(result.iloc[3]["statistic"])


def test_get_hierarchical_stats_mode_matches_tie_breaking_and_normalizes_mod(tmp_path):
    frame = BaseData(
        {
            "group": ["x", "x", "x", "y", "y", None],
            "label": ["b", "a", None, "c", "c", "z"],
        },
        cache_dir=tmp_path,
    )

    result = frame.get_hierarchical_stats("label", strategy="mod", hierarchy_cols="group")
    direct_mode = frame.get_hierarchical_stats(
        "label", strategy="mode", hierarchy_cols="group"
    )

    assert result["strategy"].tolist() == ["mode"] * 3
    pd.testing.assert_frame_equal(direct_mode, result)
    assert result["group_key"].tolist()[:2] == [("x",), ("y",)]
    assert pd.isna(result.iloc[2]["group_key"][0])
    assert result["statistic"].tolist() == ["a", "c", "z"]


def test_get_hierarchical_stats_handles_null_values_and_empty_groups(tmp_path):
    frame = BaseData(
        {
            "group": ["filled", "empty", None],
            "value": [4, None, None],
        },
        cache_dir=tmp_path,
    )

    result = frame.get_hierarchical_stats("value", hierarchy_cols="group")

    assert result["group_key"].tolist()[:2] == [("empty",), ("filled",)]
    assert pd.isna(result.iloc[2]["group_key"][0])
    assert pd.isna(result.iloc[0]["statistic"])
    assert result.iloc[1]["statistic"] == 4.0
    assert pd.isna(result.iloc[2]["statistic"])


def test_get_hierarchical_stats_returns_fixed_empty_schema_for_invalid_inputs(tmp_path, capsys):
    frame = BaseData({"group": ["a"], "value": [1]}, cache_dir=tmp_path)

    no_target = frame.get_hierarchical_stats("missing", hierarchy_cols="group")
    assert list(no_target.columns) == OUTPUT_COLUMNS
    assert no_target.empty
    assert capsys.readouterr().out == (
        "Warning: No valid target columns found for statistics extraction.\n"
    )

    invalid_strategy = frame.get_hierarchical_stats(
        "value", strategy="sum", hierarchy_cols="group"
    )
    assert list(invalid_strategy.columns) == OUTPUT_COLUMNS
    assert invalid_strategy.empty
    assert "Invalid strategy 'sum'" in capsys.readouterr().out

    no_hierarchy = frame.get_hierarchical_stats("value", hierarchy_cols="missing")
    assert list(no_hierarchy.columns) == OUTPUT_COLUMNS
    assert no_hierarchy.empty

    empty_frame = BaseData({"group": [], "value": []}, cache_dir=tmp_path)
    empty_result = empty_frame.get_hierarchical_stats("value", hierarchy_cols="group")
    assert list(empty_result.columns) == OUTPUT_COLUMNS
    assert empty_result.empty


def test_get_hierarchical_stats_does_not_change_fill_missing_behavior(tmp_path):
    frame = BaseData(
        {
            "region": ["East", "East", "West"],
            "score": [1, None, 3],
        },
        cache_dir=tmp_path,
    )

    extracted = frame.get_hierarchical_stats("score", hierarchy_cols="region")
    imputed = frame.fill_missing("score", hierarchy_cols="region")

    assert extracted["statistic"].tolist() == [1.0, 3.0]
    assert imputed["score"].tolist() == [1.0, 1.0, 3.0]
    assert frame["score"].iloc[0] == 1.0
    assert pd.isna(frame["score"].iloc[1])
    assert frame["score"].iloc[2] == 3.0


def test_get_hierarchical_stats_supports_explicit_dataframe_and_dict_outputs(tmp_path):
    frame = BaseData(
        {
            "region": ["East", "East", "West"],
            "score": [1, 3, 5],
        },
        cache_dir=tmp_path,
    )

    dataframe_result = frame.get_hierarchical_stats(
        "score",
        strategy="mean",
        hierarchy_cols="region",
        output_type=pd.DataFrame,
    )
    dict_result = frame.get_hierarchical_stats(
        "score",
        strategy="mean",
        hierarchy_cols="region",
        output_type=dict,
    )

    assert type(dataframe_result) is pd.DataFrame
    assert list(dataframe_result.columns) == OUTPUT_COLUMNS
    assert list(dict_result) == OUTPUT_COLUMNS
    assert dict_result["hierarchy_level"] == [1, 1]
    assert dict_result["hierarchy_columns"] == [("region",), ("region",)]
    assert dict_result["group_key"] == [("East",), ("West",)]
    assert dict_result["target_column"] == ["score", "score"]
    assert dict_result["strategy"] == ["mean", "mean"]
    assert dict_result["statistic"] == [2.0, 5.0]


@pytest.mark.parametrize("output_type", [JsonFile, CsvFile])
def test_get_hierarchical_stats_supports_explicit_basedata_family_outputs(
    tmp_path, output_type
):
    frame = BaseData(
        {"group": ["a", "b"], "value": [1, 2]},
        cache_dir=tmp_path,
    )

    result = frame.get_hierarchical_stats(
        "value",
        hierarchy_cols="group",
        output_type=output_type,
    )

    assert type(result) is output_type
    assert list(result.columns) == OUTPUT_COLUMNS
    assert result["group_key"].tolist() == [("a",), ("b",)]


def test_get_hierarchical_stats_preserves_custom_basedata_subclass_by_default(tmp_path):
    class CustomBaseData(BaseData):
        pass

    frame = CustomBaseData(
        {"group": ["a"], "value": [1]},
        cache_dir=tmp_path,
    )

    result = frame.get_hierarchical_stats("value", hierarchy_cols="group")

    assert type(result) is CustomBaseData
    assert list(result.columns) == OUTPUT_COLUMNS


def test_get_hierarchical_stats_rejects_invalid_output_type_before_aggregation(tmp_path):
    frame = BaseData({"group": ["a"], "value": [1]}, cache_dir=tmp_path)

    with pytest.raises(TypeError, match="output_type"):
        frame.get_hierarchical_stats(
            "missing",
            hierarchy_cols="group",
            output_type="records",
        )


def test_get_hierarchical_stats_returns_requested_type_for_empty_results(tmp_path):
    frame = BaseData({"group": ["a"], "value": [1]}, cache_dir=tmp_path)

    dataframe_result = frame.get_hierarchical_stats(
        "missing", hierarchy_cols="group", output_type=pd.DataFrame
    )
    dict_result = frame.get_hierarchical_stats(
        "missing", hierarchy_cols="group", output_type=dict
    )

    assert type(dataframe_result) is pd.DataFrame
    assert dataframe_result.empty
    assert dict_result == {column: [] for column in OUTPUT_COLUMNS}


def test_get_hierarchical_stats_has_no_nested_functions_or_apply_lambda(tmp_path):
    source = textwrap.dedent(inspect.getsource(BaseData.get_hierarchical_stats))
    tree = ast.parse(source)

    assert not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(tree)
        if node is not tree.body[0]
    )
    assert not any(isinstance(node, ast.Lambda) for node in ast.walk(tree))
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "apply"
        for node in ast.walk(tree)
    )


def test_get_hierarchical_stats_deduplicates_hierarchy_columns_without_adding_levels(
    tmp_path,
):
    frame = BaseData(
        {
            "region": ["East", "East", "West"],
            "department": ["A", "B", "A"],
            "value": [1, 3, 5],
        },
        cache_dir=tmp_path,
    )

    expected = frame.get_hierarchical_stats(
        "value", hierarchy_cols=["region", "department"]
    )
    duplicate_hierarchy = frame.get_hierarchical_stats(
        "value", hierarchy_cols=["region", "region", "department", "region"]
    )

    pd.testing.assert_frame_equal(duplicate_hierarchy, expected)
    assert duplicate_hierarchy["hierarchy_columns"].tolist() == [
        ("region", "department"),
        ("region", "department"),
        ("region", "department"),
        ("region",),
        ("region",),
    ]


@pytest.mark.parametrize("strategy", ["median", "mode"])
def test_get_hierarchical_stats_rejects_target_hierarchy_overlap_before_aggregation(
    monkeypatch, tmp_path, strategy
):
    frame = BaseData(
        {"group": ["a", "a"], "value": [1, 2]},
        cache_dir=tmp_path,
    )

    def unexpected_aggregation(*args, **kwargs):
        pytest.fail("aggregation should not run before input validation")

    monkeypatch.setattr(pd.DataFrame, "groupby", unexpected_aggregation)
    monkeypatch.setattr(pd.DataFrame, "melt", unexpected_aggregation)

    with pytest.raises(
        ValueError, match="target columns cannot overlap hierarchy columns"
    ):
        frame.get_hierarchical_stats(
            "group", strategy=strategy, hierarchy_cols=["group"]
        )


def test_get_hierarchical_stats_aggregates_all_mode_targets_once_per_level(
    monkeypatch, tmp_path
):
    frame = BaseData(
        {
            "region": ["East", "East", "West", "West"],
            "department": ["A", "B", "A", "B"],
            "first": ["x", "x", "y", "y"],
            "second": ["m", "m", "n", "n"],
        },
        cache_dir=tmp_path,
    )
    original_groupby = pd.DataFrame.groupby
    groupby_calls = []

    def counting_groupby(dataframe, *args, **kwargs):
        groupby_calls.append(dataframe)
        return original_groupby(dataframe, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "groupby", counting_groupby)

    result = frame.get_hierarchical_stats(
        "first",
        "second",
        strategy="mode",
        hierarchy_cols=["region", "department"],
    )

    assert len(groupby_calls) == 4
    assert result["target_column"].tolist() == ["first"] * 6 + ["second"] * 6


def test_get_hierarchical_stats_mode_allows_hierarchy_column_named_count(tmp_path):
    frame = BaseData(
        {
            "_count": ["a", "a", "b", "b"],
            "label": ["x", "x", "y", None],
        },
        cache_dir=tmp_path,
    )

    result = frame.get_hierarchical_stats(
        "label", strategy="mode", hierarchy_cols="_count"
    )

    assert result["group_key"].tolist() == [("a",), ("b",)]
    assert result["statistic"].tolist() == ["x", "y"]


def test_generic_output_helpers_are_schema_independent(tmp_path):
    data = pd.DataFrame({"alpha": [("x",)], "beta": [3]})

    assert isinstance(_HIERARCHICAL_STATS_COLUMNS, tuple)
    assert _convert_output(data, dict) == {
        "alpha": [("x",)],
        "beta": [3],
    }
    _validate_output_type(pd.DataFrame)
    _validate_output_type(dict)
    _validate_output_type(BaseData)

    with pytest.raises(TypeError, match="output_type"):
        _validate_output_type("records")


def test_get_hierarchical_stats_supports_ordered_multiple_strategies(tmp_path):
    frame = BaseData(
        {
            "group": ["a", "a", "b", "b"],
            "first": [1, 3, 5, 7],
            "second": [10, 10, 20, 30],
        },
        cache_dir=tmp_path,
    )

    result = frame.get_hierarchical_stats(
        "first",
        "second",
        strategy=["mode", "mean", "median"],
        hierarchy_cols="group",
    )

    assert list(result.columns) == OUTPUT_COLUMNS
    assert result["strategy"].tolist() == [
        "mode",
        "mode",
        "mode",
        "mode",
        "mean",
        "mean",
        "mean",
        "mean",
        "median",
        "median",
        "median",
        "median",
    ]
    assert result["target_column"].tolist() == [
        "first",
        "first",
        "second",
        "second",
        "first",
        "first",
        "second",
        "second",
        "first",
        "first",
        "second",
        "second",
    ]
    assert not result.duplicated(
        subset=["hierarchy_level", "hierarchy_columns", "group_key", "target_column", "strategy"]
    ).any()


def test_get_hierarchical_stats_rejects_non_string_strategy_values_before_aggregation(
    monkeypatch, tmp_path
):
    frame = BaseData({"group": ["a"], "value": [1]}, cache_dir=tmp_path)

    def unexpected_aggregation(*args, **kwargs):
        pytest.fail("aggregation should not run before strategy validation")

    monkeypatch.setattr(pd.DataFrame, "groupby", unexpected_aggregation)

    with pytest.raises(TypeError, match="strategy"):
        frame.get_hierarchical_stats("value", strategy=["mean", 1], hierarchy_cols="group")


def test_get_hierarchical_stats_mode_has_no_melt_and_keeps_null_groups(tmp_path, monkeypatch):
    frame = BaseData(
        {
            "group": ["a"] * 100 + ["b"] * 100 + [None],
            "label": ["x"] * 60 + ["y"] * 40 + ["z"] * 100 + [None],
        },
        cache_dir=tmp_path,
    )

    def unexpected_melt(*args, **kwargs):
        pytest.fail("mode aggregation must not materialize a melt intermediate")

    monkeypatch.setattr(pd.DataFrame, "melt", unexpected_melt)
    result = frame.get_hierarchical_stats("label", strategy="mode", hierarchy_cols="group")

    assert result["group_key"].tolist() == [("a",), ("b",)]
    assert result["statistic"].tolist() == ["x", "z"]


def test_get_hierarchical_stats_default_fallback_requires_data_signature(tmp_path):
    class NoDataParameter(BaseData):
        def __init__(self, frame, **kwargs):
            super().__init__(data=frame, **kwargs)

    frame = NoDataParameter(
        {"group": ["a", "a", "b"], "value": [1, None, 3]},
        cache_dir=tmp_path,
    )

    result = frame.get_hierarchical_stats("value", hierarchy_cols="group")

    assert type(result) is pd.DataFrame
    assert result["statistic"].tolist() == [1.0, 3.0]


def test_get_hierarchical_stats_propagates_internal_constructor_errors(tmp_path):
    class ConstructorFailure(BaseData):
        def __init__(self, data=None, *args, **kwargs):
            if isinstance(data, pd.DataFrame):
                raise TypeError("constructor internal failure")
            super().__init__(data=data, *args, **kwargs)

    frame = ConstructorFailure(
        {"group": ["a", "a", "b"], "value": [1, None, 3]},
        cache_dir=tmp_path,
    )

    with pytest.raises(TypeError, match="constructor internal failure"):
        frame.get_hierarchical_stats("value", hierarchy_cols="group")


def test_get_hierarchical_stats_all_output_types_keep_multi_strategy_schema(tmp_path):
    frame = BaseData({"group": ["a", "b"], "value": [1, 2]}, cache_dir=tmp_path)

    for output_type in (pd.DataFrame, dict, BaseData, JsonFile, CsvFile):
        result = frame.get_hierarchical_stats(
            "value",
            strategy=["mean", "median"],
            hierarchy_cols="group",
            output_type=output_type,
        )
        if output_type is dict:
            assert list(result) == OUTPUT_COLUMNS
            assert result["strategy"] == ["mean", "mean", "median", "median"]
        else:
            assert type(result) is output_type
            assert list(result.columns) == OUTPUT_COLUMNS
            assert result["strategy"].tolist() == ["mean", "mean", "median", "median"]


def test_get_hierarchical_stats_mode_empty_valid_values_returns_empty_rows(tmp_path):
    frame = BaseData(
        {"group": ["a", "b", None], "label": [None, None, None]},
        cache_dir=tmp_path,
    )

    result = frame.get_hierarchical_stats("label", strategy="mode", hierarchy_cols="group")

    assert result.empty
    assert list(result.columns) == OUTPUT_COLUMNS


def test_get_hierarchical_stats_falls_back_to_dataframe_for_incompatible_default_subclass(
    tmp_path,
):
    class NoIntermediateConstruction(BaseData):
        def __init__(self, frame, **kwargs):
            super().__init__(data=frame, **kwargs)

    frame = NoIntermediateConstruction(
        {"group": ["a", "a", "b"], "value": [1, None, 3]},
        cache_dir=tmp_path,
    )

    result = frame.get_hierarchical_stats("value", hierarchy_cols="group")

    assert type(result) is pd.DataFrame
    assert result["group_key"].tolist() == [("a",), ("b",)]
    assert result["statistic"].tolist() == [1.0, 3.0]
