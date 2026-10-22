import copy
import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from os import PathLike

__all__ = ['CacheManager', 'CACHE_DIR_ENV_VAR', 'DEFAULT_CACHE_DIR']

logger = logging.getLogger(__name__)

CACHE_DIR_ENV_VAR = "BYNAT_CACHE_DIR"
DEFAULT_CACHE_DIR = "Settings/Cache Data/Automated"
_SQLITE_DATABASE_NAME = "cache.sqlite3"
_SQLITE_TIMEOUT_SECONDS = 30.0


def resolve_cache_dir(cache_dir: str | PathLike | None) -> str | PathLike:
    """Return `cache_dir`, else `$BYNAT_CACHE_DIR`, else the legacy relative default."""
    if cache_dir is not None:
        return cache_dir

    return os.environ.get(CACHE_DIR_ENV_VAR) or DEFAULT_CACHE_DIR




class CacheManager:
    """
    Manage lightweight JSON cache files for function-level and column-level
    operations.

    Notes:
        Each function gets its own JSON file inside the configured cache
        directory. Individual methods can then store nested objects keyed by
        column name or another logical cache bucket.
    """

    def __init__(self, cache_dir: str | PathLike | None = None):
        """
        Initialize the cache manager and resolve its storage directory.

        Relative paths are resolved from the current working directory. Absolute
        paths are used as provided. User home markers (`~`) and environment
        variables are expanded before the path is normalized.

        Args:
            cache_dir (str | os.PathLike | None): Directory where JSON cache
                files are stored. When None, the `BYNAT_CACHE_DIR` environment
                variable is used if set, otherwise
                `"Settings/Cache Data/Automated"` relative to the working
                directory.

        Raises:
            TypeError: If `cache_dir` is not a string or path-like object.
            ValueError: If `cache_dir` is empty after normalization.
        """
        cache_dir = resolve_cache_dir(cache_dir)

        if not isinstance(cache_dir, (str, PathLike)):
            raise TypeError("cache_dir must be a string or path-like object")

        cache_dir = os.fspath(cache_dir)

        if not isinstance(cache_dir, str):
            raise TypeError("cache_dir must resolve to a string path")

        cache_dir = cache_dir.strip()

        if not cache_dir:
            raise ValueError("cache_dir cannot be empty")

        expanded_cache_dir = os.path.expandvars(os.path.expanduser(cache_dir))

        self.cache_dir_path = os.path.abspath(expanded_cache_dir)
        self.cache_dir = os.path.normpath(expanded_cache_dir)

        self.caches = {}
        self._baselines = {}

    def _get_database_path(self) -> str:
        """Return the SQLite database path for this cache directory."""
        return os.path.join(self.cache_dir_path, _SQLITE_DATABASE_NAME)

    def _connect_sqlite(self):
        """Open and configure a SQLite connection for cache infrastructure."""
        connection = sqlite3.connect(
            self._get_database_path(),
            timeout = _SQLITE_TIMEOUT_SECONDS,
        )

        try:
            connection.execute(f"PRAGMA busy_timeout = {int(_SQLITE_TIMEOUT_SECONDS * 1000)}")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except Exception:
            connection.close()
            raise

    def _initialize_sqlite(self) -> None:
        """Create the SQLite cache schema in one explicit transaction."""
        os.makedirs(self.cache_dir_path, exist_ok = True)
        connection = None

        try:
            connection = self._connect_sqlite()
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    func_name TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (func_name, bucket, key)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cache_entries_func_bucket
                ON cache_entries (func_name, bucket)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS migration_metadata (
                    source_name TEXT PRIMARY KEY,
                    migrated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
        except Exception:
            if connection is not None:
                try:
                    connection.rollback()
                finally:
                    connection.close()
            raise
        else:
            connection.close()


    def _quarantine_corrupt_cache_file(self, file_path: str) -> str | None:
        """Move a corrupt cache file aside without overwriting a quarantine."""
        quarantine_path = None

        for _ in range(10):
            candidate = f"{file_path}.corrupt.{time.time_ns()}.{uuid.uuid4().hex}"
            try:
                descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(descriptor)
                quarantine_path = candidate
                break
            except FileExistsError:
                continue

        if quarantine_path is None:
            return None

        try:
            os.replace(file_path, quarantine_path)
            return quarantine_path
        except FileNotFoundError:
            # Another process may have quarantined the source first.
            try:
                os.remove(quarantine_path)
            except OSError:
                pass
            return None
        except OSError:
            try:
                os.remove(quarantine_path)
            except OSError:
                pass
            return None

    def _migrate_legacy_json_cache(self, func_name: str) -> None:
        """Import one legacy JSON cache into SQLite, without changing the API."""
        func_name = self._validate_cache_key(func_name, "func_name")
        file_path = self._get_file_path(func_name)
        source_name = os.path.basename(file_path)

        if not os.path.exists(file_path):
            return

        try:
            with open(file_path, encoding = "utf-8") as cache:
                loaded_cache = json.load(cache)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            try:
                quarantine_path = self._quarantine_corrupt_cache_file(file_path)
                location = (
                    f"moved it to '{quarantine_path}'"
                    if quarantine_path is not None
                    else "another process may have moved it aside"
                )
            except OSError as quarantine_error:
                location = f"could not move it aside: {quarantine_error}"
            logger.warning(
                "Legacy cache file '%s' is corrupted; %s. Details: %s",
                file_path, location, error,
            )
            return
        except OSError as error:
            logger.warning("Could not read legacy cache file '%s'. Details: %s", file_path, error)
            return

        if not isinstance(loaded_cache, dict):
            try:
                quarantine_path = self._quarantine_corrupt_cache_file(file_path)
                location = (
                    f"moved it to '{quarantine_path}'"
                    if quarantine_path is not None
                    else "another process may have moved it aside"
                )
            except OSError as quarantine_error:
                location = f"could not move it aside: {quarantine_error}"
            logger.warning(
                "Legacy cache file '%s' has an invalid top-level JSON value; %s.",
                file_path, location,
            )
            return

        for bucket, entries in loaded_cache.items():
            if not isinstance(entries, dict):
                logger.warning(
                    "Legacy cache file '%s' is unsupported: bucket '%s' is not a JSON object; "
                    "source preserved for recovery.",
                    file_path, bucket,
                )
                return

        prepared_entries = []
        created_at = datetime.now(UTC).isoformat()
        try:
            for bucket, entries in loaded_cache.items():
                for key, value in entries.items():
                    prepared_entries.append(
                        (func_name, bucket, key, json.dumps(value, ensure_ascii = False), created_at)
                    )
        except (TypeError, ValueError) as error:
            logger.warning(
                "Legacy cache file '%s' contains an unsupported value; source preserved for recovery. "
                "Details: %s",
                file_path, error,
            )
            return

        connection = None
        try:
            for attempt in range(6):
                try:
                    self._initialize_sqlite()
                    break
                except sqlite3.OperationalError as error:
                    if "locked" not in str(error).lower() or attempt == 5:
                        raise
                    time.sleep(0.05 * (attempt + 1))
            connection = self._connect_sqlite()
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM migration_metadata WHERE source_name = ?",
                (source_name,),
            ).fetchone() is not None:
                connection.rollback()
                return

            connection.executemany(
                """
                INSERT OR IGNORE INTO cache_entries
                (func_name, bucket, key, value_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                prepared_entries,
            )
            connection.execute(
                "INSERT INTO migration_metadata (source_name, migrated_at) VALUES (?, ?)",
                (source_name, datetime.now(UTC).isoformat()),
            )
            connection.commit()
        except Exception:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            raise
        finally:
            if connection is not None:
                connection.close()

    def _migrate_legacy_cache(self, func_name: str) -> None:
        """Compatibility name for the reusable legacy migration helper."""
        self._migrate_legacy_json_cache(func_name)


    def _rebuild_function_cache_from_sqlite(self, func_name: str) -> dict:
        """
        Rebuild the nested cache for one function from its SQLite rows.

        Reads every ``cache_entries`` row for ``func_name`` and decodes each
        ``value_json`` blob back into a Python value with ``json.loads``, so
        explicit ``None`` and nested lists/dicts round-trip faithfully. The
        result mirrors the legacy JSON shape: ``{bucket: {key: decoded_value}}``.

        Args:
            func_name (str): Function identifier whose rows should be rebuilt.

        Returns:
            dict: Nested ``{bucket: {key: value}}`` mapping for the function.
            An empty dictionary is returned when the function has no rows.
        """
        func_name = self._validate_cache_key(func_name, "func_name")
        reconstructed: dict = {}

        connection = self._connect_sqlite()

        try:
            rows = connection.execute(
                "SELECT bucket, key, value_json FROM cache_entries "
                "WHERE func_name = ? ORDER BY bucket, key",
                (func_name,),
            ).fetchall()
        finally:
            connection.close()

        for bucket, key, value_json in rows:
            reconstructed.setdefault(bucket, {})[key] = json.loads(value_json)

        return reconstructed


    def _validate_cache_key(self, key: str, key_name: str) -> str:
        """
        Validate a cache key before using it in file paths or dictionaries.

        Args:
            key (str): Cache key value to validate.
            key_name (str): Human-readable argument name for error messages.

        Returns:
            str: Normalized cache key.

        Raises:
            TypeError: If `key` is not a string.
            ValueError: If `key` is empty or contains path separators.
        """
        if not isinstance(key, str):
            raise TypeError(f"{key_name} must be a string")

        key = key.strip()

        if not key:
            raise ValueError(f"{key_name} cannot be empty")

        if os.path.basename(key) != key or os.path.sep in key or (os.path.altsep and os.path.altsep in key):
            raise ValueError(f"{key_name} cannot contain path separators")

        return key


    def _get_file_path(self, func_name: str) -> str:
        """
        Build the cache-file path for a specific function name.

        Args:
            func_name (str): The logical function identifier used as the cache
                filename prefix.

        Returns:
            str: Absolute path to the JSON cache file for the requested
            function.

        Raises:
            TypeError: If `func_name` is not a string.
            ValueError: If `func_name` is empty or contains path separators.
        """
        func_name = self._validate_cache_key(func_name, "func_name")

        return os.path.join(self.cache_dir_path, f"{func_name}_cache.json")


    def load_function_cache(self, func_name: str):
        """
        Load a function cache from SQLite into memory on first access.

        The SQLite backend is created on first public access for any function.
        Before reading, any legacy JSON cache for the function is migrated into
        SQLite through the accepted migration helper, then the function's cache
        is rebuilt from its SQLite rows via
        ``_rebuild_function_cache_from_sqlite``. On first load the reconstructed
        state is deep-copied into a private per-function baseline
        (``_baselines[func_name]``) so the snapshot never shares nested mutable
        references with the live cache. The returned dict is memoized, so
        repeated lookups and in-place mutations stay consistent for the lifetime
        of the manager.

        Args:
            func_name (str): Function identifier whose cache should be loaded.

        Returns:
            dict: The in-memory cache object for the requested function, backed
            by SQLite. A function with no cached entries returns an empty
            dictionary.

        Raises:
            TypeError: If `func_name` is not a string.
            ValueError: If `func_name` is empty or contains path separators.
        """
        func_name = self._validate_cache_key(func_name, "func_name")

        if func_name not in self.caches:
            self._initialize_sqlite()
            self._migrate_legacy_json_cache(func_name)
            self.caches[func_name] = self._rebuild_function_cache_from_sqlite(func_name)
            self._baselines[func_name] = copy.deepcopy(self.caches[func_name])

        return self.caches[func_name]


    def get_column_cache(self, func_name: str, col_name: str):
        """
        Return a nested cache bucket for a specific function and column.

        Args:
            func_name (str): Parent function cache name.
            col_name (str): Nested cache key, commonly a column name.

        Returns:
            dict: The cache dictionary stored under the requested nested key.
            The dictionary is created automatically if it does not yet exist.
        """
        col_name = self._validate_cache_key(col_name, "col_name")
        func_cache = self.load_function_cache(func_name)

        if col_name not in func_cache or not isinstance(func_cache[col_name], dict):
            func_cache[col_name] = {}

        return func_cache[col_name]


    def save_function_cache(self, func_name: str):
        """
        Persist newly added, changed, or removed entries of a function cache to SQLite.

        The live in-memory cache is diffed against the deep baseline snapshot
        captured at load time (``_baselines[func_name]``). Entries that were
        newly added or whose value changed are written, and every baseline
        ``(bucket, key)`` identity no longer present in the live cache — including
        the keys of an entirely removed bucket — is deleted. This detects nested
        JSON-compatible mutations and explicit ``None`` because each changed
        value is compared as a whole and then re-serialized. Serialization
        happens first; additions, changes, and removals are then applied
        together, in one explicit ``BEGIN IMMEDIATE`` transaction: added and
        changed rows use ``INSERT OR REPLACE`` conflict semantics so that writes
        to the same key resolve to the last committed writer while disjoint keys
        merge, and each removed identity is deleted with an exact
        ``(func_name, bucket, key)`` ``DELETE`` so no broad bucket delete is ever
        issued.

        The deep baseline is advanced to a copy of the live cache only after the
        transaction commits. Serialization, locking, transaction, or commit
        failures are logged and swallowed: the transaction is rolled back, the
        baseline is left unchanged, and the pending in-memory changes are
        preserved so the next save re-diffs against the same baseline and retries.

        Args:
            func_name (str): Function identifier whose cache should be written.

        Returns:
            None
        """
        func_name = self._validate_cache_key(func_name, "func_name")

        if func_name not in self.caches:
            self.caches[func_name] = {}

        live_cache = self.caches[func_name]
        baseline = self._baselines.get(func_name)

        if not isinstance(baseline, dict):
            baseline = {}

        prepared_entries = []
        removal_identities = []
        created_at = datetime.now(UTC).isoformat()

        try:
            for bucket, entries in live_cache.items():
                if not isinstance(entries, dict):
                    continue

                baseline_bucket = baseline.get(bucket)

                if not isinstance(baseline_bucket, dict):
                    baseline_bucket = {}

                for key, value in entries.items():
                    if key in baseline_bucket and baseline_bucket[key] == value:
                        continue

                    prepared_entries.append(
                        (
                            func_name,
                            bucket,
                            key,
                            json.dumps(value, ensure_ascii = False),
                            created_at,
                        )
                    )

            for bucket, baseline_entries in baseline.items():
                if not isinstance(baseline_entries, dict):
                    continue

                live_bucket = live_cache.get(bucket)

                if isinstance(live_bucket, dict):
                    removed_keys = [key for key in baseline_entries if key not in live_bucket]
                else:
                    removed_keys = list(baseline_entries)

                for key in removed_keys:
                    removal_identities.append((func_name, bucket, key))
        except (TypeError, ValueError) as error:
            logger.error("Could not save cache for '%s'. Details: %s", func_name, error)
            return

        if not prepared_entries and not removal_identities:
            return

        connection = None

        try:
            self._initialize_sqlite()
            connection = self._connect_sqlite()
            connection.execute("BEGIN IMMEDIATE")
            if prepared_entries:
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO cache_entries
                    (func_name, bucket, key, value_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    prepared_entries,
                )
            if removal_identities:
                connection.executemany(
                    "DELETE FROM cache_entries WHERE func_name = ? AND bucket = ? AND key = ?",
                    removal_identities,
                )
            connection.commit()
        except (sqlite3.Error, OSError) as error:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            logger.error("Could not save cache for '%s'. Details: %s", func_name, error)
        else:
            self._baselines[func_name] = copy.deepcopy(live_cache)
        finally:
            if connection is not None:
                connection.close()


    def delete_column_cache(self, func_name: str, col_name: str):
        """
        Remove a nested cache bucket from a function cache and save the result.

        Args:
            func_name (str): Parent function cache name.
            col_name (str): Nested cache key to remove.

        Returns:
            None
        """
        col_name = self._validate_cache_key(col_name, "col_name")
        func_cache = self.load_function_cache(func_name)

        if col_name in func_cache:
            del func_cache[col_name]
            self.save_function_cache(func_name)

            logger.info("Cache for column '%s' deleted from '%s_cache.json'", col_name, func_name)

        else:
            logger.info("No cache found for column '%s' in '%s_cache.json'", col_name, func_name)
