"""Cached lexicon-based entity extraction with spaCy."""

import hashlib
import json
import os
from collections.abc import Iterable
from typing import Any

from bynat.caching import CacheManager

from .._common import FrameT, _import_optional, _require_positive_int, logger
from .engine import _cached_map

_NLP_REQUIRED_PIPES = frozenset(
    {'tok2vec', 'tagger', 'attribute_ruler', 'lemmatizer', 'morphologizer'}
)


def _normalize_nlp_disable(disable: Iterable[str] | str | None) -> tuple[str, ...]:
    """Normalize and validate spaCy components requested for disabling."""
    if disable is None:
        return ()

    if isinstance(disable, str):
        components: tuple = (disable,)
    else:
        try:
            components = tuple(disable)
        except TypeError as exc:
            raise TypeError(
                "disable must be None, a string, or an iterable of strings."
            ) from exc

    normalized: list[str] = []

    for component in components:
        if not isinstance(component, str):
            raise TypeError("disable must contain only strings.")

        component = component.strip()

        if not component:
            raise ValueError("disable cannot contain empty component names.")

        if component not in normalized:
            normalized.append(component)

    prohibited = sorted(set(normalized) & _NLP_REQUIRED_PIPES)

    if prohibited:
        raise ValueError(
            "disable cannot disable spaCy components required for NLP extraction: "
            f"{prohibited}."
        )

    return tuple(normalized)


def _nlp_cache_bucket(target_col: str, model_identifier: str, disable: tuple[str, ...]) -> str:
    """Build a filesystem-safe cache bucket for one NLP configuration."""
    identity = json.dumps(
        [target_col, model_identifier, sorted(disable)],
        ensure_ascii = False,
        separators = (',', ':'),
    )
    digest = hashlib.sha1(identity.encode('utf-8')).hexdigest()[:16]
    return f"nlp_{digest}"


def _lexicon_terms(lexicon_data: Any, keys_to_match: list) -> set[str]:
    """Collect lowercase terms found under any of the requested lexicon keys."""
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

    return extracted_terms


def extract_entities_nlp(
    df: FrameT,
    *source_cols: Any,
    lexicon_source: str | os.PathLike | dict,
    spacy_model: str = "en_core_web_sm",
    exclude_pos: set | list | tuple | str | None = None,
    disable: Iterable[str] | str | None = ('parser', 'ner'),
    save_every: int = 1000,
    noisy_terms: set | None = None,
    cache_manager: CacheManager,
    **target_cols_and_json_keys: Any
) -> FrameT:
    """
    Extract entities from free text by matching against a cached lexicon with
    spaCy tokenization and lemmatization.

    Notes:
        Terms are loaded from a JSON file or dictionary, grouped by target
        output column, and matched across the combined source text. Results
        are cached per unique text snippet so repeat runs avoid reprocessing
        the same content. Cache buckets include the target column, model
        identifier, and normalized disabled components, so configurations do
        not reuse one another's results.

        The `disable` parameter controls which spaCy pipeline components are
        disabled during both lexicon and text processing. Its default is
        `("parser", "ner")`. The components available for disabling depend
        on the selected spaCy model.

        The following components cannot be disabled because they may be
        required to provide token annotations or representations used by
        lemmatization and entity matching:
            - `tok2vec`
            - `tagger`
            - `attribute_ruler`
            - `lemmatizer`
            - `morphologizer`

        spaCy pipeline components may depend on one another. For example,
        some lemmatizers require POS annotations provided by `tagger` and
        `attribute_ruler`, or by `morphologizer`, depending on the model.
        Disabling a component may also affect other pipeline outputs.

        Components not present in the selected model are not disabled.
        Requesting an unknown component logs a warning listing the unknown
        names and the components available in the loaded pipeline; processing
        continues.

    Args:
        *source_cols: Text columns whose combined content will be searched.
        lexicon_source (str | os.PathLike | dict): Path to a lexicon JSON
            file or a preloaded dictionary with equivalent structure.
        spacy_model (str): Installed spaCy model name or model path.
            Defaults to `"en_core_web_sm"`.
        exclude_pos (set | list | tuple | str | None): Optional POS tag or
            tags to ignore for single-token matches, for example
            `{"VERB", "ADV"}`. Defaults to None.
        disable (Iterable[str] | str | None): spaCy pipeline components to
            disable during both lexicon and text processing. Accepts None,
            one component name, or an iterable of component names.
            Defaults to `("parser", "ner")`. The components `tok2vec`,
            `tagger`, `attribute_ruler`, `lemmatizer`, and `morphologizer`
            cannot be disabled. Unknown component names generate a warning
            and are otherwise ignored.
        save_every (int): Cache checkpoint frequency during NLP processing.
            Defaults to 1000.
        noisy_terms (set | None): Terms that should be ignored even if they
            are matched. Terms are compared case-insensitively. Defaults
            to None.
        **target_cols_and_json_keys: Mapping where each key is an output
            column name and each value is a lexicon key or list of keys to
            collect terms from.

    Returns:
        A new dataframe with extracted entity-list columns.

    Raises:
        ValueError: If `disable` contains a required spaCy component, a
            component name is empty, or `save_every` is not a positive
            integer.

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

    if "noisey_terms" in target_cols_and_json_keys:
        raise TypeError(
            "extract_entities_nlp() got an unexpected keyword argument "
            "'noisey_terms'; did you mean 'noisy_terms'?"
        )

    normalized_disable = _normalize_nlp_disable(disable)
    ignored_terms: set[str] = set()

    if noisy_terms is not None:
        ignored_terms.update(str(term).strip().lower() for term in noisy_terms if str(term).strip())

    if exclude_pos is None:
        excluded_pos = set()

    elif isinstance(exclude_pos, str):
        excluded_pos = {exclude_pos.strip().upper()} if exclude_pos.strip() else set()

    else:
        excluded_pos = {str(pos).strip().upper() for pos in exclude_pos}

    _require_positive_int(save_every, "save_every")

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
        extracted_terms = _lexicon_terms(lexicon_data, keys_to_match)

        if extracted_terms:
            term_dict[target_col] = list(extracted_terms)

        else:
            logger.warning("No data found for keys '%s'. Skipping '%s'.", keys_to_match, target_col)

    if not term_dict:
        raise ValueError("No valid terms were extracted from the requested lexicon keys.")

    valid_sources = [col for col in source_cols if col in df.columns]
    if not valid_sources:
        logger.warning("No valid source columns found.")

        return df.copy()

    result = df.copy()
    combined_series = result[valid_sources[0]].fillna('').astype(str)

    for col in valid_sources[1:]:
        combined_series += " " + result[col].fillna('').astype(str)

    combined_text = combined_series.str.strip()
    valid_mask = combined_text != ""
    all_valid_texts = combined_text[valid_mask].unique().tolist()

    if not all_valid_texts:
        logger.warning("No valid text found in the specified columns.")
        return result

    if not isinstance(spacy_model, (str, os.PathLike)):
        raise ValueError("spacy_model must be a non-empty model name or path.")

    model_path = os.fspath(spacy_model)

    if not model_path.strip():
        raise ValueError("spacy_model must be a non-empty model name or path.")

    model_identifier = os.fsdecode(model_path)
    buckets = {
        target_col: _nlp_cache_bucket(target_col, model_identifier, normalized_disable)
        for target_col in term_dict
    }

    active: dict[str, Any] = {}

    def start(count: int) -> None:
        spacy = _import_optional("spacy", "spacy", "extract_entities_nlp")
        matcher_module = _import_optional(
            "spacy.matcher", "spacy", "extract_entities_nlp"
        )
        logger.info("Loading NLP Model and building Multi-Label Matcher...")

        nlp = spacy.load(model_path)

        available_pipe_names = tuple(getattr(nlp, "pipe_names", ()))
        unknown_disable = sorted(set(normalized_disable) - set(available_pipe_names))

        if unknown_disable:
            logger.warning(
                "Requested spaCy pipeline components %s are not present in the "
                "loaded pipeline %s; continuing.",
                unknown_disable,
                list(available_pipe_names),
            )

        matcher = matcher_module.Matcher(nlp.vocab)

        for col_name, terms in term_dict.items():
            all_patterns = []

            for term_doc in nlp.pipe(terms, disable = normalized_disable):
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

        active["nlp"] = nlp
        active["matcher"] = matcher
        logger.info("Extracting smart entities from %s rows simultaneously...", count)

    def extract(texts: list) -> list:
        nlp = active["nlp"]
        matcher = active["matcher"]
        outcomes: list[Any] = []

        for doc in nlp.pipe(texts, batch_size = 100, disable = normalized_disable):
            found_entities: dict[str, set] = {col: set() for col in term_dict}

            try:
                for match_id, span_start, span_end in matcher(doc):
                    col_name = nlp.vocab.strings[match_id]
                    span = doc[span_start:span_end]
                    clean_skill = " ".join([t.lemma_ for t in span]).title()

                    is_valid = True

                    if len(clean_skill) == 1:
                        if not span.text.isupper() or not span[0].is_alpha:
                            is_valid = False

                    if len(span) == 1 and is_valid:
                        if span[0].pos_ in excluded_pos:
                            is_valid = False

                    if clean_skill.lower() in ignored_terms:
                        is_valid = False

                    if is_valid:
                        found_entities[col_name].add(clean_skill)

            except Exception as e:
                outcomes.append(e)
                continue

            outcomes.append({buckets[col]: list(found_entities[col]) for col in term_dict})

        return outcomes

    caches = _cached_map(
        all_valid_texts,
        None,
        tuple(buckets.values()),
        save_every,
        cache_manager = cache_manager,
        func_name = "extract_entities_nlp",
        key = str,
        fetch_many = extract,
        on_fetch_start = start,
        action = "process text snippet",
        label = "NLP extractions",
    )

    for col, bucket in buckets.items():
        cache = caches[bucket]
        result[col] = [cache.get(text, []) for text in combined_text]

    return result
