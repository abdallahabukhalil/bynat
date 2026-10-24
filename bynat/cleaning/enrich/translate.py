"""Language-aware imputation and cached translation helpers."""

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
import pandas as pd

from bynat.caching import CacheManager

from .._common import (
    FrameT,
    _cache_has_value,
    _cache_lookup_value,
    _import_optional,
    _require_positive_int,
    logger,
)
from .engine import SKIP, _cached_map, _rate_limited_map
from .providers import GoogleTranslatorProvider, Translator


def _translate_all(provider: Any, texts: Sequence[str], target_lang: str) -> list[Any]:
    """Translate texts with a provider's batch method or its rate-limited `translate`."""
    translate_batch = getattr(provider, "translate_batch", None)

    if callable(translate_batch):
        return list(translate_batch(list(texts), target_lang))

    return _rate_limited_map(
        lambda text: provider.translate(text, target_lang),
        texts,
        max_workers = int(getattr(provider, "max_workers", 1) or 1),
        min_interval = float(getattr(provider, "min_interval", 0.0) or 0.0),
    )


def _translate_cached(
    values: pd.Series,
    *,
    target_col: str,
    target_lang: str,
    save_every: int,
    provider: Translator | None,
    cache_manager: CacheManager,
    func_name: str,
    reject: Callable[[Any], bool],
) -> dict:
    """Translate the uncached unique values of a series and return the cache bucket."""
    active: dict[str, Any] = {}

    def start(count: int) -> None:
        logger.info("preparing %s values to be translated...", count)
        active["provider"] = provider if provider is not None else GoogleTranslatorProvider(func_name)

    def fetch_many(chunk: list) -> list:
        translations = _translate_all(active["provider"], [str(value) for value in chunk], target_lang)

        return [
            translation if isinstance(translation, Exception) or not reject(translation) else SKIP
            for translation in translations
        ]

    return _cached_map(
        values,
        None,
        target_col,
        save_every,
        cache_manager = cache_manager,
        func_name = func_name,
        is_cached = _cache_has_value,
        fetch_many = fetch_many,
        on_fetch_start = start,
        action = "translate",
        label = "translations",
    )


def impute_by_language(
    df: FrameT,
    reference_col: str,
    *target_cols: Any,
    target_lang: str = 'en'
) -> FrameT:
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

    Returns:
        A new dataframe with imputed columns.

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
    if reference_col not in df.columns:
        return df.copy()

    langdetect = _import_optional(
        "langdetect", "langdetect", "impute_by_language"
    )
    langdetect.DetectorFactory.seed = 0
    detect_error = getattr(langdetect, "LangDetectException", Exception)
    detect = langdetect.detect

    result = df.copy()

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

    return result


def translate_conditional_column(
    df: FrameT,
    partition_col: str,
    detect_col: str,
    target_col: str,
    target_lang: str = 'en',
    sample_size: int = 10,
    number_of_samples: int = 3,
    threshold: float = 0.5,
    save_every: int = 1000,
    random_state: int | np.random.Generator | np.random.RandomState | None = 0,
    *,
    provider: Translator | None = None,
    cache_manager: CacheManager,
) -> FrameT:
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
        random_state (int | numpy.random.Generator | numpy.random.RandomState | None):
            Seed or generator for partition sampling. One generator is drawn
            from for every sample, so repeated samples differ while repeated
            calls with the same seed are reproducible. Pass None for
            unseeded sampling. Defaults to 0.
        provider (Translator | None): Translation provider. Defaults to
            Google Translate through `deep-translator`.

    Returns:
        A new dataframe with translated values.

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

    _require_positive_int(sample_size, "sample_size")
    _require_positive_int(number_of_samples, "number_of_samples")
    _require_positive_int(save_every, "save_every")

    required_cols = [partition_col, detect_col, target_col]

    if not all(col in df.columns for col in required_cols):
        return df.copy()

    langdetect = _import_optional(
        "langdetect", "langdetect", "translate_conditional_column"
    )
    langdetect.DetectorFactory.seed = 0
    detect_error = getattr(langdetect, "LangDetectException", Exception)
    detect = langdetect.detect

    if isinstance(random_state, (np.random.Generator, np.random.RandomState)):
        rng = random_state
    else:
        rng = np.random.default_rng(random_state)

    result = df.copy()

    safe_partitions = []
    partition_texts = result.groupby(partition_col, sort = False, dropna = True, observed = True)[detect_col]

    for partition, texts in partition_texts:
        available_texts = texts.dropna().drop_duplicates()

        if available_texts.empty:
            continue

        actual_sample_size = min(sample_size, len(available_texts))

        lang_match_count = 0
        for _ in range(number_of_samples):
            sample_texts = available_texts.sample(n = actual_sample_size, random_state = rng)

            combined_text = " . ".join(sample_texts.astype(str).tolist())

            try:
                if detect(str(combined_text)) == target_lang:
                    lang_match_count += 1

            except detect_error:
                pass

        if lang_match_count >= (number_of_samples * threshold):
            safe_partitions.append(partition)

    mask_foreign = ~result[partition_col].isin(safe_partitions)
    foreign_values = result.loc[mask_foreign, target_col].dropna()

    if foreign_values.empty:
        return result

    current_cache = _translate_cached(
        foreign_values,
        target_col = target_col,
        target_lang = target_lang,
        save_every = save_every,
        provider = provider,
        cache_manager = cache_manager,
        func_name = "translate_conditional_column",
        reject = lambda translated: not translated or "Error 500" in translated or "Server Error" in translated,
    )

    unique_vals = result[target_col].dropna().unique()

    lookup_dict = {
        val: _cache_lookup_value(current_cache, val)
        for val in unique_vals
    }

    result[target_col] = result[target_col].map(lookup_dict).fillna(result[target_col])

    mask_translated = result[target_col].notna()
    result.loc[mask_translated, target_col] = result.loc[mask_translated, target_col].astype(str).str.title()

    return result


def translate_categorical_column(
    df: FrameT,
    target_col: str,
    target_lang: str = 'en',
    save_every: int = 1000,
    *,
    provider: Translator | None = None,
    cache_manager: CacheManager,
) -> FrameT:
    """
    Translate all unique categories in a specific column using an automated translator.

    This method optimizes performance and API usage by translating only unique
    values (categories) and utilizing a caching mechanism to avoid re-translating
    previously processed strings.

    Notes:
        Cache keys are stored as strings so non-string categories remain
        reusable after the cache is saved and loaded again.

    Args:
        target_col (str): The name of the column containing categorical text to translate.
        target_lang (str): The ISO language code for the target language.
            Defaults to 'en'.
        save_every (int): Cache checkpoint frequency while translating.
            Defaults to 1000.
        provider (Translator | None): Translation provider. Defaults to
            Google Translate through `deep-translator`.

    Returns:
        A new dataframe with translated values.

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

    if target_col not in df.columns:
        return df.copy()

    _require_positive_int(save_every, "save_every")

    result = df.copy()

    values = result[target_col].dropna()

    if values.empty:
        return result

    current_cache = _translate_cached(
        values,
        target_col = target_col,
        target_lang = target_lang,
        save_every = save_every,
        provider = provider,
        cache_manager = cache_manager,
        func_name = "translate_categorical_column",
        reject = lambda translated: not translated,
    )

    unique_vals = result[target_col].dropna().unique()

    lookup_dict = {
        val: _cache_lookup_value(current_cache, val)
        for val in unique_vals
    }

    result[target_col] = result[target_col].map(lookup_dict).fillna(result[target_col])

    return result
