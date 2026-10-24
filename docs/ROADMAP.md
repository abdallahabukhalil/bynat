# bynat Roadmap

This roadmap is written against the code as it is, not as it might be. Every item names the real method, file, or test it refers to. Line numbers are as of `0.3.0` and drift; method names are the stable reference. If an item stops matching the code, fix the item before working on it.

## 1. Project context

`bynat` is a small utility library. The cleaning code lives in focused modules under `bynat/cleaning/` and the cache in `bynat/caching/manager.py`.

- **Cleaning functions** in `columns`, `text`, `stats`, `enrich` (`translate`, `geocode`, `currency`, `nlp`, `engine`, `providers`), and `io` take a DataFrame first and return a new one: column helpers, text extraction, hierarchical outlier handling, imputation and statistics, external-service enrichment (translation, geocoding, currency, spaCy), and JSON/SQL export and loading.
- **`df.bynat`** (`bynat.cleaning.accessor`) exposes them on any DataFrame. **`BaseData`**, `JsonFile`, and `CsvFile` (`bynat.cleaning.base`) are deprecated `pandas.DataFrame` subclass wrappers over the same functions. `bynat.cleaning.Cleaning` and `bynat.caching.Caching` are compatibility aliases.
- **`bynat.caching.CacheManager`** keeps one SQLite database per cache directory (`cache.sqlite3`) holding nested dictionaries keyed by column, importing legacy `<func_name>_cache.json` files on first read. Its public methods are `load_function_cache`, `get_column_cache`, `save_function_cache`, and `delete_column_cache`. There is no `get`, `set`, `clear`, or TTL API.

## 2. Principles

1. **No row-wise iteration over frames in data paths.** `DataFrame.apply(..., axis=1)`, `iterrows`, and `itertuples` are not allowed. Column-wise `DataFrame.apply`, `Series.map`, groupby, and vectorized string operations are fine. Python-level loops over **unique values** are expected wherever each value needs an external call or a Python object (translation, geocoding, Aho-Corasick, JSON keys for unhashable cells). Performance changes must come with a benchmark and an equivalence test.
2. **Fail loudly on caller errors, stay lenient on data.** Invalid arguments raise. Missing columns are skipped with a logged warning (the documented contract, see README). Per-item external failures are logged and skipped, never cached.
3. **Backward compatibility is explicit.** Behavior changes are listed in `CHANGELOG.md` under *Breaking changes* and ship in a new minor version (`0.x`). Prefer a deprecation path when it is cheap.
4. **Minimal footprint.** Core dependencies stay `numpy` and `pandas`. Heavy integrations stay optional and are imported lazily with `_import_optional`. Prefer the standard library (`sqlite3`, `logging`, `tempfile`) over new dependencies.
5. **Tests do not touch the network.** External services are replaced with fake modules (`tests/test_integrations.py`). Light extras with offline behavior (`pyahocorasick`, SQLAlchemy with SQLite) are also tested for real when installed.

## 3. Current state (`0.3.0`)

- **Python:** `>=3.11`. CI runs Ubuntu and Windows on 3.11 and 3.13, a Ruff lint job, and an extras job (`.[categorization,sql]`).
- **Tests:** 289 full-suite tests pass on pandas 3 (pandas 2 skips the comparisons against the `0.3.0` outlier merge, which mishandles null keys there). Six are skipped unless the `categorization` and `sql` extras are installed. Equivalence tests for the optimized methods live in `tests/test_equivalence.py`.
- **Lint:** `ruff check bynat tests benchmarks` is clean with `E`, `F`, `W`, `I`, `UP` (`E501` ignored; see `pyproject.toml`).
- **Types:** `python -m mypy` is clean for the scope in `[tool.mypy]`: the cache manager and the cleaning helpers. The deprecated `bynat/cleaning/base.py` is excluded.
- **Row-wise iteration audit:** no `apply(axis=1)`, `iterrows`, or `itertuples` in `bynat/`. Unique-value loops exist in the enrichment methods by design (Principle 1).

### Done

| Version | Item | Where |
| --- | --- | --- |
| 0.1.2 | `save_to_json(append=True)` no longer loses data on list or dict columns; fast path when no such columns exist | `save_to_json`, `_dedupe_key` |
| 0.1.2 | Named, multi-level, and duplicate indexes work in outlier, mode-fill, and currency methods | `_left_merge_aligned` |
| 0.1.2 | `drop_outliers` drops by position, so duplicated labels keep their twin row | `drop_outliers` |
| 0.1.2 | Atomic cache saves (temporary file + `os.replace`) | `CacheManager.save_function_cache` |
| 0.1.2 | Negative caching for unresolvable geocodes, countries, and past exchange-rate years | `fill_missing_coordinates`, `convert_to_usd` |
| 0.1.2 | Geocoding shortens failed queries one part at a time, as documented; `user_agent` and `retry_not_found` parameters | `fill_missing_coordinates` |
| 0.1.2 | spaCy `parser` and `ner` disabled for input texts | `extract_entities_nlp` |
| 0.1.2 | Bare `except:` removed | `impute_by_language`, `translate_conditional_column`, `convert_to_usd` |
| 0.1.2 | `get_hierarchical_stats` documented in the README | `README.md` |
| 0.1.2 | Fake-module tests for every external-service method; Ruff; Windows and extras CI | `tests/test_integrations.py`, `.github/workflows/test.yml` |
| 0.2.0 | `logging` under the `bynat` logger replaces `print` | both modules |
| 0.2.0 | Caller errors raise: unknown `strategy`, `save_to_json` path and unreadable append target, `save_to_sql` settings and database errors, `extract_entities_nlp` configuration | see `CHANGELOG.md` |
| 0.2.0 | Corrupted cache files are quarantined as `<name>.corrupt.<timestamp>` instead of being overwritten | `CacheManager.load_function_cache` |
| 0.2.0 | `BYNAT_CACHE_DIR`, with the old relative default kept as the fallback | `bynat.caching.Caching.resolve_cache_dir` |
| 0.2.0 | `save_to_sql` disposes its engine | `save_to_sql` |
| 0.3.0 | `handle_outliers`, `drop_outliers`, and `fill_missing` optionally return original-data hierarchical statistics without changing their default return contracts | `BaseData.handle_outliers`, `BaseData.drop_outliers`, `BaseData.fill_missing`, `tests/test_hierarchical_stats.py` |
| 0.3.0 | `extract_entities_nlp` accepts configurable spaCy pipeline components, validates required components, warns on unknown names, and isolates configuration-specific caches | `BaseData.extract_entities_nlp`, `tests/test_integrations.py` |
| Unreleased | `ENRICH-01` one cached-lookup engine (`_cached_map`) for the translation, geocoding, currency-code, and NLP lookups, with `Translator`, `Geocoder`, and `RateSource` protocols accepted through `provider=` | `bynat/cleaning/enrich/`, `tests/test_enrichment.py` |
| Unreleased | `PERF-01` translation batches or a rate-limited thread pool instead of `sleep(0.2)` per value; one rate download per currency | `enrich/translate.py`, `enrich/currency.py`, `enrich/providers.py` |
| Unreleased | `PERF-02` `extract_keywords` on unique texts with one combined pattern (Aho-Corasick for ASCII text when installed) | `bynat/cleaning/text.py`, `tests/test_equivalence.py`, `benchmarks/bench_extract_keywords.py` |
| Unreleased | `PERF-03` `translate_conditional_column` partitions through one `groupby`, reproducible `random_state` | `enrich/translate.py` |
| Unreleased | `PERF-04` one generator of per-level outlier masks, one grouped `quantile([0.25, 0.75])` per level, no merge | `bynat/cleaning/stats.py`, `tests/test_equivalence.py`, `benchmarks/bench_outliers.py` |
| Unreleased | `API-01` `df.bynat` accessor, `bynat.read_json` / `bynat.read_csv`, deprecated `BaseData` / `JsonFile` / `CsvFile` wrappers, lazily created and shared `CacheManager` | `bynat/cleaning/accessor.py`, `bynat/cleaning/base.py`, `tests/test_accessor.py` |
| Unreleased | `API-02` transformations always return new objects; `inplace=True` warns (D-03) | `bynat/cleaning/base.py`, `tests/test_inplace_deprecation.py` |
| Unreleased | `ARCH-01` `Cleaning.py` split into modules, `Caching.py` renamed to `manager.py`, legacy module paths kept as aliases | `bynat/cleaning/`, `bynat/caching/` |
| Unreleased | `QA-01` mypy for the cache manager and the cleaning helpers, CI job | `pyproject.toml`, `.github/workflows/test.yml` |
| 0.3.0 | `CACHE-01` SQLite cache backend: per-directory key-value store that replaces full-file JSON rewrites, one-time legacy `*_cache.json` migration, delta-only checkpoint saves against deep baselines, `BEGIN IMMEDIATE` + WAL busy-timeout concurrency, and baseline-scoped `delete_column_cache` | `bynat/caching/Caching.py` (`CacheManager`), `tests/test_caching.py` |

## 4. Backlog

Priority: **P1** correctness or data safety, **P2** performance or maintainability, **P3** nice to have. Effort: **S** under a day, **M** a few days, **L** a week or more.

### Next minor release

#### `DEPR-02`: remove the deprecated API (P2, S)
- Remove `inplace` from every transformation and delete `BaseData`, `JsonFile`, and `CsvFile` with `bynat/cleaning/base.py`, after one minor release of `DeprecationWarning`s (D-03, `API-01`, `API-02`).
- Then add the remaining helper modules to the mypy scope; the cleaning functions are already checked.
- Keep the `bynat.cleaning.Cleaning` and `bynat.caching.Caching` aliases (pointing at the new modules) for at least one more minor release, since they cost nothing.

#### `PERF-05`: rates through the engine (P3, S)
- `convert_to_usd` resolves currency codes through `_cached_map`, but its year-range rate download stays a dedicated loop because one fetch fills many `CODE_YEAR` keys. Revisit if the engine gains a multi-key store.

### Later or optional

- `DEPR-01` (P3, S): deprecate the misspelled `extract_entities_nlp(noisey_terms=...)` alias with a warning.
- `IO-01` (P3, M): an append-friendly output (JSON Lines or SQLite) so `save_to_json(append=True)` does not re-read and rewrite the whole file (`bynat/cleaning/io.py`).
- `COMPAT-01` (P3, S): Python 3.10. Low value. It needs `typing.Self` from `typing_extensions` and a CI run before any `requires-python` change.

## 5. Decisions

- **D-01: `group_key` is always a tuple.** One value per hierarchy column, including single-column levels (`("EG",)`), as pinned by `tests/test_hierarchical_stats.py`. Resolved: keep it.
- **D-02: `mean` and `median` on non-numeric targets raise pandas' `TypeError`.** Resolved: keep failing fast.
- **D-03: return convention without `inplace`.** Resolved: always return a new object, including from early exits. `inplace=True` keeps working with a `DeprecationWarning` for one minor release (`API-02`), then is removed (`DEPR-02`).
- **D-04: concurrency for the cache.** Resolved in favor of `sqlite3` (`CACHE-01`) over a lock-directory protocol or a `portalocker` dependency. It gives locking and atomic commits from the standard library.
- **D-05: logging defaults.** Resolved in `0.2.0`: no `NullHandler`, so `WARNING`s reach stderr through Python's last-resort handler and keep the visibility users had with `print`, while `INFO` progress stays quiet.
- **D-06: `CACHE-01` deletion semantics.** A bucket deletion is calculated against the manager's last committed baseline and deletes only those known rows. Rows added concurrently after that baseline are preserved, preventing a stale manager from erasing unrelated concurrent work. Resolved: keep the safer preservation behavior.
- **D-07: `CACHE-01` write conflicts.** Disjoint row identities merge because saves apply deltas. Concurrent writes to the same `(func_name, bucket, key)` use last committed writer wins. Resolved: do not add version columns, conflict exceptions, or a custom merge policy.
- **D-08: `CACHE-01` legacy JSON validation.** Invalid JSON and non-object top-level values retain the current quarantine behavior. Unsupported nested bucket shapes are not silently converted or discarded; migration leaves the source available, logs the problem, and does not record a completed migration marker. Resolved: preserve recoverability over partial silent import.
- **D-09: `CACHE-01` save failures.** `save_function_cache` continues to log and return without raising for serialization, locking, and SQLite errors. Failed transactions leave the baseline snapshot unchanged, so pending changes remain retryable. Resolved: preserve the current public error behavior while making rollback explicit.
- **D-10: `CACHE-01` transaction and initialization settings.** Database creation is lazy. Each migration, save, and deletion is one explicit `BEGIN IMMEDIATE` transaction. Connections use a finite busy timeout, WAL journaling, and durable synchronous commits. Resolved: rely on SQLite locking and transactions, not a lock-directory protocol.
- **D-11: `CACHE-01` migration safety.** Migration metadata is checked and re-checked inside the write transaction. Entries use idempotent insert-if-absent behavior, so stale JSON cannot overwrite an existing SQLite row. The migration marker is committed only after every source entry succeeds. Resolved: retries are safe after partial failure and concurrent initialization.
- **D-12: `CACHE-01` API compatibility.** The cache API continues returning ordinary mutable dictionaries rather than a custom mapping. Existing direct indexing, assignment, membership, `.get`, iteration/equality, and explicit `None` values remain supported. Resolved: use baseline snapshots and delta persistence instead of changing callers.

## 6. Definition of done

- `python -m pytest -q`, `python -m compileall -q bynat tests`, and `ruff check bynat tests` pass. CI is green on every matrix job.
- New or changed behavior has tests covering the success path and the edge cases that motivated it (nulls, empty frames, named, multi-level, and duplicate indexes, unhashable cells).
- Performance items include an equivalence test and a benchmark at representative sizes. Record the numbers in the PR description, not in the code.
- Behavior changes are listed in `CHANGELOG.md`, and the README stays accurate. README examples are executed before merging.
- No new row-wise iteration (Principle 1), no new mandatory dependencies (Principle 4), and no network access in tests (Principle 5).

## 7. Corrections to the previous roadmap draft

The previous draft described a different codebase. For the record, so its items are not revived by mistake:

- `clean_column_names`, `convert_types`, `expand_date`, `parse_dates`, `normalize_text`, `remove_duplicates`, `clean_currency`, `convert_currency`, `summarize_data`, `translate_text`, `geocode_addresses`, `extract_entities`, `read_sql_query`, `to_sql_table`, and a `BaseData.apply` wrapper do not exist. The tasks built on them (old `PERF-001` and `PERF-003` as written, `API-001`, `API-002`, `TEST-002`) do not apply.
- There were no row-wise `.apply` calls to remove. `categorize_by_reference` already ran once per unique text.
- `CacheManager` had 5 tests, not 0. Corrupt JSON was already caught, not a crash. `_save_cache`, `_load_cache`, `get`, `set`, and `clear()` do not exist.
- `get_hierarchical_stats` returns tuple keys for single-column levels, not scalars (see D-01).
- The draft missed the real defects fixed in `0.1.2`, listed in section 3.
