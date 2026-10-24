"""Keyword extraction, truncation, and categorization helpers."""

import importlib
import re
from collections.abc import Sequence
from typing import Any, cast

import numpy as np
import pandas as pd

from ._common import FrameT, _import_optional

_WHITESPACE = re.compile(r'\s+')


def _optional_ahocorasick() -> Any:
    """Return the `ahocorasick` module when installed, otherwise None."""
    try:
        return importlib.import_module("ahocorasick")
    except ImportError:
        return None


def _is_ascii_word_char(char: str) -> bool:
    """Match `\\w` for an ASCII character."""
    return char.isalnum() or char == '_'


def _first_literal_matches(texts: pd.Index | np.ndarray, keywords: Sequence[str]) -> list[int | None]:
    """
    Return, for each text, the index of the highest-priority keyword that
    occurs as a case-insensitive whole word, or None.

    One zero-width alternation finds every candidate start position, so
    overlapping keywords are all seen while each text is scanned once. Pure
    ASCII texts use an Aho-Corasick automaton instead when the optional
    `pyahocorasick` dependency is installed and every keyword is ASCII; both
    paths give the same result.
    """
    pattern = re.compile(
        r"(?=(?<!\w)(?:" + "|".join(f"({re.escape(word)})" for word in keywords) + r")(?!\w))",
        re.IGNORECASE,
    )

    automaton = None
    ahocorasick = _optional_ahocorasick() if all(word.isascii() for word in keywords) else None

    if ahocorasick is not None:
        automaton = ahocorasick.Automaton()
        priorities: dict[str, int] = {}

        for priority, word in enumerate(keywords):
            priorities.setdefault(word.lower(), priority)

        for word_lower, priority in priorities.items():
            automaton.add_word(word_lower, (len(word_lower), priority))

        automaton.make_automaton()

    matches: list[int | None] = []

    for text in texts:
        best: int | None = None

        if automaton is not None and text.isascii():
            text_lower = text.lower()
            text_len = len(text_lower)

            for end_index, (word_len, priority) in automaton.iter(text_lower):
                if best is not None and priority >= best:
                    continue

                start_index = end_index - word_len + 1

                if start_index > 0 and _is_ascii_word_char(text_lower[start_index - 1]):
                    continue

                if end_index + 1 < text_len and _is_ascii_word_char(text_lower[end_index + 1]):
                    continue

                best = priority

        else:
            for match in pattern.finditer(text):
                # Exactly one keyword group takes part in every match.
                priority = cast(int, match.lastindex) - 1

                if best is None or priority < best:
                    best = priority

                    if best == 0:
                        break

        matches.append(best)

    return matches


def _first_regex_matches(texts: pd.Index | np.ndarray, patterns: Sequence[re.Pattern]) -> pd.Series:
    """
    Return the first capture group of the highest-priority matching pattern
    for each text, or NaN, using `Series.str.extract` semantics.
    """
    found = pd.Series(np.nan, index = range(len(texts)), dtype = object)
    unresolved = pd.Series(texts, dtype = object)

    for compiled in patterns:
        if unresolved.empty:
            break

        extracted_match = unresolved.str.extract(compiled, expand = True)

        if extracted_match.empty:
            continue

        match_col = extracted_match.iloc[:, 0]
        hit = match_col.notna()

        found.loc[match_col.index[hit]] = match_col[hit]
        unresolved = unresolved[~hit]

    return found


def _remove_patterns(series: pd.Series, patterns: Sequence[re.Pattern]) -> pd.Series:
    """Apply removal patterns and whitespace cleanup once per unique text."""
    text = series.astype(str)
    uniques = text.unique()
    cleaned = {}

    for value in uniques:
        stripped = value

        for compiled in patterns:
            stripped = compiled.sub('', stripped)

        cleaned[value] = _WHITESPACE.sub(' ', stripped).strip()

    return text.map(cleaned)


def extract_keywords(
    df: FrameT,
    *source_cols: Any,
    remove_extracted: bool = False,
    use_regex: bool = False,
    **target_cols_and_keywords: Any
) -> FrameT:
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
        - **Performance**: Each unique combined text is matched once and the
          results are mapped back to every row. Literal keywords are matched
          with one combined pattern (or an Aho-Corasick automaton when the
          optional `pyahocorasick` dependency is installed).

    Args:
        *source_cols: Variable number of column names to search within.
        remove_extracted (bool): If True, removes the matched keywords from the
            original source columns to clean the text. Defaults to False.
        use_regex (bool): If True, treats the input strings in **target_cols_and_keywords
            as regular expressions for pattern extraction. Defaults to False.
        **target_cols_and_keywords: Keyword arguments where the key is the new
            column name and the value is a list of strings to search for.

    Returns:
        A new dataframe with the extracted columns.

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
            df.extract_keywords("comments", "tags", categories=["urgent", "review"])
    ```
    """

    sources = list(source_cols)
    valid_sources = [col for col in sources if col in df.columns]

    if not valid_sources:
        return df.copy()

    source_text = df[valid_sources].fillna('').astype(str)
    combined_text = source_text.iloc[:, 0]

    for position in range(1, source_text.shape[1]):
        combined_text = combined_text + ' ' + source_text.iloc[:, position]

    codes, unique_texts = pd.factorize(combined_text)

    result = df.copy()

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

        if use_regex:
            extract_patterns = []

            for word in cleaned_keywords:
                compiled = re.compile(word)

                if compiled.groups == 0:
                    compiled = re.compile(rf"({word})")

                extract_patterns.append(compiled)

            per_text = _first_regex_matches(unique_texts, extract_patterns).to_numpy()

        else:
            titles = [word.title() for word in cleaned_keywords]
            per_text = np.array(
                [
                    np.nan if priority is None else titles[priority]
                    for priority in _first_literal_matches(unique_texts, cleaned_keywords)
                ],
                dtype = object,
            )

        if len(per_text):
            row_values = per_text[codes]
            mask = pd.notna(row_values)

            if mask.any():
                extracted.loc[mask] = row_values[mask]

        if new_col_name in result.columns:
            result[new_col_name] = result[new_col_name].astype('object')
            result[new_col_name] = result[new_col_name].where(result[new_col_name].notna(), extracted)

        else:
            result[new_col_name] = extracted

        mask_extracted = result[new_col_name].notna()
        result.loc[mask_extracted, new_col_name] = result.loc[mask_extracted, new_col_name].astype(str).str.title()

        if remove_extracted:
            if use_regex:
                remove_patterns = [re.compile(pattern) for pattern in cleaned_keywords]

            else:
                escaped_keywords = [re.escape(word) for word in cleaned_keywords]
                remove_patterns = [
                    re.compile(rf"(?<!\w)({'|'.join(escaped_keywords)})(?!\w)", re.IGNORECASE)
                ]

            for col in valid_sources:
                mask = result[col].notna()

                result.loc[mask, col] = _remove_patterns(result.loc[mask, col], remove_patterns)

    return result


def truncate_after_substring(df: FrameT, regex: bool = True, **target_cols_and_substrings: Any) -> FrameT:
    """
    Remove specific substrings and everything following them from target columns.

    Args:
        regex (bool): Passed to `pandas.Series.str.split`. When True,
            substring values are interpreted as regular expressions.
            Defaults to True.
        **target_cols_and_substrings: Keyword arguments where the key is the column
            name and the value is a string or a list of strings at which to cut off the text.

    Returns:
        A new dataframe with truncated columns.

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
        return df.copy()

    result = df.copy()

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

    return result


def categorize_by_reference(
    df: FrameT,
    *source_cols: str,
    target_col: str,
    reference: dict,
    default_category: str = 'Other'
) -> FrameT:
    """
    Categorize text data into broader groups based on a reference lexicon dictionary.

    This method scans one or more source text columns for specific keywords defined in a
    lexicon dictionary. If a match is found, the dictionary's key (the category)
    is assigned to a new target column.

    Notes:
        - The search uses word boundaries (\\b) to prevent partial word matches
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

    Returns:
        A new dataframe with the category column.

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
            reference = reference
        )
    ```
    """
    sources = list(source_cols)
    valid_sources = [col for col in sources if col in df.columns]

    if not valid_sources:
        return df.copy()

    result = df.copy()

    combined_series = result[valid_sources[0]].fillna('').astype(str)

    if len(valid_sources) > 1:
        for col in valid_sources[1:]:
            combined_series += " " + result[col].fillna('').astype(str)

    if not isinstance(reference, dict) or not reference:
        result[target_col] = default_category

        return result

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

        return result

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

    return result
