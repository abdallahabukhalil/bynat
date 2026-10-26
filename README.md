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
  <strong>A Python toolkit for simplifying data workflows.</strong><br>
  Small, reusable utilities for tidying, enriching, loading, saving and caching tabular data.
</p>

<p align="center">
  Python <code>&gt;=3.11</code> &nbsp;·&nbsp; MIT license &nbsp;·&nbsp; version <code>0.6.0</code>
</p>

<div align="center">

  [What it is](#what-is-bynat) ·
  [Installation](#installation) ·
  [Using bynat](#how-to-use-bynat) ·
  [To Know](#limitations-and-operational-notes) ·
  [Development](#development-and-verification)

</div>

---

## What is bynat?

`bynat` is a Python utility package for tabular-data cleaning and small SQLite-backed caches.

Think of it as a small workbench for the fiddly jobs around a table of data: renaming and reshaping columns, pulling keywords out of text, spotting outliers and filling gaps by comparing like with like, translating or geocoding values, converting amounts to USD, reading and writing files, and remembering the results of slow lookups so you do not repeat them. (A `DataFrame` is simply pandas' name for a table of rows and columns.)

It comes in a few flavors, and you can mix them:

- **The class-based API is first-class.** Use `BaseData`, `JsonFile`, and `CsvFile`.
- **The `df.bynat` accessor is optional and complementary.** It gives ordinary `pandas.DataFrame` objects the same cleaning operations.
- **The caching API** stores per-function dictionaries in a SQLite database.

New here? Install it, then head to the [Quick start](#quick-start).

### Project status

Version `0.6.0` is the project version declared in `pyproject.toml`. See [CHANGELOG.md](CHANGELOG.md) for behavior changes since `0.1.1` and [ROADMAP.md](./docs/ROADMAP.md) for planned work.

## Installation

```bash
python -m pip install bynat
```

For a checkout, install it in editable mode:

```bash
python -m pip install -e .
```

### Requirements and optional integrations

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

> **Two things that are not bundled:** `extract_entities_nlp` also needs a compatible spaCy model, defaulting to `en_core_web_sm`, and `save_to_sql` needs the DBAPI driver required by the SQLAlchemy URL. Neither model files nor database drivers are bundled.

## How to Use bynat

bynat has two parts that work well together: **cleaning** (`bynat.cleaning`) and **caching** (`bynat.caching`). You can reach them with module imports, direct imports of public objects, or the optional pandas accessor. Cleaning methods that call outside services (translation, geocoding, NLP extraction, or exchange-rate lookup) use a shared `CacheManager`, so the two parts are really one toolkit.

The path through this section goes from easy to detailed:

1. [Quick start](#quick-start): one small example.
2. [Choosing an import style](#choosing-an-import-style): module imports, direct imports, or the accessor.
3. [Cleaning (`bynat.cleaning`)](#cleaning-bynatcleaning): everyday tasks and the class-based API.
4. [Caching (`bynat.caching`)](#caching-bynatcaching): where results are remembered and how to control it.
5. [The pandas accessor (`df.bynat`)](#the-pandas-accessor-dfbynat): the optional extra way in.
6. [Advanced capabilities](#advanced-capabilities): hierarchical statistics and external-service providers.
7. [Cleaning method reference](#cleaning-method-reference): every method, its parameters, and its behavior, for when you need the details.

### Quick start

Here is the shortest path from a messy table to a tidy one. `import bynat` registers the `df.bynat` accessor on every `pandas.DataFrame`:

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

That is two steps: `rename_all_DOTS` replaces dots in column names (with `_` by default), then `drop_by_substring("user")` drops every column whose name contains `user`. What is left is the `description` column.

Prefer working with a class instead of the accessor? The same cleanup with `BaseData`, the `pandas.DataFrame` subclass:

```python
from bynat.cleaning import BaseData

cleaned = BaseData(records).rename_all_DOTS().drop_by_substring("user")
```

Both give you a new object and leave `records` untouched. (Class methods return a new object by default; accessor methods always return new DataFrames.)

### Choosing an import style

There are three supported ways in. None of them is "the" way; pick whichever reads best in your code.

| Style | Looks like | Handy when |
| --- | --- | --- |
| Module imports | `import bynat.cleaning as cl`<br>`import bynat.caching as ch` | You like reaching objects through a module namespace, such as `cl.BaseData` or `ch.CacheManager`. |
| Direct imports | `from bynat.cleaning import BaseData` | You want specific public objects under their own names. |
| Pandas accessor | `import pandas as pd`<br>`import bynat as by` | You already have a plain `pandas.DataFrame` and want to call `df.bynat.<method>(...)` on it. |

Module imports:

```python
import bynat.cleaning as cl
import bynat.caching as ch
```

Direct imports of public objects:

```python
from bynat.cleaning import BaseData
```

The pandas accessor, which is registered as soon as `bynat` is imported:

```python
import pandas as pd
import bynat as by
```

The accessor is always called `bynat` (`df.bynat...`), whatever alias you give the module. It is an additional interface, not a replacement: the class-based API stays first-class. Importing `bynat` or any of its submodules registers the accessor.

Here are all the supported imports in one place:

```python
import bynat                          # registers the df.bynat accessor
from bynat import read_csv, read_json
from bynat.caching import CacheManager, shared_cache_manager
from bynat.cleaning import BaseData, CsvFile, JsonFile
from bynat.cleaning.enrich import Geocoder, RateSource, Translator
```

- `bynat`: `read_json`, `read_csv`, `BynatAccessor`. Importing `bynat` (or any submodule) registers the `bynat` accessor on `pandas.DataFrame`.
- `bynat.caching`: `CacheManager`, `shared_cache_manager`, `CACHE_DIR_ENV_VAR`, `DEFAULT_CACHE_DIR`
- `bynat.cleaning`: `BynatAccessor`, `read_json`, `read_csv`, and the supported `BaseData`, `JsonFile`, `CsvFile` classes
- `bynat.cleaning.enrich`: the provider protocols `Translator`, `Geocoder`, `RateSource` and their defaults `GoogleTranslatorProvider`, `NominatimGeocoder`, `YFinanceRateSource`

> **Import submodules explicitly.** The top-level `bynat` namespace exposes only `read_json`, `read_csv`, and `BynatAccessor`; import the supported class API from `bynat.cleaning` (for example, `from bynat.cleaning import BaseData`) and the cache API from `bynat.caching`.

Two practical differences between the styles: accessor methods return new DataFrames and do not accept `inplace`, while class methods accept `inplace` where in-place operation is meaningful (details in [The class-based API](#the-class-based-api)).

### Cleaning (`bynat.cleaning`)

This is where the cleaning methods live. Each function in `columns`, `text`, `stats`, `enrich`, and `io` takes the DataFrame as its first argument and returns a new one by default; the supported classes and optional accessor are thin layers over them.

- The class-based API remains first-class: instantiate `BaseData(data)`, `JsonFile`, or `CsvFile` and call the cleaning methods directly.
- After `import bynat`, every `pandas.DataFrame` also has the optional `bynat` accessor for the same cleaning operations.
- Accessor methods return new DataFrames and do not accept `inplace`; class methods return a new object by default, while supported `inplace=True` calls mutate the original object and return `None` (or the documented statistics result).
- Methods that perform translation, geocoding, NLP extraction, or exchange-rate lookup use one shared `CacheManager` per cache directory: the `BYNAT_CACHE_DIR` directory or the default, unless `df.bynat.with_cache(...)` selects another directory or an explicit `CacheManager`. See [Caching](#caching-bynatcaching).

#### Common tasks: keywords and files

Pull keywords out of text. This continues with the `records` frame from the quick start:

```python
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

#### The class-based API

`BaseData`, `JsonFile`, and `CsvFile` are supported public classes. `BaseData` is a supported `pandas.DataFrame` subclass, and all three use the same cleaning implementations as the optional accessor.

| Class | Constructor parameters | Equivalent accessor / function form |
| --- | --- | --- |
| `BaseData` | `data=None`, `cache_dir=None`, `*args`, `**kwargs` | `pandas.DataFrame(...)` with optional `df.bynat`; `df.bynat.with_cache(cache_dir)` for a cache directory |
| `JsonFile` | `data=None`, `json_file_path=None`, `key=None`, `cache_dir=None`, `*args`, `**kwargs` | `bynat.read_json(json_file_path, key=None)` followed by optional `df.bynat` methods |
| `CsvFile` | `data=None`, `csv_file_path=None`, `cache_dir=None`, `read_kwargs=None`, `*args`, `**kwargs` | `bynat.read_csv(csv_file_path, **read_kwargs)` followed by optional `df.bynat` methods |

Every supported `BaseData` method in the [cleaning method reference](#cleaning-method-reference) also accepts `inplace=False` where in-place operation is meaningful. With `inplace=True`, the method mutates the original object and returns `None` (or the documented statistics result); it is supported and is not scheduled for removal. Without it, every method returns a new object, including early exits that previously returned the original object itself. `BaseData` creates its `CacheManager` on first use and shares it with every frame pandas derives from it.

The same workflow in both styles is shown in [The pandas accessor (`df.bynat`)](#the-pandas-accessor-dfbynat). Looking for one specific method? Jump to the [Cleaning method reference](#cleaning-method-reference).

### Caching (`bynat.caching`)

Caching is what lets bynat remember results between runs instead of repeating slow lookups. `bynat.caching` provides `CacheManager`, the shared-manager helper `shared_cache_manager`, and the constants `CACHE_DIR_ENV_VAR` and `DEFAULT_CACHE_DIR`. Cleaning methods use it for you; you can also use `CacheManager` directly.

#### How a cache is stored and located

`CacheManager` stores each function's cache as rows in a SQLite database. Where it lives, and when it appears:

- The database is named `cache.sqlite3` and sits under `cache_dir`.
- When `cache_dir` is omitted (here and in `BaseData`, `JsonFile`, and `CsvFile`), the `BYNAT_CACHE_DIR` environment variable is used if set, otherwise `Settings/Cache Data/Automated`.
- Relative cache paths are resolved from the current working directory; `~` and environment variables are expanded.
- The cache directory and database are created lazily: constructing a manager creates neither, and the SQLite backend is created on first cache access or on the first save of in-memory changes.
- Legacy `<func_name>_cache.json` files, if present, are read only for a one-time migration into SQLite.

Using it directly:

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

#### Rules, safety, and migration

- Cache keys must be non-empty strings and cannot contain path separators.
- Values are JSON-serialized inside SQLite rows, so supported values stay JSON-compatible: strings, numbers, `None`, lists, and dictionaries.
- `save_function_cache` applies the added, changed, and removed entries in one SQLite transaction; serialization, locking, or commit failures are logged rather than raised, committed rows stay intact, and the pending in-memory changes are kept so the next save retries them.
- A legacy `<func_name>_cache.json` file is migrated into SQLite at most once: existing SQLite values take precedence over stale JSON, malformed or non-object files are quarantined to a `.corrupt` name for manual recovery, and unsupported nested structures are left in place, unmarked, for recovery.

#### `CacheManager` methods and parameters

| Method | Parameters | Behavior |
| --- | --- | --- |
| `load_function_cache(func_name)` | `func_name: str` | Return the function's in-memory cache loaded from SQLite, creating the backend on first access; any legacy JSON is migrated once, and a function with no entries returns an empty dictionary. |
| `get_column_cache(func_name, col_name)` | `func_name: str`, `col_name: str` | Return or create the ordinary mutable nested dictionary under `col_name`. |
| `save_function_cache(func_name)` | `func_name: str` | Write the added, changed, and removed entries to SQLite in one transaction; log failures without raising and keep pending changes for retry. |
| `delete_column_cache(func_name, col_name)` | `func_name: str`, `col_name: str` | Delete a nested bucket and save; the deletion removes only rows the manager already knew about, preserving concurrent additions and unrelated buckets or functions. Logs an `INFO` message when the bucket is absent or deleted. |

`_validate_cache_key` and `_get_file_path` are implementation helpers and are not part of the public API.

### The pandas accessor (`df.bynat`)

The accessor is an optional, additional way to use bynat on ordinary `pandas.DataFrame` objects. It does not replace the class-based or module-based API; it exposes the same cleaning operations through `df.bynat`. After `import bynat` (or importing any bynat submodule), every `pandas.DataFrame` has it.

- Accessor methods return new DataFrames and do not accept `inplace`.
- `df.bynat.with_cache(...)` returns an accessor that caches in another directory or an explicit `CacheManager`.
- `bynat.read_csv` and `bynat.read_json` return plain DataFrames you can use it on right away.
- The accessor is always spelled `df.bynat`, whatever alias you import the module under.

Here are the two styles side by side, doing the same job:

```python
# Class-based style
from bynat.cleaning import JsonFile
jobs = JsonFile(json_file_path="jobs.json", key="items", cache_dir="cache")
jobs = jobs.rename_all_DOTS()
jobs.translate_categorical_column("status", inplace=True)

# Optional accessor style
import bynat
jobs = bynat.read_json("jobs.json", key="items")
jobs = jobs.bynat.rename_all_DOTS()
jobs = jobs.bynat.with_cache("cache").translate_categorical_column("status")
```

The class-based version uses `JsonFile` and mutates in place with `inplace=True`; the accessor version works on a plain DataFrame and reassigns the result. For the full class table, see [The class-based API](#the-class-based-api).

### Advanced capabilities

#### Hierarchical statistics

Want a typical value for each group, at several levels of detail at once (say, per country and job title, then per country)? Hierarchical statistics compute one statistic per group at every level of a hierarchy, from the full hierarchy down to its first column. Pass one strategy or a list of `median`, `mean`, and `mode` (`mod` is accepted as an alias):

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

#### Enrichment and external-service providers

Network-backed enrichment is explicit in the method behavior and should be used with an appropriate cache directory and service credentials/configuration where applicable:

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

Translation, geocoding, and currency conversion call external services through small provider protocols in `bynat.cleaning.enrich`. The defaults are used unless a method receives `provider=`:

| Protocol | Method a provider defines | Default | Accepted by |
| --- | --- | --- | --- |
| `Translator` | `translate(text, target_lang) -> str \| None`, optionally `translate_batch(texts, target_lang) -> list` | `GoogleTranslatorProvider` (`deep-translator`) | `translate_categorical_column`, `translate_conditional_column` |
| `Geocoder` | `geocode(query) -> (latitude, longitude) \| None` | `NominatimGeocoder` (`geopy`, one request per second) | `fill_missing_coordinates` |
| `RateSource` | `yearly_rates(currency, start_year, end_year) -> {year: rate}` | `YFinanceRateSource` (`yfinance`) | `convert_to_usd` |

A provider is any object with the right method; no subclassing or monkeypatching is needed:

```python
import bynat
import pandas as pd

class UpperTranslator:
    def translate(self, text, target_lang):
        return text.upper()

df = pd.DataFrame({"status": ["pending", "done", "pending"]})

df = df.bynat.translate_categorical_column("status", provider=UpperTranslator())
```

Translators with `translate_batch` receive the values in chunks of `save_every`. Otherwise `translate` runs in a thread pool sized by the provider's optional `max_workers` attribute (default 1), with request starts spaced by its optional `min_interval` attribute in seconds (default 0). `GoogleTranslatorProvider` uses four workers and at most five requests per second. A falsy translation or a raised exception leaves that value untranslated and uncached. `convert_to_usd` makes one `yearly_rates` call per currency, covering every year its look-back can need.

All four cached methods, and `extract_entities_nlp`, run through one engine: unique non-null values are looked up in the cache, the misses are fetched, the cache is checkpointed every `save_every` values, and failures are logged and left uncached so the next run retries them.

### Cleaning method reference

The optional accessor and the supported classes expose the same cleaning methods. Accessor methods take no `inplace` argument; class methods accept it where in-place operation is meaningful. The statistics-only `get_hierarchical_stats` method returns statistics and does not expose an in-place mode.

The methods are grouped by the kind of job they do.

**Columns: rename, drop, reorder, reshape**

| Method | Parameters | Purpose and important behavior |
| --- | --- | --- |
| `drop_by_substring` | `substring` | Drop every column whose name contains `substring`. |
| `drop_CLASS_columns` | none | Drop columns containing the literal substring `__CLASS__`. |
| `rename_by_substring` | `old_substring`, `new_substring` | Replace occurrences of one substring in column names. |
| `rename_all_DOTS` | `unified_separator="_"` | Replace dots in all matching column names. |
| `reorder_columns` | `*first_cols` | Move the named columns to the front while preserving the order of other columns. Pass column names as positional arguments. |
| `move_column` | `col_to_move`, `col_target`, `position="after"` | Move one column before or after another. `position` must be `"before"` or `"after"`. |
| `align_lat_lng` | none | Move exact, case-sensitive `longitude` immediately after `latitude`. |
| `melt_transform_pivot` | `id_vars`, `value_vars=None`, `transform_func=None`, `aggfunc="first"`, `var_name="variable_name"`, `value_name="variable_value"`, `pivot_index=None`, `pivot_columns=None`, `**kwargs` | Melt selected columns, optionally transform the melted value series, and pivot back. `transform_func` must return the same number of values. |
| `split_list_objects` | `source_col`, `remove_source_col=False`, `explode_rows=False`, `cartesian_explode=False`, `**target_cols_and_indices` | Extract list elements by integer index, join selected values, or explode extracted lists in parallel/cartesian mode. Exploding rows changes the row count and resets the index. |

**Text and language**

| Method | Parameters | Purpose and important behavior |
| --- | --- | --- |
| `extract_keywords` | `*source_cols`, `remove_extracted=False`, `use_regex=False`, `**target_cols_and_keywords` | Search combined source text and write matched keywords/regex results to named target columns. Literal matches are case-insensitive and title-cased; keyword lists are passed as keyword arguments. Each unique text is matched once with a single combined pattern (Aho-Corasick for ASCII text when `pyahocorasick` is installed). |
| `truncate_after_substring` | `regex=True`, `**target_cols_and_substrings` | Split named columns at one or more substring/regex delimiters and keep the text before the first delimiter. |
| `categorize_by_reference` | `*source_cols`, keyword-only `target_col`, `reference`, `default_category="Other"` | Search combined text with an Aho-Corasick automaton and assign the longest matching reference keyword's category. |
| `impute_by_language` | `reference_col`, `*target_cols`, `target_lang="en"` | Choose existing values by exact normalized match or language detection, then fall back to title-cased reference values. Uses deterministic `langdetect` seeding. |

**Enrichment: translation, geocoding, currency, NLP**

| Method | Parameters | Purpose and important behavior |
| --- | --- | --- |
| `translate_categorical_column` | `target_col`, `target_lang="en"`, `save_every=1000`, `provider=None` | Translate each unique non-null category once, cache results, and map them back. Uses the translation provider. |
| `translate_conditional_column` | `partition_col`, `detect_col`, `target_col`, `target_lang="en"`, `sample_size=10`, `number_of_samples=3`, `threshold=0.5`, `save_every=1000`, `random_state=0`, `provider=None` | Sample each partition for language detection and translate target values for partitions below the threshold. Partitions are processed with one `groupby`; sampling is reproducible for a given `random_state` (pass `None` for unseeded sampling). `random_state` accepts an integer seed or a NumPy `Generator` or `RandomState`; one generator is drawn from for every sample, so repeated samples differ while repeated calls with the same seed are reproducible. Uses the translation provider and cached results. |
| `fill_missing_coordinates` | `*fallback_cols`, keyword-only `lat_col`, `long_col`, `save_every=1000`, `user_agent="data_cleaning_core_geocoder"`, `retry_not_found=False`, `provider=None` | Build location queries from fallback columns and geocode missing coordinates with `geopy`/Nominatim. A failed query is retried with its leftmost part dropped (`"street, city, country"` → `"city, country"` → `"country"`). Results and not-found queries are cached; pass `retry_not_found=True` to re-query misses. Set `user_agent` to identify your application, as Nominatim's usage policy requires. |
| `convert_to_usd` | `*target_cols`, keyword-only `country_col`, `year_col`, `new_cols=True`, `round_decimals=None`, `provider=None` | Infer country currencies with `pycountry`, fetch yearly mean rates through the rate provider (Yahoo Finance by default) with one download per currency (looking back to earlier years when a year has no data), cache lookups, and divide target values by the rate. Unknown countries and past years without data are cached as misses; network errors are not cached. |
| `extract_entities_nlp` | `*source_cols`, keyword-only `lexicon_source`, `spacy_model="en_core_web_sm"`, `exclude_pos=None`, `disable=("parser", "ner")`, `save_every=1000`, `noisy_terms=None`, `**target_cols_and_json_keys` | Load lexicon data from JSON or a dictionary, process text with spaCy, cache entity lists per target/model/pipeline configuration, and write them to target columns. `disable` accepts None, one component name, or an iterable; required lemmatization components are rejected and unknown names warn. The misspelled `noisey_terms` name remains a separate deprecated compatibility alias. |

**Outliers, missing values, and statistics**

| Method | Parameters | Purpose and important behavior |
| --- | --- | --- |
| `handle_outliers` | `*target_cols`, keyword-only `hierarchy_cols`, `min_samples=5`, `get_hierarchical_stats=False` | Replace hierarchical-IQR outliers with `NaN`, progressively relaxing the grouping hierarchy. When enabled, return `(result, stats)` with the original-data hierarchical median statistics. |
| `drop_outliers` | `*target_cols`, keyword-only `hierarchy_cols`, `min_samples=5`, `get_hierarchical_stats=False` | Drop rows containing hierarchical-IQR outliers. Rows are dropped by position, so duplicated index labels are safe. When enabled, return `(result, stats)` with the original-data hierarchical median statistics. |
| `fill_missing` | `*target_cols`, `strategy="median"`, `hierarchy_cols=None`, `get_hierarchical_stats=False` | Fill missing values progressively through a grouping hierarchy using `median`, `mean`, `mode`, or the accepted alias `mod`. When enabled, return `(result, stats)` with statistics for the requested strategy computed before imputation. |
| `get_hierarchical_stats` | `*target_cols`, `strategy="median"`, `hierarchy_cols=None`, `output_type=None` | Return one statistic row per group, hierarchy level, target, and strategy. See the [hierarchical statistics example](#hierarchical-statistics) for the output schema and `output_type` values. |

For `handle_outliers`, `drop_outliers`, and `fill_missing`, passing `get_hierarchical_stats=True` reuses `get_hierarchical_stats()` and computes the statistics from the original data before the cleaning operation changes values or rows. The return value is `(result, stats)`; with `inplace=True`, the operation mutates the original object and returns `stats` directly. The outlier methods use the default hierarchical `median` statistic; `fill_missing` uses its requested `strategy`.

**Saving data**

| Method | Parameters | Purpose and important behavior |
| --- | --- | --- |
| `save_to_json` | `file_path`, `orient="records"`, `append=False`, `force_ascii=False`, `indent=4` | Write JSON only when changed, or append/deduplicate existing JSON rows. Rows with list or dictionary values are deduplicated by their JSON content. Datetime columns are formatted as `%Y-%m-%d %H:%M:%S`. |
| `save_to_sql` | `table_name`, `database_type`, `database_user`, `database_password`, `database_host`, `database_port`, `database_name`, `database_driver=None`, `columns_types=None`, `chunk_size=None`, `fast_executemany=False`, `if_exists="append"`, `index=False` | Build a SQLAlchemy connection URL and write the frame with `DataFrame.to_sql`. SQLite uses `database_name` as a path; SQL Server requires `database_driver`. |

**Cache and object helpers**

| Method | Parameters | Purpose and important behavior |
| --- | --- | --- |
| `with_cache` (accessor only) | `cache` | Return an accessor that caches in another directory or `CacheManager`. |
| `clear_cache_for_column` | `func_name`, `target_col` | Delete one cache bucket through the attached `CacheManager`. |
| `view` (`BaseData` only) | none | Return a plain `pandas.DataFrame` view/copy of the object. |

> **Always use the method's return value.** Most methods skip missing source/target columns with a logged warning and return an unchanged copy. Invalid arguments that would otherwise produce silently wrong results raise instead: an unknown `strategy`, an empty `file_path`, invalid `save_to_sql` connection settings, or an unusable `extract_entities_nlp` configuration.

## Module layout

Where everything lives, if you want to read the source or import something specific:

| Module | In plain words | Contents |
| --- | --- | --- |
| `bynat.caching.manager` | Remembering results | `CacheManager` and `shared_cache_manager` |
| `bynat.cleaning.columns` | Tidying and reshaping columns | Column renaming, dropping, reordering, moving, `melt_transform_pivot`, `split_list_objects` |
| `bynat.cleaning.text` | Making sense of text | `extract_keywords`, `truncate_after_substring`, `categorize_by_reference` |
| `bynat.cleaning.stats` | Outliers, gaps, and group statistics | `handle_outliers`, `drop_outliers`, `fill_missing`, `get_hierarchical_stats` |
| `bynat.cleaning.enrich` | Adding information from outside the table | `translate`, `geocode`, `currency`, `nlp`, the shared cached-lookup `engine`, and `providers` |
| `bynat.cleaning.io` | Reading and writing | `save_to_json`, `save_to_sql`, `read_json`, `read_csv` |
| `bynat.cleaning.accessor` | The `df.bynat` entry point | The `df.bynat` accessor |
| `bynat.cleaning.base` | The class-based API | The supported `BaseData`, `JsonFile`, `CsvFile` classes |

The legacy module paths `bynat.caching.Caching` and `bynat.cleaning.Cleaning` still import and resolve to `bynat.caching.manager` and `bynat.cleaning.base`.

## Limitations and operational notes

A few things worth knowing before you rely on it:

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
- The top-level `bynat` namespace exposes only `read_json`, `read_csv`, and `BynatAccessor`; import the supported class API from `bynat.cleaning` (for example, `from bynat.cleaning import BaseData`).

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

Type checking is gradual. `python -m mypy` reads its scope from `[tool.mypy]` in `pyproject.toml`: the cache manager and the cleaning helper modules (`_common`, `accessor`, `columns`, `text`, `stats`, `io`, `enrich`), checked against `pandas-stubs`. The supported `BaseData` classes in `bynat/cleaning/base.py` remain a separate gradual-typing boundary. To bring a module into scope, add it to `files`, fix what mypy reports with precise annotations rather than `Any` or `# type: ignore`, and keep the runtime behavior unchanged.

`benchmarks/` holds timing scripts that compare the current implementations with the `0.3.0` algorithms; see [`benchmarks/README.md`](benchmarks/README.md).

Building a wheel or source archive requires the configured setuptools build backend and wheel tooling.

## License

MIT. See [LICENSE](LICENSE).
