# bynat

`bynat` is a Python utility package for tabular-data cleaning and small JSON-backed caches. Its cleaning API is exposed through subclasses of `pandas.DataFrame`; the caching API stores per-function dictionaries as JSON files.

## Status

Version `0.1.0` is the package metadata version. This README describes the implementation currently in this repository. It is not a claim that every optional external service, database driver, or spaCy model is available in a fresh environment.

## Installation

The package metadata declares the runtime libraries imported by the cleaning module:

```bash
python -m pip install bynat
```

For a checkout, install it in editable mode:

```bash
python -m pip install -e .
```

### Requirements

- Python `>=3.11`. The source imports `typing.Self`, which is not available from `typing` on Python 3.10.
- Runtime distributions declared by `pyproject.toml`: `Caching` (the local distribution providing `from Caching import CacheManager`), the conventional distribution mapping `pyahocorasick` for the `ahocorasick` import, plus `Babel`, `deep-translator`, `geopy`, `langdetect`, `numpy`, `pandas`, `pycountry`, `spacy`, `SQLAlchemy`, and `yfinance`.
- Distribution-name and version uncertainty remains for dependencies absent from the offline environment. In particular, the `ahocorasick` → `pyahocorasick` mapping could not be verified from local installed metadata; confirm it before publishing. Versions are intentionally unpinned because the repository contains no tested version constraints. `numpy` and `pandas` were present locally (`2.5.3` and `3.0.6`), but that is not a compatibility guarantee.
- `extract_entities_nlp` also needs a separately installed spaCy language model, defaulting to `en_core_web_sm`; the model is not a Python dependency declared by this project.
- `save_to_sql` additionally needs the DBAPI driver required by the SQLAlchemy URL you use, such as a PostgreSQL, MySQL, or SQL Server driver. No database driver is bundled.

`Cleaning.py` imports `CacheManager` with the absolute top-level statement `from Caching import CacheManager`. Therefore `Caching` is a mandatory runtime dependency, not an optional cache extra. The adjacent local cache package declares distribution name `Caching` (version `0.1.0`); `bynat` does not bundle that top-level module, so a compatible `Caching` distribution must be installed separately. The import style was not changed because this task is limited to package metadata and documentation.

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

`CacheManager` manages JSON files named `<func_name>_cache.json` under `cache_dir`. Relative cache paths are resolved from the current working directory; `~` and environment variables are expanded. Cache directories are created when a cache is saved, not when the manager is constructed.

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

Cache keys must be non-empty strings and cannot contain path separators. A corrupted or unreadable JSON cache is reported, then treated as an empty dictionary. JSON serialization errors and file-write errors are printed rather than raised by `save_function_cache`.

### CacheManager methods and parameters

| Method | Parameters | Behavior |
| --- | --- | --- |
| `load_function_cache(func_name)` | `func_name: str` | Load `<func_name>_cache.json`, or return/create an in-memory empty dictionary when it does not exist. |
| `get_column_cache(func_name, col_name)` | `func_name: str`, `col_name: str` | Return/create a nested dictionary under `col_name`. |
| `save_function_cache(func_name)` | `func_name: str` | Write the in-memory function cache as indented UTF-8 JSON. |
| `delete_column_cache(func_name, col_name)` | `func_name: str`, `col_name: str` | Delete a nested bucket and save the function cache; print a message when the bucket is absent or deleted. |

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
| `BaseData` | `data=None`, `cache_dir="Settings/Cache Data/Automated"`, `*args`, `**kwargs` | Build a `pandas.DataFrame` subclass and attach a `CacheManager`. Extra arguments are forwarded to `pandas.DataFrame`. |
| `JsonFile` | `data=None`, `json_file_path=None`, `key=None`, `cache_dir="Settings/Cache Data/Automated"`, `*args`, `**kwargs` | Read a JSON file, optionally select a dictionary key, and normalize the payload with `pandas.json_normalize`. A path passed through `data` is also recognized. |
| `CsvFile` | `data=None`, `csv_file_path=None`, `cache_dir="Settings/Cache Data/Automated"`, `read_kwargs=None`, `*args`, `**kwargs` | Read a CSV file with `pandas.read_csv`; `read_kwargs` is forwarded to that function. A path passed through `data` is also recognized. |

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
| `fill_missing_coordinates` | `*fallback_cols`, keyword-only `lat_col`, `long_col`, `save_every=1000`, `inplace=False` | Build location queries from fallback columns and geocode missing coordinates with `geopy`/Nominatim, caching successful results. |
| `convert_to_usd` | `*target_cols`, keyword-only `country_col`, `year_col`, `new_cols=True`, `round_decimals=None`, `inplace=False` | Infer country currencies with `pycountry`, fetch historical Yahoo Finance rates with `yfinance`, cache lookups, and divide target values by the rate. |
| `handle_outliers` | `*target_cols`, keyword-only `hierarchy_cols`, `min_samples=5`, `inplace=False` | Replace hierarchical-IQR outliers with `NaN`, progressively relaxing the grouping hierarchy. |
| `drop_outliers` | `*target_cols`, keyword-only `hierarchy_cols`, `min_samples=5`, `inplace=False` | Drop rows containing hierarchical-IQR outliers. |
| `fill_missing` | `*target_cols`, `strategy="median"`, `hierarchy_cols=None`, `inplace=False` | Fill missing values progressively through a grouping hierarchy using `median`, `mean`, `mode`, or the accepted alias `mod`. |
| `categorize_by_reference` | `*source_cols`, keyword-only `target_col`, `reference`, `default_category="Other"`, `inplace=False` | Search combined text with an Aho-Corasick automaton and assign the longest matching reference keyword's category. |
| `extract_entities_nlp` | `*source_cols`, keyword-only `lexicon_source`, `spacy_model="en_core_web_sm"`, `exclude_pos=None`, `save_every=1000`, `inplace=False`, `noisy_terms=None`, `noisey_terms=None`, `**target_cols_and_json_keys` | Load lexicon data from JSON or a dictionary, process text with spaCy, cache entity lists, and write them to target columns. `noisey_terms` is the backward-compatible misspelled alias. |
| `save_to_json` | `file_path`, `orient="records"`, `append=False`, `force_ascii=False`, `indent=4` | Write JSON only when changed, or append/deduplicate existing JSON rows. Datetime columns are formatted as `%Y-%m-%d %H:%M:%S`. |
| `save_to_sql` | `table_name`, `database_type`, `database_user`, `database_password`, `database_host`, `database_port`, `database_name`, `database_driver=None`, `columns_types=None`, `chunk_size=None`, `fast_executemany=False`, `if_exists="append"`, `index=False` | Build a SQLAlchemy connection URL and write the frame with `DataFrame.to_sql`. SQLite uses `database_name` as a path; SQL Server requires `database_driver`. |

Most methods silently skip missing source/target columns and return the original or copied frame, depending on `inplace`. Check the method's return value when chaining operations.

## Limitations and operational notes

- Cleaning imports all third-party libraries at module import time, so even basic `BaseData` operations require the full declared dependency set to be importable.
- The repository has no automated test suite. The implementation has been validated here only with static parsing, cache smoke checks, and available local tooling; network-backed methods were not exercised offline.
- Translation uses `deep-translator` and may make external requests. Geocoding uses the public Nominatim service through `geopy` with a one-second rate limit. Currency conversion uses Yahoo Finance history through `yfinance`. These services can fail, rate-limit, or change results; cached values persist locally as JSON.
- NLP extraction requires a compatible spaCy model installed separately. A missing or unloadable model causes the method to print an error and fill requested output columns with empty lists.
- `save_to_sql` catches database exceptions and prints an error instead of re-raising them. It also receives database credentials as ordinary arguments; do not hard-code secrets in application code.
- Cache files are ordinary JSON and are not locked or transactionally written. Concurrent writers can overwrite each other, and values must be JSON serializable.
- The implementation uses `print` for warnings/progress/errors rather than a logging interface.
- The package's top-level namespace is intentionally empty; `from bynat import BaseData` is not supported by the current `__init__.py`.

## License

MIT. See [LICENSE](LICENSE).
