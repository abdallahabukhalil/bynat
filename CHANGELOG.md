# Changelog

## 0.3.0

This release adds a SQLite-backed caching system with automatic migration from legacy JSON caches, safer concurrent cache updates, and improved cache persistence, alongside optional hierarchical statistics in hierarchy-aware cleaning methods and configurable spaCy pipeline components, while preserving existing default behavior.

### Breaking changes

None.

### Added

- `handle_outliers`, `drop_outliers`, and `fill_missing` accept `get_hierarchical_stats=False`. When enabled, they reuse `get_hierarchical_stats()` and return original-data hierarchical statistics alongside non-inplace results, or directly for inplace calls.
- `extract_entities_nlp` accepts configurable spaCy pipeline components through `disable`, rejects components required for lemmatization, warns for unknown names, and isolates caches by target column, model, and normalized configuration.

### Fixed

None.

### Changed

- Removed the deprecated `noisey_terms` parameter; use `noisy_terms` instead.
- `CacheManager` stores each function cache as rows in a SQLite database named `cache.sqlite3` instead of one JSON file per function, while keeping `load_function_cache`, `get_column_cache`, `save_function_cache`, and `delete_column_cache` and their ordinary dictionary results.
- Legacy `<func_name>_cache.json` files are migrated into SQLite at most once; existing SQLite rows take precedence over stale JSON; malformed or non-object JSON is quarantined to a `.corrupt` path for recovery, while valid JSON with unsupported nested bucket shapes remains in place, unmarked, for recovery.
- `save_function_cache` writes only the added, changed, and removed entries in one transaction, so a failed save keeps the previous state for retry, concurrent writers merge disjoint keys, and same-key writes take the last committed value.
- Deleting a column bucket removes only the entries known when the cache was loaded, so rows added concurrently or in unrelated buckets are preserved.

## 0.2.0

This release changes how problems are reported. Review the **Breaking changes** before upgrading.

### Breaking changes

- **Messages use `logging` instead of `print`.** Everything is logged under the `bynat` logger. Warnings (skipped columns, failed lookups) still appear on stderr by default; progress and checkpoint messages are `INFO` and hidden unless you enable them, e.g. `logging.basicConfig(level=logging.INFO)`. Code that captured stdout to read these messages must read log records instead.
- **Invalid arguments raise instead of printing and returning unchanged data:**
  - `fill_missing` and `get_hierarchical_stats` raise `ValueError` for an unknown `strategy`.
  - `save_to_json` raises `ValueError` for an empty `file_path`, and when `append=True` but the existing file cannot be read with the given `orient`. It previously overwrote that file, losing its contents.
  - `save_to_sql` raises `ValueError` for missing or invalid connection settings, and lets database errors propagate.
  - `extract_entities_nlp` raises `ValueError` when no target columns are given, no terms match the requested lexicon keys, or `spacy_model` is empty. Lexicon-file and spaCy-model loading errors propagate (`OSError`, `json.JSONDecodeError`) instead of producing empty lists.
- **Corrupted cache files are moved aside, not overwritten.** A cache file that is not a valid JSON object is renamed to `<name>.corrupt.<timestamp>` before an empty cache takes its place.

### Added

- `BYNAT_CACHE_DIR` environment variable: used when `cache_dir` is omitted. The default `cache_dir` is now `None`, which falls back to `BYNAT_CACHE_DIR` and then to the previous `Settings/Cache Data/Automated`, so existing caches keep working.

### Fixed

- `save_to_sql` now disposes its SQLAlchemy engine, so SQLite files are not left locked (notably on Windows).

## 0.1.2

Fixes and hardening with no intended API breaks.

### Fixed

- `save_to_json(append=True)` no longer discards the existing file when a column holds lists or dictionaries. Such rows are deduplicated by their JSON content; frames without them keep pandas' fast path.
- `handle_outliers`, `drop_outliers`, `fill_missing(strategy="mode")`, and `convert_to_usd` work with named, multi-level, and duplicate indexes (previously `KeyError: "None of ['index'] are in the columns"`).
- `drop_outliers` drops rows by position, so an outlier with a duplicated index label no longer removes its twin row.
- `fill_missing_coordinates` now shortens failed queries one part at a time (`"street, city, country"` → `"city, country"` → `"country"`) as documented, instead of jumping straight to the last part.
- Cache saves are atomic (temporary file + `os.replace`). An interrupted or failed save keeps the previous cache file instead of leaving it truncated.
- Bare `except:` clauses were narrowed, so `KeyboardInterrupt` is no longer swallowed during language detection and currency lookup.

### Changed

- Lookups that definitively fail are cached and not re-queried on later runs: geocoding queries Nominatim cannot resolve, countries `pycountry` cannot match, and past years with no exchange-rate data. Network and service errors are still not cached.
- `extract_entities_nlp` disables spaCy's `parser` and `ner` components when processing input texts; only the tagger and lemmatizer are needed.

### Added

- `fill_missing_coordinates(user_agent=..., retry_not_found=False)`.
- README documentation for `get_hierarchical_stats`.
- Offline tests for translation, geocoding, currency conversion, and NLP extraction using fake modules; tests against real `pyahocorasick` and SQLAlchemy/SQLite when those extras are installed.
- Ruff linting (`ruff check bynat tests`) and a CI matrix on Ubuntu and Windows with Python 3.11 and 3.13, plus an extras job.
