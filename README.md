# bynat

`bynat` is a Python utility package for tabular-data cleaning and small SQLite-backed caches. Its cleaning API is a `df.bynat` accessor on ordinary `pandas.DataFrame` objects; the caching API stores per-function dictionaries in a SQLite database.

## Status

Version `0.3.0` is the project version declared in `pyproject.toml`. See [CHANGELOG.md](CHANGELOG.md) for behavior changes since `0.1.1` and [ROADMAP.md](./docs/ROADMAP.md) for planned work.

## Installation

```bash
python -m pip install bynat
```

For a checkout, install it in editable mode:

```bash
python -m pip install -e .
```

### Requirements

- Python `>=3.11`.
- Mandatory runtime distributions: `numpy` and `pandas`.
- Optional integrations are grouped in `pyproject.toml`:
  - `translation`: `deep-translator`, `langdetect`
  - `geocoding`: `geopy`
  - `currency`: `Babel`, `pycountry`, `yfinance`
  - `categorization`: `pyahocorasick` for the `ahocorasick` import
  - `nlp`: `spacy`
  - `sql`: `SQLAlchemy`

Install an integration group when needed, for example:

```bash
python -m pip install "bynat[translation]"
```

These integrations are imported only when their methods are used. Basic imports and core dataframe operations do not require them. Calling a method that needs a missing integration raises an `ImportError` naming the required optional dependency. Install the extra corresponding to the feature you use.

`extract_entities_nlp` also needs a compatible spaCy model, defaulting to `en_core_web_sm`. `save_to_sql` needs the DBAPI driver required by the SQLAlchemy URL. Neither model files nor database drivers are bundled.


## Public API

```python
import bynat                          # registers the df.bynat accessor
from bynat import read_csv, read_json
from bynat.caching import CacheManager, shared_cache_manager
from bynat.cleaning.enrich import Geocoder, RateSource, Translator
```

- `bynat`: `read_json`, `read_csv`, `BynatAccessor`. Importing `bynat` (or any submodule) registers the `bynat` accessor on `pandas.DataFrame`.
- `bynat.caching`: `CacheManager`, `shared_cache_manager`, `CACHE_DIR_ENV_VAR`, `DEFAULT_CACHE_DIR`
- `bynat.cleaning`: `BynatAccessor`, `read_json`, `read_csv`, and the deprecated `BaseData`, `JsonFile`, `CsvFile`
- `bynat.cleaning.enrich`: the provider protocols `Translator`, `Geocoder`, `RateSource` and their defaults `GoogleTranslatorProvider`, `NominatimGeocoder`, `YFinanceRateSource`

### Module layout

| Module | Contents |
| --- | --- |
| `bynat.caching.manager` | `CacheManager` and `shared_cache_manager` |
| `bynat.cleaning.columns` | Column renaming, dropping, reordering, moving, `melt_transform_pivot`, `split_list_objects` |
| `bynat.cleaning.text` | `extract_keywords`, `truncate_after_substring`, `categorize_by_reference` |
| `bynat.cleaning.stats` | `handle_outliers`, `drop_outliers`, `fill_missing`, `get_hierarchical_stats` |
| `bynat.cleaning.enrich` | `translate`, `geocode`, `currency`, `nlp`, the shared cached-lookup `engine`, and `providers` |
| `bynat.cleaning.io` | `save_to_json`, `save_to_sql`, `read_json`, `read_csv` |
| `bynat.cleaning.accessor` | The `df.bynat` accessor |
| `bynat.cleaning.base` | The deprecated `BaseData`, `JsonFile`, `CsvFile` wrappers |

Each function in `columns`, `text`, `stats`, `enrich`, and `io` takes the DataFrame as its first argument and returns a new one; the accessor and the deprecated classes are thin layers over them. The legacy module paths `bynat.caching.Caching` and `bynat.cleaning.Cleaning` still import and resolve to `bynat.caching.manager` and `bynat.cleaning.base`.

## Caching

### `CacheManager`

`CacheManager` stores each function's cache as rows in a SQLite database named `cache.sqlite3` under `cache_dir`. When `cache_dir` is omitted (here and in `BaseData`, `JsonFile`, and `CsvFile`), the `BYNAT_CACHE_DIR` environment variable is used if set, otherwise `Settings/Cache Data/Automated`. Relative cache paths are resolved from the current working directory; `~` and environment variables are expanded. The cache directory and database are created lazily: constructing a manager creates neither, and the SQLite backend is created on first cache access or on the first save of in-memory changes. Legacy `<func_name>_cache.json` files, if present, are read only for a one-time migration into SQLite.

```python
from pathlib import Path
from bynat.caching import CacheManager

cache = CacheManager(Path(".cache") / "bynat")
translations = cache.get_column_cache("translate_categorical_column", "status")
translations["pending"] = "Pending"
cache.save_function_cache("translate_categorical_column")

# Load the same function cache later.
loaded = cache.load_function_cache("translate_categorical_column")
assert loaded["status"]["pending"] == "Pending"
```

Cache keys must be non-empty strings and cannot contain path separators. Values are JSON-serialized inside SQLite rows, so supported values stay JSON-compatible: strings, numbers, `None`, lists, and dictionaries. `save_function_cache` applies the added, changed, and removed entries in one SQLite transaction; serialization, locking, or commit failures are logged rather than raised, committed rows stay intact, and the pending in-memory changes are kept so the next save retries them. A legacy `<func_name>_cache.json` file is migrated into SQLite at most once: existing SQLite values take precedence over stale JSON, malformed or non-object files are quarantined to a `.corrupt` name for manual recovery, and unsupported nested structures are left in place, unmarked, for recovery.

### CacheManager methods and parameters

| Method | Parameters | Behavior |
| --- | --- | --- |
| `load_function_cache(func_name)` | `func_name: str` | Return the function's in-memory cache loaded from SQLite, creating the backend on first access; any legacy JSON is migrated once, and a function with no entries returns an empty dictionary. |
| `get_column_cache(func_name, col_name)` | `func_name: str`, `col_name: str` | Return or create the ordinary mutable nested dictionary under `col_name`. |
| `save_function_cache(func_name)` | `func_name: str` | Write the added, changed, and removed entries to SQLite in one transaction; log failures without raising and keep pending changes for retry. |
| `delete_column_cache(func_name, col_name)` | `func_name: str`, `col_name: str` | Delete a nested bucket and save; the deletion removes only rows the manager already knew about, preserving concurrent additions and unrelated buckets or functions. Logs an `INFO` message when the bucket is absent or deleted. |

`_validate_cache_key` and `_get_file_path` are implementation helpers and are not part of the public API.

## Cleaning

After `import bynat`, every `pandas.DataFrame` has a `bynat` accessor. Each method returns a new DataFrame and never modifies the original, including when there is nothing to change. Methods that perform translation, geocoding, NLP extraction, or exchange-rate lookup use one shared `CacheManager` per cache directory: the `BYNAT_CACHE_DIR` directory or the default, unless `df.bynat.with_cache(...)` selects another directory or an explicit `CacheManager`.

### Practical examples

Basic column cleaning and reshaping:

```python
import bynat
import pandas as pd

records = pd.DataFrame({
    "user.display_name": ["Ada", "Grace"],
    "user_id": [1, 2],
    "description": ["Blue shoes", "Red shirt"],
})

cleaned = records.bynat.rename_all_DOTS().bynat.drop_by_substring("user")

labeled = records.bynat.extract_keywords(
    "description",
    colors=["blue", "red"],
)
```

Load files with the loader helpers, which return plain DataFrames:

```python
import bynat

from_csv = bynat.read_csv("input.csv", sep=";")
from_json = bynat.read_json("input.json", key="items")

from_csv.bynat.save_to_json("output.json", orient="records")
```

Hierarchical statistics compute one statistic per group at every level of a hierarchy, from the full hierarchy down to its first column. Pass one strategy or a list of `median`, `mean`, and `mode` (`mod` is accepted as an alias):

```python
import bynat
import pandas as pd

salaries = pd.DataFrame({
    "country": ["EG", "EG", "EG", "US", "US"],
    "title": ["Analyst", "Analyst", "Engineer", "Analyst", "Analyst"],
    "salary": [10.0, 14.0, 30.0, 70.0, 90.0],
})

stats = salaries.bynat.get_hierarchical_stats(
    "salary",
    strategy=["median", "mode"],
    hierarchy_cols=["country", "title"],
)
```

`stats` is returned as the caller's class (here `pandas.DataFrame`) with the columns `hierarchy_level`, `hierarchy_columns`, `group_key`, `target_column`, `strategy`, and `statistic`. The median rows are:

| hierarchy_level | group_key | strategy | statistic |
| --- | --- | --- | --- |
| 2 | `("EG", "Analyst")` | median | 12.0 |
| 2 | `("EG", "Engineer")` | median | 30.0 |
| 2 | `("US", "Analyst")` | median | 80.0 |
| 1 | `("EG",)` | median | 14.0 |
| 1 | `("US",)` | median | 80.0 |

`group_key` is always a tuple with one value per hierarchy column, including single-column levels such as `("EG",)`. Null group keys are kept. `mean` and `median` need numeric targets and raise pandas' `TypeError` otherwise; `mode` works for any hashable values and breaks ties by first occurrence. Choose another container with `output_type`:

```python
as_frame = salaries.bynat.get_hierarchical_stats("salary", hierarchy_cols="country", output_type=pd.DataFrame)
as_dict = salaries.bynat.get_hierarchical_stats("salary", hierarchy_cols="country", output_type=dict)
# as_dict["group_key"] == [("EG",), ("US",)]; as_dict["statistic"] == [14.0, 80.0]
```

`output_type` accepts `None` (default, the caller's class), `pandas.DataFrame`, `dict` (column name to list of values), or a `BaseData` subclass. Target columns cannot also be hierarchy columns.

Network-backed enrichment is explicit in the method behavior and should be used with an appropriate cache directory and service credentials/configuration where applicable:

```python
translated = records.bynat.with_cache(".cache/bynat").translate_categorical_column(
    "category",
    target_lang="en",
    save_every=100,
)
```

### External-service providers

Translation, geocoding, and currency conversion call external services through small provider protocols in `bynat.cleaning.enrich`. The defaults are used unless a method receives `provider=`:

| Protocol | Method a provider defines | Default | Accepted by |
| --- | --- | --- | --- |
| `Translator` | `translate(text, target_lang) -> str \| None`, optionally `translate_batch(texts, target_lang) -> list` | `GoogleTranslatorProvider` (`deep-translator`) | `translate_categorical_column`, `translate_conditional_column` |
| `Geocoder` | `geocode(query) -> (latitude, longitude) \| None` | `NominatimGeocoder` (`geopy`, one request per second) | `fill_missing_coordinates` |
| `RateSource` | `yearly_rates(currency, start_year, end_year) -> {year: rate}` | `YFinanceRateSource` (`yfinance`) | `convert_to_usd` |

A provider is any object with the right method; no subclassing or monkeypatching is needed:

```python
class UpperTranslator:
    def translate(self, text, target_lang):
        return text.upper()

df.bynat.translate_categorical_column("status", provider=UpperTranslator())
```

Translators with `translate_batch` receive the values in chunks of `save_every`. Otherwise `translate` runs in a thread pool sized by the provider's optional `max_workers` attribute (default 1), with request starts spaced by its optional `min_interval` attribute in seconds (default 0). `GoogleTranslatorProvider` uses four workers and at most five requests per second. A falsy translation or a raised exception leaves that value untranslated and uncached. `convert_to_usd` makes one `yearly_rates` call per currency, covering every year its look-back can need.

All four cached methods, and `extract_entities_nlp`, run through one engine: unique non-null values are looked up in the cache, the misses are fetched, the cache is checkpointed every `save_every` values, and failures are logged and left uncached so the next run retries them.

### Deprecated classes

`BaseData`, `JsonFile`, and `CsvFile` still work for one more minor version and emit a `DeprecationWarning` when constructed directly. They are thin wrappers over the same functions as the accessor.

| Class | Constructor parameters | Replacement |
| --- | --- | --- |
| `BaseData` | `data=None`, `cache_dir=None`, `*args`, `**kwargs` | `pandas.DataFrame(...)` with `df.bynat`; `df.bynat.with_cache(cache_dir)` for a cache directory |
| `JsonFile` | `data=None`, `json_file_path=None`, `key=None`, `cache_dir=None`, `*args`, `**kwargs` | `bynat.read_json(json_file_path, key=None)` |
| `CsvFile` | `data=None`, `csv_file_path=None`, `cache_dir=None`, `read_kwargs=None`, `*args`, `**kwargs` | `bynat.read_csv(csv_file_path, **read_kwargs)` |

Migration:

```python
# Before
from bynat.cleaning import JsonFile
jobs = JsonFile(json_file_path="jobs.json", key="items", cache_dir="cache")
jobs = jobs.rename_all_DOTS()
jobs.translate_categorical_column("status", inplace=True)

# After
import bynat
jobs = bynat.read_json("jobs.json", key="items")
jobs = jobs.bynat.rename_all_DOTS()
jobs = jobs.bynat.with_cache("cache").translate_categorical_column("status")
```

Every `BaseData` method below also accepts `inplace=False`. **`inplace=True` is deprecated**: it still modifies the object and returns `None` (or the statistics, see below), but emits a `DeprecationWarning` and will be removed in the next minor release. Without it, every method returns a new object, including early exits that previously returned the original object itself. `BaseData` creates its `CacheManager` on first use and shares it with every frame pandas derives from it.

### Methods and parameters

The accessor and the deprecated classes expose the same methods. Accessor methods take no `inplace` argument.

| Method | Parameters | Purpose and important behavior |
| --- | --- | --- |
| `with_cache` (accessor only) | `cache` | Return an accessor that caches in another directory or `CacheManager`. |
| `clear_cache_for_column` | `func_name`, `target_col` | Delete one cache bucket through the attached `CacheManager`. |
| `view` (`BaseData` only) | none | Return a plain `pandas.DataFrame` view/copy of the object. |
| `drop_by_substring` | `substring` | Drop every column whose name contains `substring`. |
| `drop_CLASS_columns` | none | Drop columns containing the literal substring `__CLASS__`. |
| `rename_by_substring` | `old_substring`, `new_substring` | Replace occurrences of one substring in column names. |
| `rename_all_DOTS` | `unified_separator="_"` | Replace dots in all matching column names. |
| `reorder_columns` | `*first_cols` | Move the named columns to the front while preserving the order of other columns. Pass column names as positional arguments. |
| `melt_transform_pivot` | `id_vars`, `value_vars=None`, `transform_func=None`, `aggfunc="first"`, `var_name="variable_name"`, `value_name="variable_value"`, `pivot_index=None`, `pivot_columns=None`, `**kwargs` | Melt selected columns, optionally transform the melted value series, and pivot back. `transform_func` must return the same number of values. |
| `move_column` | `col_to_move`, `col_target`, `position="after"` | Move one column before or after another. `position` must be `"before"` or `"after"`. |
| `align_lat_lng` | none | Move exact, case-sensitive `longitude` immediately after `latitude`. |
| `extract_keywords` | `*source_cols`, `remove_extracted=False`, `use_regex=False`, `**target_cols_and_keywords` | Search combined source text and write matched keywords/regex results to named target columns. Literal matches are case-insensitive and title-cased; keyword lists are passed as keyword arguments. Each unique text is matched once with a single combined pattern (Aho-Corasick for ASCII text when `pyahocorasick` is installed). |
| `split_list_objects` | `source_col`, `remove_source_col=False`, `explode_rows=False`, `cartesian_explode=False`, `**target_cols_and_indices` | Extract list elements by integer index, join selected values, or explode extracted lists in parallel/cartesian mode. Exploding rows changes the row count and resets the index. |
| `impute_by_language` | `reference_col`, `*target_cols`, `target_lang="en"` | Choose existing values by exact normalized match or language detection, then fall back to title-cased reference values. Uses deterministic `langdetect` seeding. |
| `translate_conditional_column` | `partition_col`, `detect_col`, `target_col`, `target_lang="en"`, `sample_size=10`, `number_of_samples=3`, `threshold=0.5`, `save_every=1000` | Sample each partition for language detection and translate target values for partitions below the threshold. Partitions are processed with one `groupby`; sampling is reproducible for a given `random_state` (pass `None` for unseeded sampling). Uses the translation provider and cached results. |
| `translate_categorical_column` | `target_col`, `target_lang="en"`, `save_every=1000`, `provider=None` | Translate each unique non-null category once, cache results, and map them back. Uses the translation provider. |
| `truncate_after_substring` | `regex=True`, `**target_cols_and_substrings` | Split named columns at one or more substring/regex delimiters and keep the text before the first delimiter. |
| `fill_missing_coordinates` | `*fallback_cols`, keyword-only `lat_col`, `long_col`, `save_every=1000`, `user_agent="data_cleaning_core_geocoder"`, `retry_not_found=False`, `provider=None` | Build location queries from fallback columns and geocode missing coordinates with `geopy`/Nominatim. A failed query is retried with its leftmost part dropped (`"street, city, country"` → `"city, country"` → `"country"`). Results and not-found queries are cached; pass `retry_not_found=True` to re-query misses. Set `user_agent` to identify your application, as Nominatim's usage policy requires. |
| `convert_to_usd` | `*target_cols`, keyword-only `country_col`, `year_col`, `new_cols=True`, `round_decimals=None`, `provider=None` | Infer country currencies with `pycountry`, fetch yearly mean rates through the rate provider (Yahoo Finance by default) with one download per currency (looking back to earlier years when a year has no data), cache lookups, and divide target values by the rate. Unknown countries and past years without data are cached as misses; network errors are not cached. |
| `handle_outliers` | `*target_cols`, keyword-only `hierarchy_cols`, `min_samples=5`, `get_hierarchical_stats=False` | Replace hierarchical-IQR outliers with `NaN`, progressively relaxing the grouping hierarchy. When enabled, return `(result, stats)` with the original-data hierarchical median statistics. |
| `drop_outliers` | `*target_cols`, keyword-only `hierarchy_cols`, `min_samples=5`, `get_hierarchical_stats=False` | Drop rows containing hierarchical-IQR outliers. Rows are dropped by position, so duplicated index labels are safe. When enabled, return `(result, stats)` with the original-data hierarchical median statistics. |
| `fill_missing` | `*target_cols`, `strategy="median"`, `hierarchy_cols=None`, `get_hierarchical_stats=False` | Fill missing values progressively through a grouping hierarchy using `median`, `mean`, `mode`, or the accepted alias `mod`. When enabled, return `(result, stats)` with statistics for the requested strategy computed before imputation. |
| `get_hierarchical_stats` | `*target_cols`, `strategy="median"`, `hierarchy_cols=None`, `output_type=None` | Return one statistic row per group, hierarchy level, target, and strategy. See the example above for the output schema and `output_type` values. |
| `categorize_by_reference` | `*source_cols`, keyword-only `target_col`, `reference`, `default_category="Other"` | Search combined text with an Aho-Corasick automaton and assign the longest matching reference keyword's category. |
| `extract_entities_nlp` | `*source_cols`, keyword-only `lexicon_source`, `spacy_model="en_core_web_sm"`, `exclude_pos=None`, `disable=("parser", "ner")`, `save_every=1000`, `noisy_terms=None`, `**target_cols_and_json_keys` | Load lexicon data from JSON or a dictionary, process text with spaCy, cache entity lists per target/model/pipeline configuration, and write them to target columns. `disable` accepts None, one component name, or an iterable; required lemmatization components are rejected and unknown names warn. |
| `save_to_json` | `file_path`, `orient="records"`, `append=False`, `force_ascii=False`, `indent=4` | Write JSON only when changed, or append/deduplicate existing JSON rows. Rows with list or dictionary values are deduplicated by their JSON content. Datetime columns are formatted as `%Y-%m-%d %H:%M:%S`. |
| `save_to_sql` | `table_name`, `database_type`, `database_user`, `database_password`, `database_host`, `database_port`, `database_name`, `database_driver=None`, `columns_types=None`, `chunk_size=None`, `fast_executemany=False`, `if_exists="append"`, `index=False` | Build a SQLAlchemy connection URL and write the frame with `DataFrame.to_sql`. SQLite uses `database_name` as a path; SQL Server requires `database_driver`. |

Most methods skip missing source/target columns with a logged warning and return an unchanged copy. Always use the method's return value. Invalid arguments that would otherwise produce silently wrong results raise instead: an unknown `strategy`, an empty `file_path`, invalid `save_to_sql` connection settings, or an unusable `extract_entities_nlp` configuration.

For `handle_outliers`, `drop_outliers`, and `fill_missing`, passing `get_hierarchical_stats=True` reuses `get_hierarchical_stats()` and computes the statistics from the original data before the cleaning operation changes values or rows. The return value is `(result, stats)`; with the deprecated `inplace=True`, the operation mutates the original object and returns `stats` directly. The outlier methods use the default hierarchical `median` statistic; `fill_missing` uses its requested `strategy`.

## Limitations and operational notes

- Optional integrations are lazy-loaded, so basic `bynat.cleaning` imports and core dataframe methods work with only `numpy` and `pandas`. Methods that need an absent integration raise a clear `ImportError` when called.
- Translation uses `deep-translator` by default and may make external requests. Geocoding uses Nominatim through `geopy` with a one-second rate limit. Currency conversion uses Yahoo Finance history through `yfinance`. These services can fail, rate-limit, or change results; cached values persist locally in SQLite.
- NLP extraction requires a compatible spaCy model installed separately. `extract_entities_nlp(disable=...)` accepts None, a component name, or an iterable, defaults to disabling `parser` and `ner`, rejects components required for lemmatization, and warns before continuing when a requested component is not in the loaded pipeline. A missing or unloadable model raises spaCy's `OSError`.
- `save_to_sql` lets database exceptions propagate to the caller and always disposes its engine. It receives database credentials as ordinary arguments; do not hard-code secrets in application code.
- Cache entries live in a single SQLite database rather than one JSON file per function. Saves run inside SQLite transactions and WAL checkpoints, so an interrupted or failed save is logged and rolled back without raising; rows from earlier successful saves stay intact and the pending in-memory changes are retried on the next save. Concurrent managers sharing one database merge disjoint key updates, and for a key written by more than one manager the last committed write wins. Values must be JSON serializable.
- Messages go through the standard `logging` module under the `bynat` logger. Skipped columns and failed lookups are `WARNING`s and appear on stderr by default; progress and checkpoints are `INFO`. To see progress, or to silence warnings:

  ```python
  import logging

  logging.basicConfig(level=logging.INFO)              # show progress
  logging.getLogger("bynat").setLevel(logging.ERROR)   # or hide warnings
  ```
- The top-level `bynat` namespace exposes only `read_json`, `read_csv`, and `BynatAccessor`; `from bynat import BaseData` is not supported.

## Development and verification

The repository test suite uses `pytest` and avoids network access, secrets, user files, and external services. Network-backed integrations (translation, geocoding, exchange rates, spaCy) are exercised with fake modules:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m compileall -q bynat
ruff check bynat tests benchmarks
python -m mypy
```

Install `".[categorization,sql]"` to also run the tests that use the real `pyahocorasick` and SQLAlchemy/SQLite; they are skipped otherwise. CI runs the suite on Ubuntu and Windows with Python 3.11 and 3.13, plus Ruff, mypy, and an extras job.

Type checking is gradual. `python -m mypy` reads its scope from `[tool.mypy]` in `pyproject.toml`: the cache manager and the cleaning helper modules (`_common`, `accessor`, `columns`, `text`, `stats`, `io`, `enrich`), checked against `pandas-stubs`. The deprecated `BaseData` wrappers in `bynat/cleaning/base.py` are excluded. To bring a module into scope, add it to `files`, fix what mypy reports with precise annotations rather than `Any` or `# type: ignore`, and keep the runtime behavior unchanged.

`benchmarks/` holds timing scripts that compare the current implementations with the `0.3.0` algorithms; see `benchmarks/README.md`.

Building a wheel or source archive requires the configured setuptools build backend and wheel tooling.

## License

MIT. See [LICENSE](LICENSE).
