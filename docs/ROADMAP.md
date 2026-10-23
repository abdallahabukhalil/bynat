# bynat Roadmap

This roadmap is written against the code as it is, not as it might be. Every item names the real method, file, or test it refers to. Line numbers are as of `0.2.0` and drift; method names are the stable reference. If an item stops matching the code, fix the item before working on it.

## 1. Project context

`bynat` is a small utility library (about 3,200 lines in `bynat/cleaning/Cleaning.py` and 260 in `bynat/caching/Caching.py`).

- **`bynat.cleaning.BaseData`** subclasses `pandas.DataFrame` and adds column helpers, text extraction, hierarchical outlier handling, imputation and statistics, external-service enrichment (translation, geocoding, currency, spaCy), and JSON/SQL export. `JsonFile` and `CsvFile` are loading subclasses.
- **`bynat.caching.CacheManager`** keeps one JSON file per function (`<func_name>_cache.json`) holding nested dictionaries keyed by column. Its public methods are `load_function_cache`, `get_column_cache`, `save_function_cache`, and `delete_column_cache`. There is no `get`, `set`, `clear`, or TTL API.

## 2. Principles

1. **No row-wise iteration over frames in data paths.** `DataFrame.apply(..., axis=1)`, `iterrows`, and `itertuples` are not allowed. Column-wise `DataFrame.apply`, `Series.map`, groupby, and vectorized string operations are fine. Python-level loops over **unique values** are expected wherever each value needs an external call or a Python object (translation, geocoding, Aho-Corasick, JSON keys for unhashable cells). Performance changes must come with a benchmark and an equivalence test.
2. **Fail loudly on caller errors, stay lenient on data.** Invalid arguments raise. Missing columns are skipped with a logged warning (the documented contract, see README). Per-item external failures are logged and skipped, never cached.
3. **Backward compatibility is explicit.** Behavior changes are listed in `CHANGELOG.md` under *Breaking changes* and ship in a new minor version (`0.x`). Prefer a deprecation path when it is cheap.
4. **Minimal footprint.** Core dependencies stay `numpy` and `pandas`. Heavy integrations stay optional and are imported lazily with `_import_optional`. Prefer the standard library (`sqlite3`, `logging`, `tempfile`) over new dependencies.
5. **Tests do not touch the network.** External services are replaced with fake modules (`tests/test_integrations.py`). Light extras with offline behavior (`pyahocorasick`, SQLAlchemy with SQLite) are also tested for real when installed.

## 3. Current state (`0.2.0`)

- **Python:** `>=3.11`. CI runs Ubuntu and Windows on 3.11 and 3.13, a Ruff lint job, and an extras job (`.[categorization,sql]`).
- **Tests:** 72 collected. `test_caching.py` 11, `test_cleaning.py` 26, `test_hierarchical_stats.py` 27, `test_integrations.py` 8. Three are skipped unless the `categorization` and `sql` extras are installed.
- **Lint:** `ruff check bynat tests` is clean with `E`, `F`, `W`, `I`, `UP` (`E501` ignored; see `pyproject.toml`).
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

## 4. Backlog

Priority: **P1** correctness or data safety, **P2** performance or maintainability, **P3** nice to have. Effort: **S** under a day, **M** a few days, **L** a week or more.

### Milestone 3 (`0.3.0`): cache and enrichment engine

#### `CACHE-01`: SQLite cache backend (P1, M)
- **Problem:** `save_function_cache` rewrites the entire JSON file at every checkpoint, which costs O(n) per save and O(n²) over a long run. Concurrent writers to one cache still lose each other's updates; atomic replace does not prevent that.
- **Plan:** a `sqlite3` key-value table per cache directory (`func_name`, `bucket`, `key`, `value_json`, `created_at`). SQLite provides atomic commits and cross-process locking with no new dependency, so there is no hand-rolled lock-directory protocol. Keep `get_column_cache` returning a dict-like object so call sites do not change. Read existing `*_cache.json` files once and import them.
- **Done when:** a multi-process test writing disjoint keys loses nothing, an interrupted run keeps every committed checkpoint, and existing JSON caches are migrated by a test fixture.
- **Not doing:** a versioned JSON envelope with `__bynat_type__` tags. Values the methods store are strings, numbers, `None`, and `[lat, lng]` pairs, which JSON already handles.

**Execution policy:** Complete one checklist item at a time. Run the relevant tests or checks after each item, mark an item `[x]` only after its work and verification succeed, and leave it `[ ]` when work is partial or verification fails. Record blockers instead of marking incomplete work complete. Resume from the first unchecked item across sessions. The `CACHE-01` entry itself is not complete until every required subtask and every acceptance criterion above has been verified; checked subtasks must not be treated as milestone completion.

**Implementation checklist:**

- [x] **Resolve design decisions.**
  - [x] Define deletion semantics: `delete_column_cache` removes only rows represented by the manager's last committed baseline for that function and bucket. Concurrent rows added after that baseline are preserved rather than being deleted by a stale bucket operation.
  - [x] Define conflict semantics: concurrent writes to different `(func_name, bucket, key)` identities are merged; concurrent writes to the same identity use last committed writer wins. No silent conflict-resolution or retry policy is added to the public API.
  - [x] Define malformed and unsupported JSON handling: invalid JSON or a non-object top level is quarantined using the existing `.corrupt.<timestamp>` behavior; unsupported nested bucket shapes are not imported or marked migrated, the source remains available for recovery, and the failure is logged explicitly rather than silently discarded.
  - [x] Define save failures: preserve the current non-raising, logged-error behavior; roll back the SQLite transaction; and leave the in-memory baseline snapshot unchanged so the same pending delta can be retried.
  - [x] Confirm transaction and initialization policy: lazily create the cache directory/database, use one explicit transaction per migration/save/delete operation, serialize writers with `BEGIN IMMEDIATE`, configure a finite busy timeout, enable WAL journaling, and use durable synchronous commits.
  - [x] Confirm migration policy: guard migration with SQLite metadata, re-check the marker inside the write transaction, use idempotent inserts that never overwrite existing SQLite rows, and write the marker only after the complete migration commits.
  - [x] Confirm compatibility: keep `load_function_cache`, `get_column_cache`, `save_function_cache`, and `delete_column_cache`; return ordinary mutable dictionaries; preserve direct indexing, assignment, membership, `.get`, iteration/equality, and explicit `None` values.
  - [x] Record these decisions in the `CACHE-01` checklist and Decisions section below.
- [x] **Implement SQLite infrastructure.**
  - [x] Define the cache database path and lazy initialization without creating the cache directory during `CacheManager` construction.
  - [x] Create the cache-entry and migration-metadata tables with the required primary key and `(func_name, bucket)` access index.
  - [x] Implement connection configuration, explicit transaction handling, busy timeout, WAL mode, and durable synchronous behavior.
- [x] **Implement legacy JSON migration.**
  - [x] Detect each legacy `<func_name>_cache.json` source and record completed migrations in SQLite metadata.
  - [x] Import valid nested bucket entries transactionally without overwriting existing SQLite rows.
  - [x] Make migration retryable after parse, validation, insertion, or commit failures.
  - [x] Handle concurrent initialization safely through SQLite transactions rather than a lock-directory protocol.
  - [x] Preserve corrupt and unsupported JSON sources and log actionable failures.
- [x] **Implement cache API integration.**
  - [x] Reconstruct SQLite rows into the existing function-level and bucket-level dictionary shape.
  - [x] Preserve the behavior and public signatures of all four `CacheManager` methods.
  - [x] Track deep baseline snapshots for loaded and successfully committed function caches.
  - [x] Save only added, changed, and removed entries so stale in-memory snapshots cannot erase disjoint concurrent updates.
  - [x] Ensure bucket deletion cannot erase unrelated concurrent rows.
- [x] **Add and update focused tests.**
  - [x] Preserve existing API, validation, round-trip, corruption, serialization-error, and deletion regression coverage.
  - [x] Add multi-process tests for disjoint-key writes and same-key last-commit-wins behavior.
  - [x] Add migration tests for valid data, idempotency, existing SQLite conflicts, concurrent initialization, retry after failure, and malformed/unsupported JSON.
  - [x] Add interruption and failed-transaction tests proving previously committed checkpoints remain available.
  - [x] Add deletion-semantic tests covering concurrent additions and unrelated buckets.
- [ ] **Update documentation.**
  - [ ] Update the README caching section and operational notes after the implemented behavior is verified.
  - [ ] Update `CHANGELOG.md` only after the implementation and acceptance tests are complete.
  - [ ] Keep this checklist accurate after every completed step.
- [ ] **Run final verification and review.**
  - [ ] Run the complete pytest suite.
  - [ ] Run `compileall`, Ruff, and repository diff/scope checks.
  - [ ] Review the implementation for scope creep, stale-snapshot data loss, migration data loss, and API regressions.
  - [ ] Verify every original `CACHE-01` acceptance criterion and only then move the completed item to the roadmap's completed-milestones table.

#### `ENRICH-01`: one cached-lookup engine with injectable providers (P2, M)
- **Problem:** `translate_categorical_column`, `translate_conditional_column`, `fill_missing_coordinates`, `convert_to_usd`, and `extract_entities_nlp` each re-implement the same loop: unique values, cache lookup, fetch misses, checkpoint every `save_every`, map back. Providers are hard-coded (`GoogleTranslator`, `Nominatim`, `yfinance`).
- **Plan:** `_cached_map(series, fetch, bucket, save_every)` plus small `Protocol`s (`Translator`, `Geocoder`, `RateSource`) with the current services as defaults, accepted through an optional `provider=` argument. This replaces the fake-module patching in tests with plain provider objects.
- **Done when:** the five methods use the engine, behavior matches the existing `tests/test_integrations.py`, and a custom provider can be passed without monkeypatching.

#### `PERF-01`: batch external calls (P2, S)
- `translate_*`: replace the sequential `translate` plus `time.sleep(0.2)` (lines 1314, 1437) with `translate_batch` or a small rate-limited thread pool.
- `convert_to_usd`: replace one `yfinance.Ticker(...).history` per currency and year (line 1821) with one download per currency across the needed year range, then yearly means.
- **Done when:** fake-provider tests show one call per currency, and output matches the current tests.

#### `PERF-02`: `extract_keywords` on unique texts with one pattern (P2, S)
- **Problem:** one regex pass over the full column per keyword (line 893), plus one per keyword for removal, all on rows rather than unique texts. Cost is O(keywords × rows).
- **Plan:** work on `combined_text.unique()` and map back, as `categorize_by_reference` already does, using a single alternation (or Aho-Corasick when the extra is installed).
- **Done when:** an equivalence test passes over keyword priority, `use_regex`, `remove_extracted`, and repeated calls into an existing column, plus a benchmark at 10k, 100k, and 1M rows.

#### `PERF-03`: `translate_conditional_column` partition sampling (P2, S)
- **Problem:** `result[result[partition_col] == partition]` inside a loop is O(partitions × rows) (line 1254). `available_texts.sample(n=...)` (line 1263) has no `random_state`, so results are not reproducible even though the `langdetect` seed is fixed.
- **Plan:** `groupby(partition_col)` and a `random_state` parameter (default `0`).

#### `PERF-04`: one-pass hierarchical IQR (P2, S)
- **Problem:** `handle_outliers` and `drop_outliers` are near-duplicates (lines 1863 and 1961). Each hierarchy level runs `groupby` twice plus a merge, and both carry dead `level` and `col_outliers_count` bookkeeping.
- **Plan:** one private generator of per-level outlier masks, with `groupby(...).quantile([0.25, 0.75])` mapped back through `transform`. The two public methods become thin wrappers.
- **Done when:** existing outlier tests pass unchanged and a benchmark is recorded.

### Milestone 4 (`0.4.0`): API shape. Each item needs decision D-03 first.

#### `API-01`: pandas accessor (P2, L)
- **Problem:** subclassing `DataFrame` is fragile. `_constructor` (line 124) builds a new `CacheManager` on every pandas operation (line 154), `_metadata` is dropped by many operations, and pandas recommends accessors for extensions.
- **Plan:** register `df.bynat.<method>()` via `pd.api.extensions.register_dataframe_accessor`, sharing implementations with `BaseData`. Keep `BaseData`, `JsonFile`, and `CsvFile` as thin deprecated wrappers for one minor version. Replace the loader subclasses with `bynat.read_json` and `bynat.read_csv` helpers.

#### `API-02`: retire `inplace` (P2, M)
- **Problem:** every transformation accepts `inplace`, but the early-exit paths return `self` (an alias) while normal paths return a copy (`return self if not inplace else None`, 15 sites starting at line 312). `split_list_objects(inplace=True, explode_rows=True)` cannot work, as its docstring admits. pandas itself is moving away from `inplace` under Copy-on-Write.
- **Plan:** always return a new object. Emit a `DeprecationWarning` when `inplace=True` is passed, and remove it in the following minor version.

#### `ARCH-01`: split `Cleaning.py` into modules (P2, M)
- **Plan:** `cleaning/columns.py` (rename, drop, reorder, move), `text.py` (keywords, truncate, categorize), `stats.py` (outliers, `fill_missing`, `get_hierarchical_stats`), `enrich/` (translate, geocode, currency, nlp, engine), and `io.py` (JSON, SQL, loaders). `bynat.cleaning` re-exports the same names, so imports do not change. Rename `Caching.py` and `Cleaning.py` to lowercase module names, with shims.
- **Done when:** the module move is a pure refactor with no behavior diff and the full suite passes unchanged.

#### `QA-01`: Mypy (P3, M)
- Add `mypy` for `bynat/caching` first, then the cleaning helpers. Typing a `DataFrame` subclass is noisy, so do this after `API-01`.

### Later or optional

- `DEPR-01` (P3, S): deprecate the misspelled `extract_entities_nlp(noisey_terms=...)` alias with a warning.
- `IO-01` (P3, M): an append-friendly output (JSON Lines or SQLite) so `save_to_json(append=True)` does not re-read and rewrite the whole file (line 2887).
- `COMPAT-01` (P3, S): Python 3.10. Low value. It needs `typing.Self` from `typing_extensions` and a CI run before any `requires-python` change.

## 5. Decisions

- **D-01: `group_key` is always a tuple.** One value per hierarchy column, including single-column levels (`("EG",)`), as pinned by `tests/test_hierarchical_stats.py`. Resolved: keep it.
- **D-02: `mean` and `median` on non-numeric targets raise pandas' `TypeError`.** Resolved: keep failing fast.
- **D-03: return convention without `inplace`.** Open. Recommended: always return a new object (see `API-02`), and stop returning aliases from early exits. This changes behavior for callers who rely on `inplace=True`, so it needs the maintainer's approval.
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
