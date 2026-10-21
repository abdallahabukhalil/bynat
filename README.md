# bynat

`bynat` is a Python utility package for tabular-data cleaning and small JSON-backed caches. Its cleaning API is exposed through subclasses of `pandas.DataFrame`; the caching API stores per-function dictionaries as JSON files.

## Status

Version `0.2.0` is the project version declared in `pyproject.toml`. See [CHANGELOG.md](CHANGELOG.md) for behavior changes since `0.1.1` and [ROADMAP.md](./docs/ROADMAP.md) for planned work.

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

The top-level `bynat/__init__.py` is empty. Import public objects from their subpackages:

```python
from bynat.caching import CacheManager
from bynat.cleaning import BaseData, CsvFile, JsonFile
```

The subpackage `__init__.py` files re-export the names listed by each implementation module's `__all__`:

- `bynat.caching`: `CacheManager`
- `bynat.cleaning`: `BaseData`, `JsonFile`, `CsvFile`

## Caching

### `CacheManager`

`CacheManager` manages JSON files named `<func_name>_cache.json` under `cache_dir`. When `cache_dir` is omitted (here and in `BaseData`, `JsonFile`, and `CsvFile`), the `BYNAT_CACHE_DIR` environment variable is used if set, otherwise `Settings/Cache Data/Automated`. Relative cache paths are resolved from the current working directory; `~` and environment variables are expanded. Cache directories are created when a cache is saved, not when the manager is constructed.

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

Cache keys must be non-empty strings and cannot contain path separators. A cache file that is not a valid JSON object is renamed to `<name>.corrupt.<timestamp>` (so its data can be recovered by hand), logged as a warning, and treated as an empty dictionary. Saves are atomic: the JSON is written to a temporary file that replaces the old one. JSON serialization errors and file-write errors are logged rather than raised by `save_function_cache`, and the previous file is kept.

### CacheManager methods and parameters

| Method | Parameters | Behavior |
| --- | --- | --- |
| `load_function_cache(func_name)` | `func_name: str` | Load `<func_name>_cache.json`, or return/create an in-memory empty dictionary when it does not exist. |
| `get_column_cache(func_name, col_name)` | `func_name: str`, `col_name: str` | Return/create a nested dictionary under `col_name`. |
| `save_function_cache(func_name)` | `func_name: str` | Atomically write the in-memory function cache as indented UTF-8 JSON. |
| `delete_column_cache(func_name, col_name)` | `func_name: str`, `col_name: str` | Delete a nested bucket and save the function cache; log an `INFO` message when the bucket is absent or deleted. |

`_validate_cache_key` and `_get_file_path` are implementation helpers and are not part of the public API.

## Cleaning

`BaseData` subclasses `pandas.DataFrame`. Most cleaning methods return a new `BaseData`-derived object by default; pass `inplace=True` to mutate the current object and receive `None`. Methods that perform translation, geocoding, NLP extraction, or exchange-rate lookup use the instance's `CacheManager`.

### Practical examples

Basic column cleaning and reshaping:

```python
from bynat.cleaning import BaseData

records = BaseData({
    "user.display_name": ["Ada", "Grace"],
    "user_id": [1, 2],
    "description": ["Blue shoes", "Red shirt"],
})

cleaned = (
    records
    .rename_all_DOTS()
    .drop_by_substring("user")
)

labeled = records.extract_keywords(
    "description",
    colors=["blue", "red"],
)
```

Load files with the convenience subclasses:

```python
from bynat.cleaning import CsvFile, JsonFile

from_csv = CsvFile(csv_file_path="input.csv", read_kwargs={"sep": ";"})
from_json = JsonFile(json_file_path="input.json", key="items")

from_csv.save_to_json("output.json", orient="records")
```

Hierarchical statistics compute one statistic per group at every level of a hierarchy, from the full hierarchy down to its first column. Pass one strategy or a list of `median`, `mean`, and `mode` (`mod` is accepted as an alias):

```python
import pandas as pd
from bynat.cleaning import BaseData

salaries = BaseData({
    "country": ["EG", "EG", "EG", "US", "US"],
    "title": ["Analyst", "Analyst", "Engineer", "Analyst", "Analyst"],
    "salary": [10.0, 14.0, 30.0, 70.0, 90.0],
})

stats = salaries.get_hierarchical_stats(
    "salary",
    strategy=["median", "mode"],
    hierarchy_cols=["country", "title"],
)
```

`stats` is returned as the caller's class (here `BaseData`) with the columns `hierarchy_level`, `hierarchy_columns`, `group_key`, `target_column`, `strategy`, and `statistic`. The median rows are:

| hierarchy_level | group_key | strategy | statistic |
| --- | --- | --- | --- |
| 2 | `("EG", "Analyst")` | median | 12.0 |
| 2 | `("EG", "Engineer")` | median | 30.0 |
| 2 | `("US", "Analyst")` | median | 80.0 |
| 1 | `("EG",)` | median | 14.0 |
| 1 | `("US",)` | median | 80.0 |

`group_key` is always a tuple with one value per hierarchy column, including single-column levels such as `("EG",)`. Null group keys are kept. `mean` and `median` need numeric targets and raise pandas' `TypeError` otherwise; `mode` works for any hashable values and breaks ties by first occurrence. Choose another container with `output_type`:

```python
as_frame = salaries.get_hierarchical_stats("salary", hierarchy_cols="country", output_type=pd.DataFrame)
as_dict = salaries.get_hierarchical_stats("salary", hierarchy_cols="country", output_type=dict)
# as_dict["group_key"] == [("EG",), ("US",)]; as_dict["statistic"] == [14.0, 80.0]
```

`output_type` accepts `None` (default, the caller's class), `pandas.DataFrame`, `dict` (column name to list of values), or a `BaseData` subclass. Target columns cannot also be hierarchy columns.

Network-backed enrichment is explicit in the method behavior and should be used with an appropriate cache directory and service credentials/configuration where applicable:

```python
translated = records.translate_categorical_column(
    "category",
    target_lang="en",
    save_every=100,
)
```

### Constructors

| Class | Constructor parameters | Behavior |
| --- | --- | --- |
| `BaseData` | `data=None`, `cache_dir=None`, `*args`, `**kwargs` | Build a `pandas.DataFrame` subclass and attach a `CacheManager`. Extra arguments are forwarded to `pandas.DataFrame`. |
| `JsonFile` | `data=None`, `json_file_path=None`, `key=None`, `cache_dir=None`, `*args`, `**kwargs` | Read a JSON file, optionally select a dictionary key, and normalize the payload with `pandas.json_normalize`. A path passed through `data` is also recognized. |
| `CsvFile` | `data=None`, `csv_file_path=None`, `cache_dir=None`, `read_kwargs=None`, `*args`, `**kwargs` | Read a CSV file with `pandas.read_csv`; `read_kwargs` is forwarded to that function. A path passed through `data` is also recognized. |

### BaseData methods and parameters

| Method | Parameters | Purpose and important behavior |
| --- | --- | --- |
| `clear_cache_for_column` | `func_name`, `target_col` | Delete one cache bucket through the attached `CacheManager`. |
| `view` | none | Return a plain `pandas.DataFrame` view/copy of the object. |
| `drop_by_substring` | `substring`, `inplace=False` | Drop every column whose name contains `substring`. |
| `drop_CLASS_columns` | `inplace=False` | Drop columns containing the literal substring `__CLASS__`. |
| `rename_by_substring` | `old_substring`, `new_substring`, `inplace=False` | Replace occurrences of one substring in column names. |
| `rename_all_DOTS` | `unified_separator="_"`, `inplace=False` | Replace dots in all matching column names. |
| `reorder_columns` | `*first_cols`, `inplace=False` | Move the named columns to the front while preserving the order of other columns. Pass column names as positional arguments. |
| `melt_transform_pivot` | `id_vars`, `value_vars=None`, `transform_func=None`, `aggfunc="first"`, `var_name="variable_name"`, `value_name="variable_value"`, `pivot_index=None`, `pivot_columns=None`, `inplace=False`, `**kwargs` | Melt selected columns, optionally transform the melted value series, and pivot back. `transform_func` must return the same number of values. |
| `move_column` | `col_to_move`, `col_target`, `position="after"`, `inplace=False` | Move one column before or after another. `position` must be `"before"` or `"after"`. |
| `align_lat_lng` | `inplace=False` | Move exact, case-sensitive `longitude` immediately after `latitude`. |
| `extract_keywords` | `*source_cols`, `inplace=False`, `remove_extracted=False`, `use_regex=False`, `**target_cols_and_keywords` | Search combined source text and write matched keywords/regex results to named target columns. Literal matches are case-insensitive and title-cased; keyword lists are passed as keyword arguments. |
| `split_list_objects` | `source_col`, `remove_source_col=False`, `explode_rows=False`, `cartesian_explode=False`, `inplace=False`, `**target_cols_and_indices` | Extract list elements by integer index, join selected values, or explode extracted lists in parallel/cartesian mode. Do not combine `inplace=True` with row-expanding use cases. |
| `impute_by_language` | `reference_col`, `*target_cols`, `target_lang="en"`, `inplace=False` | Choose existing values by exact normalized match or language detection, then fall back to title-cased reference values. Uses deterministic `langdetect` seeding. |
| `translate_conditional_column` | `partition_col`, `detect_col`, `target_col`, `target_lang="en"`, `sample_size=10`, `number_of_samples=3`, `threshold=0.5`, `save_every=1000`, `inplace=False` | Sample each partition for language detection and translate target values for partitions below the threshold. Uses `deep-translator` and cached results. |
| `translate_categorical_column` | `target_col`, `target_lang="en"`, `save_every=1000`, `inplace=False` | Translate each unique non-null category once, cache results, and map them back. Uses `deep-translator`. |
| `truncate_after_substring` | `inplace=False`, `regex=True`, `**target_cols_and_substrings` | Split named columns at one or more substring/regex delimiters and keep the text before the first delimiter. |
| `fill_missing_coordinates` | `*fallback_cols`, keyword-only `lat_col`, `long_col`, `save_every=1000`, `user_agent="data_cleaning_core_geocoder"`, `retry_not_found=False`, `inplace=False` | Build location queries from fallback columns and geocode missing coordinates with `geopy`/Nominatim. A failed query is retried with its leftmost part dropped (`"street, city, country"` → `"city, country"` → `"country"`). Results and not-found queries are cached; pass `retry_not_found=True` to re-query misses. Set `user_agent` to identify your application, as Nominatim's usage policy requires. |
| `convert_to_usd` | `*target_cols`, keyword-only `country_col`, `year_col`, `new_cols=True`, `round_decimals=None`, `inplace=False` | Infer country currencies with `pycountry`, fetch the yearly mean Yahoo Finance rate with `yfinance` (looking back to earlier years when a year has no data), cache lookups, and divide target values by the rate. Unknown countries and past years without data are cached as misses; network errors are not cached. |
| `handle_outliers` | `*target_cols`, keyword-only `hierarchy_cols`, `min_samples=5`, `inplace=False`, `get_hierarchical_stats=False` | Replace hierarchical-IQR outliers with `NaN`, progressively relaxing the grouping hierarchy. When enabled, return the original-data hierarchical median statistics alongside the result, or directly for `inplace=True`. |
| `drop_outliers` | `*target_cols`, keyword-only `hierarchy_cols`, `min_samples=5`, `inplace=False`, `get_hierarchical_stats=False` | Drop rows containing hierarchical-IQR outliers. Rows are dropped by position, so duplicated index labels are safe. When enabled, return the original-data hierarchical median statistics alongside the result, or directly for `inplace=True`. |
| `fill_missing` | `*target_cols`, `strategy="median"`, `hierarchy_cols=None`, `inplace=False`, `get_hierarchical_stats=False` | Fill missing values progressively through a grouping hierarchy using `median`, `mean`, `mode`, or the accepted alias `mod`. When enabled, return statistics for the requested strategy computed before imputation, alongside the result, or directly for `inplace=True`. |
| `get_hierarchical_stats` | `*target_cols`, `strategy="median"`, `hierarchy_cols=None`, `output_type=None` | Return one statistic row per group, hierarchy level, target, and strategy. See the example above for the output schema and `output_type` values. |
| `categorize_by_reference` | `*source_cols`, keyword-only `target_col`, `reference`, `default_category="Other"`, `inplace=False` | Search combined text with an Aho-Corasick automaton and assign the longest matching reference keyword's category. |
| `extract_entities_nlp` | `*source_cols`, keyword-only `lexicon_source`, `spacy_model="en_core_web_sm"`, `exclude_pos=None`, `save_every=1000`, `inplace=False`, `noisy_terms=None`, `noisey_terms=None`, `**target_cols_and_json_keys` | Load lexicon data from JSON or a dictionary, process text with spaCy, cache entity lists, and write them to target columns. `noisey_terms` is the backward-compatible misspelled alias. |
| `save_to_json` | `file_path`, `orient="records"`, `append=False`, `force_ascii=False`, `indent=4` | Write JSON only when changed, or append/deduplicate existing JSON rows. Rows with list or dictionary values are deduplicated by their JSON content. Datetime columns are formatted as `%Y-%m-%d %H:%M:%S`. |
| `save_to_sql` | `table_name`, `database_type`, `database_user`, `database_password`, `database_host`, `database_port`, `database_name`, `database_driver=None`, `columns_types=None`, `chunk_size=None`, `fast_executemany=False`, `if_exists="append"`, `index=False` | Build a SQLAlchemy connection URL and write the frame with `DataFrame.to_sql`. SQLite uses `database_name` as a path; SQL Server requires `database_driver`. |

Most methods skip missing source/target columns with a logged warning and return the original or copied frame, depending on `inplace`. Check the method's return value when chaining operations. Invalid arguments that would otherwise produce silently wrong results raise instead: an unknown `strategy`, an empty `file_path`, invalid `save_to_sql` connection settings, or an unusable `extract_entities_nlp` configuration.

For `handle_outliers`, `drop_outliers`, and `fill_missing`, passing `get_hierarchical_stats=True` reuses `get_hierarchical_stats()` and computes the statistics from the original data before the cleaning operation changes values or rows. With `inplace=False`, the return value is `(result, stats)`; with `inplace=True`, the operation still mutates the original object and returns `stats` directly. The outlier methods use the default hierarchical `median` statistic; `fill_missing` uses its requested `strategy`.

## Limitations and operational notes

- Optional integrations are lazy-loaded, so basic `bynat.cleaning` imports and core dataframe methods work with only `numpy` and `pandas`. Methods that need an absent integration raise a clear `ImportError` when called.
- Translation uses `deep-translator` and may make external requests. Geocoding uses Nominatim through `geopy` with a one-second rate limit. Currency conversion uses Yahoo Finance history through `yfinance`. These services can fail, rate-limit, or change results; cached values persist locally as JSON.
- NLP extraction requires a compatible spaCy model installed separately. A missing or unloadable model raises spaCy's `OSError`.
- `save_to_sql` lets database exceptions propagate to the caller and always disposes its engine. It receives database credentials as ordinary arguments; do not hard-code secrets in application code.
- Cache files are ordinary JSON. Each save writes a temporary file and atomically replaces the old one, so an interrupted save never leaves a truncated cache. Files are not locked: concurrent writers to the same cache can still overwrite each other's updates. Values must be JSON serializable.
- Messages go through the standard `logging` module under the `bynat` logger. Skipped columns and failed lookups are `WARNING`s and appear on stderr by default; progress and checkpoints are `INFO`. To see progress, or to silence warnings:

  ```python
  import logging

  logging.basicConfig(level=logging.INFO)              # show progress
  logging.getLogger("bynat").setLevel(logging.ERROR)   # or hide warnings
  ```
- The package's top-level namespace is intentionally empty; `from bynat import BaseData` is not supported by the current `__init__.py`.

## Development and verification

The repository test suite uses `pytest` and avoids network access, secrets, user files, and external services. Network-backed integrations (translation, geocoding, exchange rates, spaCy) are exercised with fake modules:

```bash
python -m pytest -q
python -m compileall -q bynat
ruff check bynat tests
```

Install `".[categorization,sql]"` to also run the tests that use the real `pyahocorasick` and SQLAlchemy/SQLite; they are skipped otherwise. CI runs the suite on Ubuntu and Windows with Python 3.11 and 3.13, plus Ruff and an extras job.

Building a wheel or source archive requires the configured setuptools build backend and wheel tooling.

## License

MIT. See [LICENSE](LICENSE).
