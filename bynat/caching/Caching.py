import os
import json
from os import PathLike


__all__ = ['CacheManager']




class CacheManager:
    """
    Manage lightweight JSON cache files for function-level and column-level
    operations.

    Notes:
        Each function gets its own JSON file inside the configured cache
        directory. Individual methods can then store nested objects keyed by
        column name or another logical cache bucket.
    """

    def __init__(self, cache_dir: str | PathLike = "Settings/Cache Data/Automated"):
        """
        Initialize the cache manager and resolve its storage directory.

        Relative paths are resolved from the current working directory. Absolute
        paths are used as provided. User home markers (`~`) and environment
        variables are expanded before the path is normalized.

        Args:
            cache_dir (str | os.PathLike): Directory where JSON cache files are
                stored. Defaults to `"Settings/Cache Data/Automated"`.

        Raises:
            TypeError: If `cache_dir` is not a string or path-like object.
            ValueError: If `cache_dir` is empty after normalization.
        """
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
                    with open(file_path, 'r', encoding = 'utf-8') as cache:
                        loaded_cache = json.load(cache)

                    self.caches[func_name] = loaded_cache if isinstance(loaded_cache, dict) else {}

                except (json.JSONDecodeError, OSError) as e:
                    print(f"Error: Cache file '{file_path}' is corrupted or unreadable. Starting with an empty cache.\n Details: {e}")
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
        
        try:
            with open(file_path, 'w', encoding = 'utf-8') as f:
                json.dump(self.caches[func_name], f, ensure_ascii = False, indent = 4)

        except (TypeError, OSError) as e:
            print(f"Error: Could not save cache file '{file_path}'.\n Details: {e}")


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
            
            print(f"Cache for column '{col_name}' deleted from '{func_name}_cache.json'")
        
        else:
            print(f"No cache found for column '{col_name}' in '{func_name}_cache.json'")
