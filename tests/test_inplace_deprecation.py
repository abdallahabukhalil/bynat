"""API-02: transformations always return new objects; `inplace=True` is deprecated."""

import sys
import warnings

import pandas as pd
import pytest

from bynat.cleaning import BaseData


def _frame(tmp_path, data=None):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return BaseData(
            data if data is not None else {"id": [1, 2], "latitude": [1.0, 2.0], "name": ["a", "b"]},
            cache_dir=tmp_path,
        )


# Calls that take an early-exit path because nothing needs changing.
EARLY_EXITS = {
    "drop_by_substring": lambda frame: frame.drop_by_substring("missing"),
    "drop_CLASS_columns": lambda frame: frame.drop_CLASS_columns(),
    "rename_by_substring": lambda frame: frame.rename_by_substring("missing", "x"),
    "rename_all_DOTS": lambda frame: frame.rename_all_DOTS(),
    "reorder_columns": lambda frame: frame.reorder_columns("missing"),
    "move_column": lambda frame: frame.move_column("missing", "id"),
    "align_lat_lng": lambda frame: frame.align_lat_lng(),
    "extract_keywords": lambda frame: frame.extract_keywords("missing", found=["a"]),
    "split_list_objects": lambda frame: frame.split_list_objects("missing"),
    "impute_by_language": lambda frame: frame.impute_by_language("missing"),
    "translate_conditional_column": lambda frame: frame.translate_conditional_column("missing", "id", "name"),
    "translate_categorical_column": lambda frame: frame.translate_categorical_column("missing"),
    "truncate_after_substring": lambda frame: frame.truncate_after_substring(),
    "fill_missing_coordinates": lambda frame: frame.fill_missing_coordinates(lat_col="missing", long_col="lng"),
    "convert_to_usd": lambda frame: frame.convert_to_usd("id", country_col="missing", year_col="year"),
    "handle_outliers": lambda frame: frame.handle_outliers("id", hierarchy_cols=[]),
    "drop_outliers": lambda frame: frame.drop_outliers("id", hierarchy_cols=[]),
    "fill_missing": lambda frame: frame.fill_missing("missing"),
    "categorize_by_reference": lambda frame: frame.categorize_by_reference("missing", target_col="c", reference={}),
    "extract_entities_nlp": lambda frame: frame.extract_entities_nlp(
        "missing", lexicon_source={"skills": ["python"]}, skills="skills"
    ),
}


@pytest.mark.parametrize("method_name", sorted(EARLY_EXITS))
def test_early_exits_return_an_independent_copy(tmp_path, method_name):
    frame = _frame(tmp_path)
    original = frame.copy()

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        result = EARLY_EXITS[method_name](frame)

    assert result is not frame
    assert isinstance(result, BaseData)
    pd.testing.assert_frame_equal(pd.DataFrame(result), pd.DataFrame(original))

    result["id"] = [10, 20]
    assert frame["id"].tolist() == [1, 2]


def test_early_exit_with_stats_returns_a_copy(tmp_path):
    frame = _frame(tmp_path, {"g": ["a"] * 3, "v": [1.0, 2.0, 3.0]})

    result, stats = frame.handle_outliers("v", hierarchy_cols=[], get_hierarchical_stats=True)

    assert result is not frame
    assert result.equals(frame)
    assert stats.empty


def test_inplace_true_still_mutates_and_warns_at_the_caller(tmp_path):
    frame = _frame(tmp_path)

    with pytest.warns(DeprecationWarning, match="inplace=True is deprecated") as record:
        assert frame.drop_by_substring("name", inplace=True) is None

    assert list(frame.columns) == ["id", "latitude"]
    assert [warning.filename for warning in record] == [__file__]


@pytest.mark.parametrize(
    "call",
    [
        lambda frame: frame.handle_outliers("v", hierarchy_cols=["g"], inplace=True),
        lambda frame: frame.handle_outliers("v", hierarchy_cols=["g"], inplace=True, get_hierarchical_stats=True),
        lambda frame: frame.fill_missing("v", hierarchy_cols="g", inplace=True),
        lambda frame: frame.rename_all_DOTS(inplace=True),
    ],
)
def test_every_inplace_path_warns_exactly_once_at_the_caller(tmp_path, call):
    frame = _frame(tmp_path, {"g": ["a"] * 6, "v": [1.0, 2.0, 3.0, 4.0, 5.0, 100.0]})

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        call(frame)

    deprecations = [warning for warning in caught if warning.category is DeprecationWarning]
    assert len(deprecations) == 1
    assert deprecations[0].filename == __file__


def test_inplace_false_and_default_do_not_warn(tmp_path):
    frame = _frame(tmp_path)

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        frame.drop_by_substring("name")
        frame.drop_by_substring("name", inplace=False)


def test_split_list_objects_inplace_explode_reshapes_the_original(tmp_path):
    frame = _frame(tmp_path, {"id": [1, 2], "items": [["a", "b"], ["c"]]})
    expected = frame.split_list_objects("items", explode_rows=True, first=[0, 1])

    with pytest.warns(DeprecationWarning):
        assert frame.split_list_objects("items", explode_rows=True, inplace=True, first=[0, 1]) is None

    pd.testing.assert_frame_equal(pd.DataFrame(frame), pd.DataFrame(expected))
    assert frame["first"].tolist() == ["a", "b", "c"]
    assert len(frame) == 3


def test_melt_transform_pivot_result_keeps_the_cache_manager(tmp_path):
    frame = _frame(tmp_path, {"id": [1, 2], "left": ["a", "b"], "right": ["c", "d"]})

    result = frame.melt_transform_pivot(id_vars="id", transform_func=lambda values: values.str.upper())

    assert isinstance(result, BaseData)
    assert result.cache_manager is frame.cache_manager


def test_inplace_results_are_not_aliases(tmp_path):
    frame = _frame(tmp_path)

    with pytest.warns(DeprecationWarning):
        frame.rename_all_DOTS(inplace=True)

    result = frame.rename_all_DOTS()
    result.loc[0, "id"] = 99

    assert frame.loc[0, "id"] == 1


def test_inplace_on_missing_optional_dependency_still_raises(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "deep_translator", None)
    frame = _frame(tmp_path)

    with pytest.raises(ImportError, match="deep-translator"):
        frame.translate_categorical_column("name", inplace=True)
