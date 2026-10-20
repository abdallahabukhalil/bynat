import json
import logging
import os
import tempfile
import time
from os import PathLike

__all__ = ['CacheManager', 'CACHE_DIR_ENV_VAR', 'DEFAULT_CACHE_DIR']

logger = logging.getLogger(__name__)

CACHE_DIR_ENV_VAR = "BYNAT_CACHE_DIR"
DEFAULT_CACHE_DIR = "Settings/Cache Data/Automated"


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
        Load a function cache from disk into memory on first access.

        Args:
            func_name (str): Function identifier whose cache file should be
                loaded.

        Returns:
            dict: The in-memory cache object for the requested function. If no
            cache file exists yet, an empty dictionary is created and returned.

        Raises:
            TypeError: If `func_name` is not a string.
            ValueError: If `func_name` is empty or contains path separators.
        """
        func_name = self._validate_cache_key(func_name, "func_name")

        if func_name not in self.caches:
            file_path = self._get_file_path(func_name)

            if os.path.exists(file_path):
                try:
                    with open(file_path, encoding = 'utf-8') as cache:
                        loaded_cache = json.load(cache)

                    if not isinstance(loaded_cache, dict):
                        raise ValueError("top-level JSON value is not an object")

                    self.caches[func_name] = loaded_cache

                except ValueError as e:
                    # JSONDecodeError and UnicodeDecodeError are ValueErrors. Keep the bad file for manual
                    # recovery instead of letting the next save overwrite it.
                    quarantine_path = f"{file_path}.corrupt.{int(time.time())}"

                    try:
                        os.replace(file_path, quarantine_path)
                        location = f"moved it to '{quarantine_path}'"

                    except OSError:
                        location = "could not move it aside"

                    logger.warning(
                        "Cache file '%s' is corrupted; %s and started an empty cache. Details: %s",
                        file_path, location, e
                    )
                    self.caches[func_name] = {}

                except OSError as e:
                    logger.warning(
                        "Cache file '%s' is unreadable; starting with an empty in-memory cache. Details: %s",
                        file_path, e
                    )
                    self.caches[func_name] = {}

            else:
                self.caches[func_name] = {}

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
        Persist the in-memory cache for a function to disk as JSON.

        The JSON is written to a temporary file in the cache directory and then
        moved over the target with `os.replace`, so an interrupted or failed
        save leaves the previous cache file intact instead of truncated.

        Args:
            func_name (str): Function identifier whose cache should be written.

        Returns:
            None
        """
        func_name = self._validate_cache_key(func_name, "func_name")

        if func_name not in self.caches:
            self.caches[func_name] = {}

        os.makedirs(self.cache_dir_path, exist_ok = True)

        file_path = self._get_file_path(func_name)

        temp_path = None

        try:
            with tempfile.NamedTemporaryFile(
                'w',
                encoding = 'utf-8',
                dir = self.cache_dir_path,
                prefix = f".{func_name}_cache.",
                suffix = ".tmp",
                delete = False
            ) as f:
                temp_path = f.name
                json.dump(self.caches[func_name], f, ensure_ascii = False, indent = 4)
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_path, file_path)
            temp_path = None

        except (TypeError, ValueError, OSError) as e:
            logger.error("Could not save cache file '%s'. Details: %s", file_path, e)

        finally:
            if temp_path is not None and os.path.exists(temp_path):
                os.remove(temp_path)


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
