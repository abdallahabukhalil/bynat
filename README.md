<h1 align="center">
  <picture>
    <source
      media="(prefers-color-scheme: dark)"
      srcset="./assets/Full%20Logo/logo-dark.svg">
    <img
      alt="bynat"
      src="./assets/Full%20Logo/logo.svg"
      width="320">
  </picture>
</h1>

<p align="center">
  <strong>A Python toolkit for simplifying data workflows.</strong><br><br>
</p>

<div align="center">
  Python <code>&gt;=3.11</code> &nbsp;·&nbsp; MIT license &nbsp;·&nbsp; version <code>0.6.0</code>

  [Overview](#what-is-bynat) ·
  [Installation](#installation) ·
  [Quick start](#quick-start) ·
  [Usage](#using-bynat) ·
  [Advanced](#advanced-features) ·
  [Reference](#cleaning-method-reference) ·
  [Notes](#limitations-and-operational-notes) ·
  [Development](#development-and-verification)

</div>

---

## What is bynat?

`bynat` is a Python toolkit for the small, fiddly jobs around tabular data. It works on pandas `DataFrame` objects (a `DataFrame` is pandas' name for a table of rows and columns) and includes SQLite-backed caching, so slow lookups are not repeated.

### What you can do with it

- **Tidy columns:** rename, drop, reorder, move, and reshape columns, and split list values into separate columns.
- **Work with text:** extract keywords, truncate text at a delimiter, and categorize rows by reference keywords.
- **Profile data quality:** assess completeness, duplicate rows, column data types, and type-specific statistics with non-mutating structured reports.
- **Handle outliers and gaps:** find outliers and fill missing values by comparing each row with similar rows (grouped by a hierarchy of columns).
- **Enrich data:** translate values, geocode missing coordinates, convert amounts to USD, and extract entities with spaCy.
- **Read and write files:** load CSV and JSON files, and save data to JSON or a SQL database.
- **Cache results:** store lookup results in a SQLite database and reuse them between runs.

### Ways to use it

You can mix these styles in the same project:

- **Class-based API:** `BaseData`, `JsonFile`, and `CsvFile`. This is the primary API.
- **pandas accessor:** the optional `df.bynat` accessor adds the same cleaning operations to ordinary `pandas.DataFrame` objects.
- **Caching API:** `CacheManager` stores per-function dictionaries in a SQLite database.

New here? [Install bynat](#installation), then go to the [Quick start](#quick-start).

### Project status

`0.6.0` is the version declared in `pyproject.toml`. See [CHANGELOG.md](CHANGELOG.md) for behavior changes since `0.1.1`, and [ROADMAP.md](./docs/ROADMAP.md) for planned work.

---

## Installation

Install from PyPI:

```bash
python -m pip install bynat
```

To work from a repository checkout, install in editable mode:

```bash
python -m pip install -e .
```

### Requirements

- Python `>=3.11`
- Required packages: `numpy` and `pandas`

### Optional integrations

Features that call outside libraries are grouped into optional extras in `pyproject.toml`:

| Extra | Installs | Used for |
| --- | --- | --- |
| `translation` | `deep-translator`, `langdetect` | Translation and language detection |
| `geocoding` | `geopy` | Geocoding |
| `currency` | `Babel`, `pycountry`, `yfinance` | Currency conversion |
| `categorization` | `pyahocorasick` (imported as `ahocorasick`) | Keyword matching |
| `nlp` | `spacy` | Entity extraction |
| `sql` | `SQLAlchemy` | Saving to SQL |

Install an extra when you need it:

```bash
python -m pip install "bynat[translation]"
```

Optional integrations are imported only when a method uses them. Basic imports and core DataFrame operations work without them. If a method needs a missing integration, it raises an `ImportError` that names the extra to install.

> **Not bundled:** `extract_entities_nlp` also needs a compatible spaCy model (default: `en_core_web_sm`), and `save_to_sql` needs the database driver required by your SQLAlchemy URL. Neither model files nor database drivers are included.

---

## Quick start

`import bynat` registers the `df.bynat` accessor on every `pandas.DataFrame`.

```python
import bynat
import pandas as pd

records = pd.DataFrame({
    "user.display_name": ["Ada", "Grace"],
    "user_id": [1, 2],
    "description": ["Blue shoes", "Red shirt"],
})

cleaned = records.bynat.rename_all_DOTS().bynat.drop_by_substring("user")
```

This takes two steps:

1. `rename_all_DOTS` replaces dots in column names (with `_` by default).
2. `drop_by_substring("user")` drops every column whose name contains `user`.

Only the `description` column is left.

Prefer a class over the accessor? `BaseData` is a `pandas.DataFrame` subclass that does the same cleanup:

```python
from bynat.cleaning import BaseData

cleaned = BaseData(records).rename_all_DOTS().drop_by_substring("user")
```

Both versions return a new object and leave `records` unchanged. Accessor methods always return new DataFrames. Class methods also return a new object by default.

---

## Using bynat

bynat has two parts that work together:

- **Cleaning** (`bynat.cleaning`): column, text, statistics, enrichment, and file methods.
- **Caching** (`bynat.caching`): SQLite-backed storage for lookup results.

Cleaning methods that call outside services (translation, geocoding, NLP extraction, and exchange-rate lookup) use a shared `CacheManager`. See [Caching](#caching).

### Import styles

There are three supported ways to import bynat. None is required; use whichever reads best in your code.

| Style | Example | Good when |
| --- | --- | --- |
| Module imports | `import bynat.cleaning as cl`<br>`import bynat.caching as ch` | You prefer a module namespace, such as `cl.BaseData` or `ch.CacheManager`. |
| Direct imports | `from bynat.cleaning import BaseData` | You want specific objects under their own names. |
| pandas accessor | `import pandas as pd`<br>`import bynat as by` | You already have a plain `DataFrame` and want to call `df.bynat.<method>(...)`. |

The accessor is always called `bynat` (`df.bynat...`), whatever alias you give the module. Importing `bynat` or any of its submodules registers it.

#### Supported imports

```python
import bynat                          # registers the df.bynat accessor
from bynat import ProfileReport, profile, read_csv, read_json
from bynat.caching import CacheManager, shared_cache_manager
from bynat.cleaning import (
    BaseData,
    ColumnProfile,
    CsvFile,
    DatasetProfile,
    JsonFile,
    ProfileReport,
    profile,
)
from bynat.cleaning.enrich import Geocoder, RateSource, Translator
```

What each module provides:

| Module | Public objects |
| --- | --- |
| `bynat` | `read_json`, `read_csv`, `BynatAccessor`, `profile`, `ProfileReport` |
| `bynat.caching` | `CacheManager`, `shared_cache_manager`, `CACHE_DIR_ENV_VAR`, `DEFAULT_CACHE_DIR` |
| `bynat.cleaning` | `BaseData`, `JsonFile`, `CsvFile`, `profile`, `ProfileReport`, `DatasetProfile`, `ColumnProfile`, plus `BynatAccessor`, `read_json`, `read_csv` |
| `bynat.cleaning.enrich` | Provider protocols `Translator`, `Geocoder`, `RateSource`, and their defaults `GoogleTranslatorProvider`, `NominatimGeocoder`, `YFinanceRateSource` |

> **Import submodules explicitly.** The top-level `bynat` namespace exposes `read_json`, `read_csv`, `BynatAccessor`, `profile`, and `ProfileReport`. Import the class API and profiling dataclasses from `bynat.cleaning` and the cache API from `bynat.caching`.

### Class-based API and accessor: key differences

| | Class-based API | pandas accessor |
| --- | --- | --- |
| Works on | `BaseData`, `JsonFile`, `CsvFile` | Any `pandas.DataFrame` |
| Return value | A new object by default | Always a new DataFrame |
| `inplace` argument | Accepted where in-place operation is meaningful | Not accepted |
| Cache directory | `cache_dir` constructor argument | `df.bynat.with_cache(...)` |

### The class-based API

`BaseData`, `JsonFile`, and `CsvFile` are supported public classes. `BaseData` is a `pandas.DataFrame` subclass. All three use the same cleaning code as the accessor.

| Class | Constructor parameters | Equivalent accessor or function form |
| --- | --- | --- |
| `BaseData` | `data=None`, `cache_dir=None`, `*args`, `**kwargs` | `pandas.DataFrame(...)` with `df.bynat`; use `df.bynat.with_cache(cache_dir)` to set a cache directory |
| `JsonFile` | `data=None`, `json_file_path=None`, `key=None`, `cache_dir=None`, `*args`, `**kwargs` | `bynat.read_json(json_file_path, key=None)`, then `df.bynat` methods |
| `CsvFile` | `data=None`, `csv_file_path=None`, `cache_dir=None`, `read_kwargs=None`, `*args`, `**kwargs` | `bynat.read_csv(csv_file_path, **read_kwargs)`, then `df.bynat` methods |

How `inplace` works:

- Every `BaseData` method in the [method reference](#cleaning-method-reference) accepts `inplace=False` where in-place operation is meaningful.
- With `inplace=True`, the method changes the original object and returns `None` (or the documented statistics result). This is supported and is not scheduled for removal.
- Without it, every method returns a new object, including early exits that previously returned the original object.

`BaseData` creates its `CacheManager` on first use and shares it with every frame pandas derives from it.

### The pandas accessor

The accessor is an optional extra way to use bynat on ordinary DataFrames. It does not replace the class-based API. It exposes the same cleaning operations through `df.bynat`.

- Accessor methods return new DataFrames and do not accept `inplace`.
- `df.bynat.with_cache(...)` returns an accessor that caches in another directory or in an explicit `CacheManager`.
- `bynat.read_csv` and `bynat.read_json` return plain DataFrames that you can use the accessor on right away.

### The same job in both styles

```python
# Class-based style
from bynat.cleaning import JsonFile
jobs = JsonFile(json_file_path="jobs.json", key="items", cache_dir="cache")
jobs = jobs.rename_all_DOTS()
jobs.translate_categorical_column("status", inplace=True)

# Accessor style
import bynat
jobs = bynat.read_json("jobs.json", key="items")
jobs = jobs.bynat.rename_all_DOTS()
jobs = jobs.bynat.with_cache("cache").translate_categorical_column("status")
```

The class-based version uses `JsonFile` and changes the object in place with `inplace=True`. The accessor version works on a plain DataFrame and reassigns the result.

### Common tasks

**Extract keywords from text.** This continues with the `records` DataFrame from the quick start:

```python
labeled = records.bynat.extract_keywords(
    "description",
    colors=["blue", "red"],
)
```

**Read and write files.** The loader helpers return plain DataFrames:

```python
import bynat

from_csv = bynat.read_csv("input.csv", sep=";")
from_json = bynat.read_json("input.json", key="items")

from_csv.bynat.save_to_json("output.json", orient="records")
```

### How the cleaning code is organized

Each function in the `columns`, `text`, `stats`, `enrich`, and `io` modules takes a DataFrame as its first argument and returns a new one by default. The classes and the accessor are thin layers over these functions. See the [module layout](#module-layout).

---

## Caching

Caching lets bynat remember results between runs instead of repeating slow lookups. Cleaning methods use it automatically. You can also use `CacheManager` directly.

`bynat.caching` provides:

- `CacheManager`
- `shared_cache_manager`
- `CACHE_DIR_ENV_VAR`
- `DEFAULT_CACHE_DIR`

Methods that translate, geocode, extract entities, or look up exchange rates use one shared `CacheManager` per cache directory. That is the `BYNAT_CACHE_DIR` directory or the default, unless `df.bynat.with_cache(...)` selects another directory or an explicit `CacheManager`.

### Where the cache is stored

`CacheManager` stores each function's cache as rows in a SQLite database.

- The database file is named `cache.sqlite3` and lives under `cache_dir`.
- If `cache_dir` is omitted (here or in `BaseData`, `JsonFile`, and `CsvFile`), bynat uses the `BYNAT_CACHE_DIR` environment variable when set. Otherwise it uses `Settings/Cache Data/Automated`.
- Relative cache paths are resolved from the current working directory. `~` and environment variables are expanded.
- The directory and database are created lazily. Constructing a manager creates neither. The SQLite backend is created on first cache access or on the first save of in-memory changes.

### Using CacheManager directly

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

### Cache rules

- Cache keys must be non-empty strings and cannot contain path separators.
- Values are stored as JSON inside SQLite rows, so use JSON-compatible values: strings, numbers, `None`, lists, and dictionaries.

### Saving and failures

`save_function_cache` writes added, changed, and removed entries in one SQLite transaction. If serialization, locking, or the commit fails:

- The failure is logged, not raised.
- Rows that were already committed stay intact.
- The pending in-memory changes are kept, and the next save retries them.

### Migrating legacy JSON caches

Older versions stored one `<func_name>_cache.json` file per function. If such a file exists, it is read only to migrate it into SQLite, at most once:

- Existing SQLite values take precedence over stale JSON values.
- Malformed or non-object files are renamed with a `.corrupt` suffix so you can recover them by hand.
- Unsupported nested structures are left in place, unmarked, for manual recovery.

### CacheManager reference

| Method | Parameters | What it does |
| --- | --- | --- |
| `load_function_cache(func_name)` | `func_name: str` | Returns the function's in-memory cache, loaded from SQLite. Creates the backend on first access and migrates any legacy JSON once. Returns an empty dictionary if the function has no entries. |
| `get_column_cache(func_name, col_name)` | `func_name: str`, `col_name: str` | Returns the mutable nested dictionary under `col_name`, creating it if needed. |
| `save_function_cache(func_name)` | `func_name: str` | Writes added, changed, and removed entries to SQLite in one transaction. Logs failures without raising and keeps pending changes for retry. |
| `delete_column_cache(func_name, col_name)` | `func_name: str`, `col_name: str` | Deletes a nested bucket and saves. It removes only rows the manager already knew about, so concurrent additions and unrelated buckets or functions are preserved. Logs an `INFO` message whether the bucket was absent or deleted. |

`_validate_cache_key` and `_get_file_path` are implementation helpers and not part of the public API.

---

## Advanced features

### Hierarchical statistics

Hierarchical statistics give you a typical value for each group at several levels of detail at once, for example per country and job title, then per country. bynat computes one statistic per group at every level of the hierarchy, from the full hierarchy down to its first column.

Pass one strategy or a list: `median`, `mean`, and `mode` (`mod` is accepted as an alias).

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

`stats` is returned as the caller's class (here `pandas.DataFrame`) with these columns:

- `hierarchy_level`
- `hierarchy_columns`
- `group_key`
- `target_column`
- `strategy`
- `statistic`

The median rows are:

| hierarchy_level | group_key | strategy | statistic |
| --- | --- | --- | --- |
| 2 | `("EG", "Analyst")` | median | 12.0 |
| 2 | `("EG", "Engineer")` | median | 30.0 |
| 2 | `("US", "Analyst")` | median | 80.0 |
| 1 | `("EG",)` | median | 14.0 |
| 1 | `("US",)` | median | 80.0 |

Details:

- `group_key` is always a tuple with one value per hierarchy column, including single-column levels such as `("EG",)`.
- Null group keys are kept.
- `mean` and `median` need numeric targets and raise pandas' `TypeError` otherwise.
- `mode` works for any hashable values and breaks ties by first occurrence.
- Target columns cannot also be hierarchy columns.

#### Choosing the output type

Use `output_type` to choose the result container:

```python
as_frame = salaries.bynat.get_hierarchical_stats("salary", hierarchy_cols="country", output_type=pd.DataFrame)
as_dict = salaries.bynat.get_hierarchical_stats("salary", hierarchy_cols="country", output_type=dict)
# as_dict["group_key"] == [("EG",), ("US",)]; as_dict["statistic"] == [14.0, 80.0]
```

`output_type` accepts:

- `None` (default): the caller's class
- `pandas.DataFrame`
- `dict`: column name to list of values
- A `BaseData` subclass

### Data quality profiling

`bynat` provides a comprehensive data quality profiling API to evaluate tabular datasets before cleaning, feature engineering, or modeling. Profiling inspects dataset-level completeness, duplicate records, in-memory footprint, data type distributions, and per-column quality metrics and descriptive statistics.

**Non-mutating guarantee:** Profiling is strictly read-only. It never modifies the input DataFrame, its indices, column names, or cell values.

#### Entry points

Profiling is available through four equivalent public entry points:

- **pandas accessor:** `df.bynat.profile(*columns, output_type=None)`
- **Class-based API:** `BaseData(df).profile(*columns, output_type=None)` (and on `JsonFile`, `CsvFile`)
- **Top-level function:** `bynat.profile(df, *columns, output_type=None)`
- **Cleaning module function:** `bynat.cleaning.profile(df, *columns, output_type=None)`

#### Column selection

By default, all columns in the DataFrame are profiled. You can also profile a subset of columns by passing column names as multiple positional arguments or as a sequence (list, tuple, or set):

```python
# Profile all columns
report = df.bynat.profile()

# Profile specific columns via multiple arguments
report = df.bynat.profile("age", "salary")

# Profile specific columns via sequence
report = df.bynat.profile(["age", "salary"])
```

Passing a column name that does not exist in the DataFrame raises a `KeyError`. Passing an object other than a `pandas.DataFrame` raises a `TypeError`.

#### Output types

The `output_type` argument determines the format of the returned report:

| `output_type` | Return type | Description |
| --- | --- | --- |
| `None` (default) or `ProfileReport` | `ProfileReport` | Rich report object supporting attribute access (`report.dataset`, `report.columns`), dictionary access (`report["dataset"]`), iteration, string `repr`, and export methods (`to_dict()`, `to_dataframe()`). |
| `dict` | `dict[str, Any]` | Nested pure Python dictionary composed exclusively of JSON-serializable primitives (native scalars, lists, and dicts). Directly serializable with `json.dumps`. |
| `pandas.DataFrame` | `pandas.DataFrame` | Tabular summary DataFrame containing one row per profiled column with core quality metrics and type-specific statistics prefixed with `stat_`. |
| `BaseData` subclass | Caller's class | Tabular summary DataFrame wrapped in the specified `BaseData` subclass. |

```python
import json
import pandas as pd
from bynat.cleaning import BaseData, ProfileReport, profile

# 1. Default ProfileReport object
report = profile(df)

# 2. Pure dictionary for API payloads or JSON logging
data_dict = profile(df, output_type=dict)
json_str = json.dumps(data_dict, indent=2)

# 3. Tabular summary DataFrame
summary_df = profile(df, output_type=pd.DataFrame)

# 4. BaseData subclass wrapper
summary_bd = profile(df, output_type=BaseData)
```

#### Practical examples

**Generating and inspecting a report programmatically:**

```python
import bynat
import pandas as pd

df = pd.DataFrame({
    "customer_id": [1001, 1002, 1003, 1004, 1004],
    "name": ["Alice", "Bob", "Charlie", "David", "David"],
    "age": [28.0, 34.0, None, 45.0, 45.0],
    "active": [True, True, False, True, True],
    "joined": pd.to_datetime(["2021-01-15", "2022-03-20", "2023-05-12", "2024-07-01", "2024-07-01"]),
})

report = df.bynat.profile()

# Inspect dataset-level metrics
print(f"Dataset shape: {report.dataset.row_count} rows x {report.dataset.column_count} columns")
print(f"Missing cells: {report.dataset.missing_count} ({report.dataset.missing_proportion:.1%})")
print(f"Duplicate rows: {report.dataset.duplicate_rows} ({report.dataset.duplicate_proportion:.1%})")
print(f"Memory: {report.dataset.memory_bytes} bytes")
print(f"Data types: {report.dataset.dtype_counts}")

# Inspect column-level quality metrics
age_col = report.columns["age"]
print(f"Column '{age_col.name}' ({age_col.dtype}, kind: {age_col.kind}):")
print(f"  Valid count: {age_col.count}, Missing: {age_col.missing_count} ({age_col.missing_proportion:.1%})")
print(f"  Distinct: {age_col.distinct_count} ({age_col.distinct_proportion:.1%}), Unique: {age_col.is_unique}")
print(f"  Mean: {age_col.stats['mean']}, Median: {age_col.stats['median']}, Outliers: {age_col.stats['outliers_count']}")
```

**Filtering columns by quality thresholds:**

```python
# Identify columns with missing values
columns_with_missing = [
    col.name for col in report.columns.values()
    if col.missing_proportion > 0.0
]

# Identify potential key/identifier columns (fully unique, non-null)
candidate_keys = [
    col.name for col in report.columns.values()
    if col.is_unique and col.missing_count == 0
]
```

**Tabular conversion and JSON serialization:**

```python
import json

# Convert column metrics into a tabular DataFrame for display or export
summary_table = report.to_dataframe()
print(summary_table[["column", "kind", "missing_proportion", "distinct_count", "stat_mean"]])

# Serialize full profile to a valid JSON string
json_export = json.dumps(report.to_dict(), indent=2)
```

#### Report structure

The profiling report consists of aggregate dataset metrics (`DatasetProfile`) and detailed per-column profiles (`ColumnProfile`).

##### DatasetProfile fields

| Field | Type | Description |
| --- | --- | --- |
| `row_count` | `int` | Total number of rows in the DataFrame. |
| `column_count` | `int` | Total number of profiled columns. |
| `total_cells` | `int` | Total number of cells (`row_count * column_count`). |
| `missing_count` | `int` | Total count of missing (`isna()`) cells across all profiled columns. |
| `missing_proportion` | `float` | Proportion of cells that are missing (`0.0` when `total_cells == 0`). |
| `duplicate_rows` | `int` | Count of duplicate rows using `DataFrame.duplicated(keep="first")` semantics. |
| `duplicate_proportion` | `float` | Proportion of rows that are duplicates (`0.0` when `row_count == 0`). |
| `empty_column_count` | `int` | Number of profiled columns containing zero non-null values. |
| `memory_bytes` | `int` | Total estimated memory consumption of the DataFrame in bytes (`deep=True`). |
| `dtype_counts` | `dict[str, int]` | Frequency mapping of pandas data type strings across profiled columns. |

##### ColumnProfile fields

| Field | Type | Description |
| --- | --- | --- |
| `name` | `Any` | Column label or identifier. |
| `dtype` | `str` | Pandas data type string representation (e.g. `'int64'`, `'float64'`, `'category'`). |
| `inferred_type` | `str` | Fine-grained type detected by pandas (e.g. `'integer'`, `'floating'`, `'string'`). |
| `kind` | `str` | Broad classification: `'numeric'`, `'categorical'`, `'boolean'`, `'datetime'`, or `'other'`. |
| `count` | `int` | Count of valid non-null values. |
| `missing_count` | `int` | Count of missing (`isna()`) values. |
| `missing_proportion` | `float` | Proportion of column values that are missing (`0.0` when `row_count == 0`). |
| `distinct_count` | `int` | Count of distinct non-null values. |
| `distinct_proportion` | `float` | Proportion of non-null values that are distinct (`0.0` when `count == 0`). |
| `is_unique` | `bool` | `True` if every non-null value is distinct and `count > 0`; otherwise `False`. |
| `stats` | `dict[str, Any]` | Mapping of descriptive statistics specific to the column's `kind`. |

##### Per-kind statistics keys

Every column of a given `kind` contains the exact same set of keys in its `stats` mapping. Unavailable or inapplicable metrics are represented as `None` (never `NaN`, `inf`, or omitted keys).

| Kind | Key | Type | Description |
| --- | --- | --- | --- |
| `numeric` | `min` | `float \| int \| None` | Minimum value in the column (`None` if empty). |
| `numeric` | `max` | `float \| int \| None` | Maximum value in the column (`None` if empty). |
| `numeric` | `mean` | `float \| None` | Arithmetic mean of non-null values (`None` if empty). |
| `numeric` | `std` | `float \| None` | Sample standard deviation (`None` if fewer than 2 non-null values). |
| `numeric` | `median` | `float \| int \| None` | 50th percentile (median) value (`None` if empty). |
| `numeric` | `q25` | `float \| int \| None` | 25th percentile (first quartile) value (`None` if empty). |
| `numeric` | `q75` | `float \| int \| None` | 75th percentile (third quartile) value (`None` if empty). |
| `numeric` | `iqr` | `float \| None` | Interquartile range (`q75 - q25`, `None` if empty). |
| `numeric` | `zeros_count` | `int` | Count of values equal to 0. |
| `numeric` | `zeros_proportion` | `float` | Proportion of non-null values equal to 0 (`0.0` when empty). |
| `numeric` | `outliers_count` | `int \| None` | Count of values outside `[q25 - 1.5*iqr, q75 + 1.5*iqr]` (`None` if fewer than 5 non-null values). |
| `categorical` | `top` | `Any` | Most frequently occurring non-null value (`None` if empty). |
| `categorical` | `top_frequency` | `int \| None` | Number of occurrences of the top value (`None` if empty). |
| `categorical` | `top_proportion` | `float \| None` | Proportion of non-null values matching the top value (`None` if empty). |
| `categorical` | `min_length` | `int \| None` | Shortest string length among values (`None` if non-string or empty). |
| `categorical` | `max_length` | `int \| None` | Longest string length among values (`None` if non-string or empty). |
| `categorical` | `mean_length` | `float \| None` | Average string length across values (`None` if non-string or empty). |
| `boolean` | `true_count` | `int` | Number of `True` values (`0` if empty). |
| `boolean` | `false_count` | `int` | Number of `False` values (`0` if empty). |
| `boolean` | `true_proportion` | `float \| None` | Proportion of `True` among non-null values (`None` if empty). |
| `datetime` | `min` | `str \| None` | Earliest timestamp formatted as an ISO 8601 string (`None` if empty). |
| `datetime` | `max` | `str \| None` | Latest timestamp formatted as an ISO 8601 string (`None` if empty). |
| `datetime` | `range_days` | `float \| None` | Elapsed duration between `min` and `max` in fractional days (`None` if empty). |
| `other` | `top` | `Any` | Most frequently occurring non-null value (`None` if empty). |
| `other` | `top_frequency` | `int \| None` | Number of occurrences of the top value (`None` if empty). |
| `other` | `top_proportion` | `float \| None` | Proportion of non-null values matching the top value (`None` if empty). |

#### Metric definitions and counting semantics

- **Missing values:** Detected using pandas `isna()`, identifying `None`, `numpy.nan`, and `pandas.NA`.
- **Duplicate rows:** Evaluated using `DataFrame.duplicated(keep="first")` semantics. The first occurrence of each unique row is retained; every subsequent identical row increments `duplicate_rows`. For DataFrames with unhashable cell values (such as nested `list` or `dict` objects), rows are compared by value via `_dedupe_key`.
- **Distinct count:** Counts distinct non-null values (`nunique(dropna=True)`). Unhashable values are compared by value via `_dedupe_key`.
- **Outlier detection:** Computed using Tukey's 1.5*IQR method. Values below `q25 - 1.5 * iqr` or above `q75 + 1.5 * iqr` are counted as outliers. Outlier detection requires at least 5 non-null observations (`count >= 5`); when fewer observations exist, `outliers_count` is set to `None`.
- **Sample standard deviation:** Calculated as sample standard deviation (Bessel-corrected, `ddof=1`). Requires at least 2 non-null observations (`count >= 2`); otherwise `std` is `None`.
- **Zero-denominator proportions:** Any proportion with a zero denominator (such as `missing_proportion` on an empty DataFrame, or `zeros_proportion` on an empty column) evaluates to `0.0`.
- **`None` convention:** Inapplicable or unavailable metrics are always represented as `None` rather than `float('nan')`, `float('inf')`, or omitted keys. The schema of the `stats` dictionary is guaranteed invariant for each `kind`.

#### Supported data types and classification

- **Nullable pandas dtypes:** Fully compatible with pandas nullable extensions (`Int64`, `Float64`, `boolean`, `string`).
- **Categoricals:** `pandas.Categorical` and categorical series are classified as kind `'categorical'`.
- **Datetimes and timedeltas:** Supports naive datetimes, timezone-aware datetimes (`datetime64[ns, tz]`), pandas `Timestamp`, and timedeltas.
- **Mixed and object columns:** Columns containing mixed scalar types (e.g. integers mixed with text strings) or unstructured Python objects are classified as kind `'other'`.
- **Empty and all-null columns:** Handled gracefully without raising exceptions. When a column has an explicit dtype (e.g. `pd.Series([], dtype="int64")`), kind detection selects the kind corresponding to the declared dtype. Untyped or empty object columns default to kind `'other'`.

### Enrichment with external services

Translation, geocoding, and currency conversion call external services, so use an appropriate cache directory and any service credentials or configuration that apply.

```python
import bynat
import pandas as pd

products = pd.DataFrame({"category": ["حذاء", "قميص", "حذاء"]})

translated = products.bynat.with_cache(".cache/bynat").translate_categorical_column(
    "category",
    target_lang="en",
    save_every=100,
)
```

#### Providers

These services are accessed through small provider protocols in `bynat.cleaning.enrich`. bynat uses a default provider unless a method receives `provider=`.

| Protocol | Method a provider defines | Default provider | Accepted by |
| --- | --- | --- | --- |
| `Translator` | `translate(text, target_lang) -> str \| None`, optionally `translate_batch(texts, target_lang) -> list` | `GoogleTranslatorProvider` (`deep-translator`) | `translate_categorical_column`, `translate_conditional_column` |
| `Geocoder` | `geocode(query) -> (latitude, longitude) \| None` | `NominatimGeocoder` (`geopy`, one request per second) | `fill_missing_coordinates` |
| `RateSource` | `yearly_rates(currency, start_year, end_year) -> {year: rate}` | `YFinanceRateSource` (`yfinance`) | `convert_to_usd` |

A provider is any object with the right method. No subclassing or monkeypatching is needed:

```python
import bynat
import pandas as pd

class UpperTranslator:
    def translate(self, text, target_lang):
        return text.upper()

df = pd.DataFrame({"status": ["pending", "done", "pending"]})

df = df.bynat.translate_categorical_column("status", provider=UpperTranslator())
```

#### How translation providers are called

- If the translator has `translate_batch`, it receives values in chunks of `save_every`.
- Otherwise `translate` runs in a thread pool. Its size comes from the provider's optional `max_workers` attribute (default 1). Request starts are spaced by its optional `min_interval` attribute, in seconds (default 0).
- `GoogleTranslatorProvider` uses four workers and at most five requests per second.
- If a translation is falsy or raises an exception, that value stays untranslated and is not cached.

#### How currency rates are fetched

`convert_to_usd` makes one `yearly_rates` call per currency. The call covers every year the look-back might need.

#### The shared lookup engine

The four cached enrichment methods and `extract_entities_nlp` all run through one engine:

1. Unique non-null values are looked up in the cache.
2. Cache misses are fetched.
3. The cache is checkpointed every `save_every` values.
4. Failures are logged and left uncached, so the next run retries them.

---

## Cleaning method reference

The accessor and the supported classes expose the same cleaning methods.

- Accessor methods take no `inplace` argument.
- Class methods accept `inplace` where in-place operation is meaningful.
- `get_hierarchical_stats` and `profile` return reports or statistics and have no in-place mode.

> **Always use the method's return value.** Most methods skip missing source or target columns with a logged warning and return an unchanged copy. Invalid arguments that would otherwise give silently wrong results raise instead: an unknown `strategy`, an empty `file_path`, invalid `save_to_sql` connection settings, or an unusable `extract_entities_nlp` configuration.

Methods are grouped by the kind of job they do:

- [Columns](#columns)
- [Text and language](#text-and-language)
- [Enrichment](#enrichment)
- [Outliers, missing values, and statistics](#outliers-missing-values-and-statistics)
- [Saving data](#saving-data)
- [Cache and object helpers](#cache-and-object-helpers)

### Columns

Rename, drop, reorder, and reshape columns.

| Method | Parameters | Purpose |
| --- | --- | --- |
| `drop_by_substring` | `substring` | Drops every column whose name contains `substring`. |
| `drop_CLASS_columns` | none | Drops columns containing the literal substring `__CLASS__`. |
| `rename_by_substring` | `old_substring`, `new_substring` | Replaces occurrences of one substring in column names. |
| `rename_all_DOTS` | `unified_separator="_"` | Replaces dots in all matching column names. |
| `reorder_columns` | `*first_cols` | Moves the named columns to the front and keeps the order of the others. Pass column names as positional arguments. |
| `move_column` | `col_to_move`, `col_target`, `position="after"` | Moves one column before or after another. `position` must be `"before"` or `"after"`. |
| `align_lat_lng` | none | Moves the exact, case-sensitive `longitude` column right after `latitude`. |
| `melt_transform_pivot` | `id_vars`, `value_vars=None`, `transform_func=None`, `aggfunc="first"`, `var_name="variable_name"`, `value_name="variable_value"`, `pivot_index=None`, `pivot_columns=None`, `**kwargs` | Melts selected columns, optionally transforms the melted values, and pivots back. `transform_func` must return the same number of values it receives. |
| `split_list_objects` | `source_col`, `remove_source_col=False`, `explode_rows=False`, `cartesian_explode=False`, `**target_cols_and_indices` | Extracts list elements by integer index, joins selected values, or explodes extracted lists in parallel or cartesian mode. Exploding rows changes the row count and resets the index. |

### Text and language

| Method | Parameters | Purpose |
| --- | --- | --- |
| `extract_keywords` | `*source_cols`, `remove_extracted=False`, `use_regex=False`, `**target_cols_and_keywords` | Searches the combined source text and writes matched keywords or regex results to named target columns. |
| `truncate_after_substring` | `regex=True`, `**target_cols_and_substrings` | Splits named columns at one or more substring or regex delimiters and keeps the text before the first delimiter. |
| `categorize_by_reference` | `*source_cols`, keyword-only `target_col`, `reference`, `default_category="Other"` | Searches the combined text with an Aho-Corasick automaton and assigns the category of the longest matching reference keyword. |
| `impute_by_language` | `reference_col`, `*target_cols`, `target_lang="en"` | Picks existing values by exact normalized match or language detection, then falls back to title-cased reference values. Uses deterministic `langdetect` seeding. |

Notes on `extract_keywords`:

- Keyword lists are passed as keyword arguments, one per target column.
- Literal matches are case-insensitive and title-cased.
- Each unique text is matched once, with a single combined pattern. For ASCII text, Aho-Corasick is used when `pyahocorasick` is installed.

### Enrichment

Translation, geocoding, currency conversion, and NLP. See [Enrichment with external services](#enrichment-with-external-services) for providers and the shared lookup engine.

| Method | Parameters | Purpose |
| --- | --- | --- |
| `translate_categorical_column` | `target_col`, `target_lang="en"`, `save_every=1000`, `provider=None` | Translates each unique non-null category once, caches the results, and maps them back. |
| `translate_conditional_column` | `partition_col`, `detect_col`, `target_col`, `target_lang="en"`, `sample_size=10`, `number_of_samples=3`, `threshold=0.5`, `save_every=1000`, `random_state=0`, `provider=None` | Samples each partition for language detection and translates the target values of partitions below the threshold. |
| `fill_missing_coordinates` | `*fallback_cols`, keyword-only `lat_col`, `long_col`, `save_every=1000`, `user_agent="data_cleaning_core_geocoder"`, `retry_not_found=False`, `provider=None` | Builds location queries from the fallback columns and geocodes missing coordinates with `geopy` and Nominatim. |
| `convert_to_usd` | `*target_cols`, keyword-only `country_col`, `year_col`, `new_cols=True`, `round_decimals=None`, `provider=None` | Infers each country's currency with `pycountry`, fetches yearly mean exchange rates, and divides the target values by the rate. |
| `extract_entities_nlp` | `*source_cols`, keyword-only `lexicon_source`, `spacy_model="en_core_web_sm"`, `exclude_pos=None`, `disable=("parser", "ner")`, `save_every=1000`, `noisy_terms=None`, `**target_cols_and_json_keys` | Loads lexicon data, processes text with spaCy, and writes entity lists to the target columns. |

#### `translate_conditional_column`

- Uses the translation provider and cached results.
- Partitions are processed with a single `groupby`.
- Sampling is reproducible for a given `random_state`. Pass `None` for unseeded sampling.
- `random_state` accepts an integer seed or a NumPy `Generator` or `RandomState`. One generator is drawn from for every sample, so repeated samples differ, while repeated calls with the same seed give the same results.

#### `fill_missing_coordinates`

- A failed query is retried with its leftmost part dropped: `"street, city, country"` → `"city, country"` → `"country"`.
- Results and not-found queries are cached. Pass `retry_not_found=True` to query the misses again.
- Set `user_agent` to identify your application, as Nominatim's usage policy requires.

#### `convert_to_usd`

- Rates come from the rate provider (Yahoo Finance by default), with one download per currency.
- If a year has no data, bynat looks back to earlier years.
- Lookups are cached. Unknown countries and past years without data are cached as misses. Network errors are not cached.

#### `extract_entities_nlp`

- Entity lists are cached per target, model, and pipeline configuration.
- `disable` accepts `None`, one component name, or an iterable. Components required for lemmatization are rejected, and unknown names produce a warning.
- The misspelled `noisey_terms` name remains as a separate, deprecated compatibility alias for `noisy_terms`.

### Outliers, missing values, and statistics

| Method | Parameters | Purpose |
| --- | --- | --- |
| `handle_outliers` | `*target_cols`, keyword-only `hierarchy_cols`, `min_samples=5`, `get_hierarchical_stats=False` | Replaces hierarchical-IQR outliers with `NaN`, relaxing the grouping hierarchy step by step. |
| `drop_outliers` | `*target_cols`, keyword-only `hierarchy_cols`, `min_samples=5`, `get_hierarchical_stats=False` | Drops rows that contain hierarchical-IQR outliers. Rows are dropped by position, so duplicated index labels are safe. |
| `fill_missing` | `*target_cols`, `strategy="median"`, `hierarchy_cols=None`, `get_hierarchical_stats=False` | Fills missing values step by step through a grouping hierarchy, using `median`, `mean`, `mode`, or the alias `mod`. |
| `get_hierarchical_stats` | `*target_cols`, `strategy="median"`, `hierarchy_cols=None`, `output_type=None` | Returns one statistic row per group, hierarchy level, target, and strategy. See [Hierarchical statistics](#hierarchical-statistics) for the output schema and `output_type` values. |
| `profile` | `*columns`, keyword-only `output_type=None` | Generates a structured data quality profile report covering dataset summary metrics, column types, completeness, uniqueness, and kind-specific statistics. |

#### Returning statistics from cleaning methods

For `handle_outliers`, `drop_outliers`, and `fill_missing`, passing `get_hierarchical_stats=True` reuses `get_hierarchical_stats()`. The statistics are computed from the original data, before the operation changes any values or rows.

- The return value is `(result, stats)`.
- With `inplace=True`, the operation changes the original object and returns `stats` directly.
- The outlier methods use the default hierarchical `median` statistic. `fill_missing` uses its requested `strategy`.

### Saving data

| Method | Parameters | Purpose |
| --- | --- | --- |
| `save_to_json` | `file_path`, `orient="records"`, `append=False`, `force_ascii=False`, `indent=4` | Writes JSON only when the content changed, or appends to and deduplicates existing JSON rows. |
| `save_to_sql` | `table_name`, `database_type`, `database_user`, `database_password`, `database_host`, `database_port`, `database_name`, `database_driver=None`, `columns_types=None`, `chunk_size=None`, `fast_executemany=False`, `if_exists="append"`, `index=False` | Builds a SQLAlchemy connection URL and writes the frame with `DataFrame.to_sql`. |

Notes:

- `save_to_json` deduplicates rows with list or dictionary values by their JSON content. Datetime columns are formatted as `%Y-%m-%d %H:%M:%S`.
- `save_to_sql` uses `database_name` as a file path for SQLite. SQL Server requires `database_driver`.

### Cache and object helpers

| Method | Parameters | Purpose |
| --- | --- | --- |
| `with_cache` (accessor only) | `cache` | Returns an accessor that caches in another directory or `CacheManager`. |
| `clear_cache_for_column` | `func_name`, `target_col` | Deletes one cache bucket through the attached `CacheManager`. |
| `view` (`BaseData` only) | none | Returns a plain `pandas.DataFrame` view or copy of the object. |

---

## Module layout

Use this table to find where things live if you want to read the source or import something specific.

| Module | Purpose | Contents |
| --- | --- | --- |
| `bynat.caching.manager` | Remembering results | `CacheManager` and `shared_cache_manager` |
| `bynat.cleaning.columns` | Tidying and reshaping columns | Column renaming, dropping, reordering, moving, `melt_transform_pivot`, `split_list_objects` |
| `bynat.cleaning.text` | Working with text | `extract_keywords`, `truncate_after_substring`, `categorize_by_reference` |
| `bynat.cleaning.stats` | Outliers, gaps, and group statistics | `handle_outliers`, `drop_outliers`, `fill_missing`, `get_hierarchical_stats` |
| `bynat.cleaning.profile` | Data quality profiling | `profile`, `ProfileReport`, `DatasetProfile`, `ColumnProfile` |
| `bynat.cleaning.enrich` | Adding information from outside the table | `translate`, `geocode`, `currency`, `nlp`, the shared cached-lookup `engine`, and `providers` |
| `bynat.cleaning.io` | Reading and writing | `save_to_json`, `save_to_sql`, `read_json`, `read_csv` |
| `bynat.cleaning.accessor` | The `df.bynat` entry point | The `df.bynat` accessor |
| `bynat.cleaning.base` | The class-based API | `BaseData`, `JsonFile`, `CsvFile` |

The legacy module paths `bynat.caching.Caching` and `bynat.cleaning.Cleaning` still import. They resolve to `bynat.caching.manager` and `bynat.cleaning.base`.

---

## Limitations and operational notes

Read these before you rely on bynat in a pipeline.

### Optional integrations

Optional integrations are lazy-loaded, so basic `bynat.cleaning` imports and core DataFrame methods work with only `numpy` and `pandas`. A method that needs an absent integration raises a clear `ImportError` when called.

### External services

- Translation uses `deep-translator` by default and may make external requests.
- Geocoding uses Nominatim through `geopy`, with a one-second rate limit.
- Currency conversion uses Yahoo Finance history through `yfinance`.

These services can fail, rate-limit requests, or change their results. Cached values persist locally in SQLite.

### NLP extraction

- `extract_entities_nlp` needs a compatible spaCy model installed separately.
- `disable=...` accepts `None`, a component name, or an iterable. It defaults to disabling `parser` and `ner`, and rejects components required for lemmatization.
- If a requested component is not in the loaded pipeline, bynat warns and continues.
- A missing or unloadable model raises spaCy's `OSError`.

### SQL

- `save_to_sql` lets database exceptions propagate to the caller and always disposes its engine.
- It receives database credentials as ordinary arguments. Do not hard-code secrets in application code.

### Cache behavior

- Cache entries live in a single SQLite database, not one JSON file per function.
- Saves run inside SQLite transactions and WAL checkpoints. A failed or interrupted save is logged and rolled back without raising. Rows from earlier successful saves stay intact, and pending in-memory changes are retried on the next save.
- Concurrent managers sharing one database merge disjoint key updates. If more than one manager writes the same key, the last committed write wins.
- Values must be JSON-serializable.

### Logging

Messages go through the standard `logging` module under the `bynat` logger:

- Skipped columns and failed lookups are `WARNING` messages and appear on stderr by default.
- Progress and checkpoints are `INFO` messages.

```python
import logging

logging.basicConfig(level=logging.INFO)              # show progress
logging.getLogger("bynat").setLevel(logging.ERROR)   # or hide warnings
```

---

## Development and verification

The test suite uses `pytest`. It avoids network access, secrets, user files, and external services. Network-backed integrations (translation, geocoding, exchange rates, spaCy) are tested with fake modules.

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m compileall -q bynat
ruff check bynat tests benchmarks
python -m mypy
```

To also run the tests that use the real `pyahocorasick` and SQLAlchemy/SQLite, install `".[categorization,sql]"`. Those tests are skipped otherwise.

CI runs the suite on Ubuntu and Windows with Python 3.11 and 3.13, plus Ruff, mypy, and an extras job.

### Type checking

Type checking is gradual. `python -m mypy` reads its scope from `[tool.mypy]` in `pyproject.toml`. That scope covers the cache manager and the cleaning helper modules (`_common`, `accessor`, `columns`, `text`, `stats`, `io`, `profile`, `enrich`), checked against `pandas-stubs`. The supported classes in `bynat/cleaning/base.py` remain a separate gradual-typing boundary.

To bring a module into scope:

1. Add it to `files` in `[tool.mypy]`.
2. Fix what mypy reports with precise annotations, not `Any` or `# type: ignore`.
3. Keep the runtime behavior unchanged.

### Benchmarks

`benchmarks/` holds timing scripts that compare the current implementations with the `0.3.0` algorithms. See [`benchmarks/README.md`](benchmarks/README.md).

### Building packages

Building a wheel or source archive requires the configured setuptools build backend and wheel tooling.

---

## License

MIT. See [LICENSE](LICENSE).