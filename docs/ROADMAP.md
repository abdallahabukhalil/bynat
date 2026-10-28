# bynat Roadmap

This roadmap is written against the code as it is, not as it might be. Every item names the real method, file, or test it refers to. Line numbers are as of `0.3.0` and drift; method names are the stable reference. If an item stops matching the code, fix the item before working on it.

## 1. Project context

`bynat` is a small utility library. The cleaning code lives in focused modules under `bynat/cleaning/` and the cache in `bynat/caching/manager.py`.

- **Cleaning functions** in `columns`, `text`, `stats`, `enrich` (`translate`, `geocode`, `currency`, `nlp`, `engine`, `providers`), and `io` take a DataFrame first and return a new one: column helpers, text extraction, hierarchical outlier handling, imputation and statistics, external-service enrichment (translation, geocoding, currency, spaCy), and JSON/SQL export and loading.
- **`BaseData`**, `JsonFile`, and `CsvFile` (`bynat.cleaning.base`) are supported public class-based APIs. `BaseData` remains a supported `pandas.DataFrame` subclass over the shared functions. **`df.bynat`** (`bynat.cleaning.accessor`) is an optional complementary interface for ordinary DataFrames, not a replacement or required access path. `bynat.cleaning.Cleaning` and `bynat.caching.Caching` are compatibility aliases.
- **`bynat.caching.CacheManager`** keeps one SQLite database per cache directory (`cache.sqlite3`) holding nested dictionaries keyed by column, importing legacy `<func_name>_cache.json` files on first read. Its public methods are `load_function_cache`, `get_column_cache`, `save_function_cache`, and `delete_column_cache`. There is no `get`, `set`, `clear`, or TTL API.

## 2. Principles

1. **No row-wise iteration over frames in data paths.** `DataFrame.apply(..., axis=1)`, `iterrows`, and `itertuples` are not allowed. Column-wise `DataFrame.apply`, `Series.map`, groupby, and vectorized string operations are fine. Python-level loops over **unique values** are expected wherever each value needs an external call or a Python object (translation, geocoding, Aho-Corasick, JSON keys for unhashable cells). Performance changes must come with a benchmark and an equivalence test.
2. **Fail loudly on caller errors, stay lenient on data.** Invalid arguments raise. Missing columns are skipped with a logged warning (the documented contract, see README). Per-item external failures are logged and skipped, never cached.
3. **Backward compatibility is explicit.** Behavior changes are listed in `CHANGELOG.md` under *Breaking changes* and ship in a new minor version (`0.x`). Prefer a deprecation path when it is cheap.
4. **Minimal footprint.** Core dependencies stay `numpy` and `pandas`. Heavy integrations stay optional and are imported lazily with `_import_optional`. Prefer the standard library (`sqlite3`, `logging`, `tempfile`) over new dependencies.
5. **Tests do not touch the network.** External services are replaced with fake modules (`tests/test_integrations.py`). Light extras with offline behavior (`pyahocorasick`, SQLAlchemy with SQLite) are also tested for real when installed.

## 3. Current state (`0.6.0`)

- **Python:** `>=3.11`. CI runs Ubuntu and Windows on 3.11 and 3.13, a Ruff lint job, and an extras job (`.[categorization,sql]`).
- **Tests:** 289 full-suite tests pass on pandas 3 (pandas 2 skips the comparisons against the `0.3.0` outlier merge, which mishandles null keys there). Six are skipped unless the `categorization` and `sql` extras are installed. Equivalence tests for the optimized methods live in `tests/test_equivalence.py`.
- **Lint:** `ruff check bynat tests benchmarks` is clean with `E`, `F`, `W`, `I`, `UP` (`E501` ignored; see `pyproject.toml`).
- **Types:** `python -m mypy` is clean for the scope in `[tool.mypy]`: the cache manager and the cleaning helpers. The supported class wrappers in `bynat/cleaning/base.py` may remain outside the initial scope while the remaining helper modules are brought into coverage incrementally; this is not a removal plan.
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
| 0.3.0 | `CACHE-01` SQLite cache backend: per-directory key-value store that replaces full-file JSON rewrites, one-time legacy `*_cache.json` migration, delta-only checkpoint saves against deep baselines, `BEGIN IMMEDIATE` + WAL busy-timeout concurrency, and baseline-scoped `delete_column_cache` | `bynat/caching/Caching.py` (`CacheManager`), `tests/test_caching.py` |
| 0.6.0 | `ENRICH-01` one cached-lookup engine (`_cached_map`) for the translation, geocoding, currency-code, and NLP lookups, with `Translator`, `Geocoder`, and `RateSource` protocols accepted through `provider=` | `bynat/cleaning/enrich/`, `tests/test_enrichment.py` |
| 0.6.0 | `PERF-01` translation batches or a rate-limited thread pool instead of `sleep(0.2)` per value; one rate download per currency | `enrich/translate.py`, `enrich/currency.py`, `enrich/providers.py` |
| 0.6.0 | `PERF-02` `extract_keywords` on unique texts with one combined pattern (Aho-Corasick for ASCII text when installed) | `bynat/cleaning/text.py`, `tests/test_equivalence.py`, `benchmarks/bench_extract_keywords.py` |
| 0.6.0 | `PERF-03` `translate_conditional_column` partitions through one `groupby`, reproducible `random_state` | `enrich/translate.py` |
| 0.6.0 | `PERF-04` one generator of per-level outlier masks, one grouped `quantile([0.25, 0.75])` per level, no merge | `bynat/cleaning/stats.py`, `tests/test_equivalence.py`, `benchmarks/bench_outliers.py` |
| 0.6.0 | `API-01` optional `df.bynat` accessor and `bynat.read_json` / `bynat.read_csv`, alongside the supported `BaseData` / `JsonFile` / `CsvFile` class API and lazily created/shared `CacheManager` | `bynat/cleaning/accessor.py`, `bynat/cleaning/base.py`, `tests/test_accessor.py` |
| 0.6.0 | `API-02` transformations return new objects by default; where meaningful, `inplace=True` mutates the original object and returns `None` (or the documented statistics result) | `bynat/cleaning/base.py`, `tests/test_inplace_deprecation.py` |
| 0.6.0 | `ARCH-01` `Cleaning.py` split into modules, `Caching.py` renamed to `manager.py`, legacy module paths kept as aliases | `bynat/cleaning/`, `bynat/caching/` |
| 0.6.0 | `QA-01` mypy for the cache manager and the cleaning helpers, CI job; supported class wrappers remain a separate gradual-typing boundary | `pyproject.toml`, `.github/workflows/test.yml` |
| 0.8.0 | `DQ-01` DataFrame quality profiling: non-mutating `profile` API returning structured `DataQualityReport` with missing values, duplicate rows, column dtypes, and descriptive statistics | `bynat/cleaning/profiling.py`, `tests/test_profiling.py` |

### Public API policy

- `BaseData(data)`, `JsonFile`, and `CsvFile` remain supported public APIs.
- `df.bynat` is an optional, complementary interface, not a replacement for the class-based API.
- `inplace` remains supported wherever meaningful; its behavior and return contract must be consistent and documented.
- Shared implementations should prevent the class-based API and accessor from drifting apart.
- Roadmap tasks must preserve these decisions unless a decision is explicitly revisited and approved.

## 4. Backlog

**Priority:**
- **P1** correctness or data safety
- **P2** performance or maintainability
- **P3** nice to have

**Effort:**
- **S** under a day
- **M** a few days
- **L** a week or more.

### Next minor release

### Milestone 7 (`0.7.0`): analytics integration

#### `ANALYTICS-01`: integrate the analytics library (P1, M)
- **Problem:** the existing analytics library is separate from `bynat`, so its functionality is not yet available through the package's public API.
- **Plan:** integrate the existing library under `bynat.analytics`, preserving its current behavior and defining clear public API and output contracts. Keep analytics logic independent of application-specific integrations such as Power BI.
- **Done when:** the analytics functionality is accessible through the documented package API, existing behavior is preserved, and tests cover public interfaces and output contracts.

### Milestone 8 (`0.8.0`): data quality

#### `DQ-01`: DataFrame quality profiling (P1, M)
- **Problem:** users need a structured way to assess data quality before cleaning or analysis without manually combining checks for missing values, duplicates, column types, and descriptive statistics.
- **Plan:** add a profiling API that produces a structured report covering missing values, duplicate rows, column types, and relevant descriptive statistics. Keep profiling non-mutating and make its output suitable for programmatic use.
- **Done when:** the report has a documented, stable structure, covers supported data types and edge cases, and tests verify its results without modifying the input.

#### `DQ-02`: declarative DataFrame quality checks (P1, M)
- **Problem:** users need to define and evaluate data quality requirements consistently instead of implementing ad hoc validation logic for every dataset.
- **Plan:** add declarative checks for required columns, missing-value policies, uniqueness, numeric ranges, and allowed values. Return structured validation results identifying failed checks and relevant violations. Keep validation separate from automatic cleaning.
- **Done when:** supported rules can be declared and evaluated through a documented API, results identify failures clearly, and tests cover valid data, invalid data, and edge cases without modifying the input.

### Milestone 9 (`0.9.0`): API maintenance

#### `DEPR-02`: standardize transformation behavior and maintenance boundaries (P2, S)
- **Problem:** transformation methods still need one consistent `inplace` convention, while the supported class-based API and its implementation module must remain part of the public surface.
- **Plan:** standardize `inplace` behavior according to `API-02`, without deleting `BaseData`, `JsonFile`, `CsvFile`, or `bynat/cleaning/base.py`. Add the remaining helper modules to the mypy scope; the class wrappers may remain a separate gradual-typing boundary. Keep the `bynat.cleaning.Cleaning` and `bynat.caching.Caching` aliases for at least one additional minor release.
- **Done when:** the `inplace` mutation and return behavior is documented and tested consistently, the supported class API and imports continue to work, the remaining helper modules are covered by mypy, the aliases remain available for the stated period, and the full test suite and mypy checks pass.

#### `PERF-05`: rates through the engine (P3, S)
- **Problem:** `convert_to_usd` resolves currency codes through `_cached_map`, but year-range rate downloads remain a dedicated loop because each fetch populates multiple `CODE_YEAR` keys.
- **Plan:** revisit integration if the engine gains a multi-key store. Keep the existing dedicated download loop until that abstraction provides a clear benefit.
- **Done when:** the existing implementation remains documented as intentional or a multi-key engine integration is implemented with equivalent results and tests.

### Milestone 10 (`1.0.0`): I/O and compatibility

#### `IO-01`: append-friendly output (P3, M)
- **Problem:** `save_to_json(append=True)` re-reads and rewrites the entire file, making repeated appends increasingly expensive (`bynat/cleaning/io.py`).
- **Plan:** add an append-friendly output option, such as JSON Lines or SQLite, that avoids rewriting the complete dataset on each append while preserving the existing JSON behavior.
- **Done when:** repeated appends do not require rewriting the entire accumulated dataset, output can be read back correctly, and tests cover appends, existing files, and failure cases.

#### `COMPAT-01`: Python 3.10 support (P3, S)
- **Problem:** the package currently requires Python 3.11 or newer, limiting use in Python 3.10 environments.
- **Plan:** evaluate compatibility using `typing.Self` from `typing_extensions` and a dedicated Python 3.10 CI run before changing `requires-python`.
- **Done when:** the test suite passes on Python 3.10, compatibility requirements are satisfied, and package metadata is updated only if support is confirmed.

## 5. Decisions

- **D-01: `group_key` is always a tuple.** One value per hierarchy column, including single-column levels (`("EG",)`), as pinned by `tests/test_hierarchical_stats.py`. Resolved: keep it.
- **D-02: `mean` and `median` on non-numeric targets raise pandas' `TypeError`.** Resolved: keep failing fast.
- **D-03: return convention with supported `inplace`.** Resolved: class-based callers remain supported, and transformations return a new object by default, including from early exits. Where an operation supports in-place behavior, `inplace=True` mutates the original object and returns `None`; methods that produce an additional documented result return that result instead. `inplace` is not scheduled for deprecation or removal.
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
