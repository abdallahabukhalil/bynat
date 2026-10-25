"""The cached-lookup engine shared by every enrichment method."""

import threading
import time
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pandas as pd

from bynat.caching import CacheManager

from .._common import _cache_value_key, logger

# Returned by a fetch to leave a value uncached without logging a failure.
SKIP = object()

Fetch = Callable[[Any], Any]
FetchMany = Callable[[list], Sequence[Any]]


def _unique_values(values: Iterable) -> list:
    """Return the unique non-null values in first-seen order."""
    series = values if isinstance(values, pd.Series) else pd.Series(list(values), dtype = object)
    return list(series.dropna().unique())


def _rate_limited_map(
    func: Fetch,
    items: Sequence[Any],
    max_workers: int = 1,
    min_interval: float = 0.0,
) -> list:
    """
    Call `func` on every item and return results in input order, with an
    exception in place of each failed call.

    At most `max_workers` calls run at once, and call starts are spaced at
    least `min_interval` seconds apart across all workers.
    """
    lock = threading.Lock()
    next_start = time.monotonic()

    def call(item: Any) -> Any:
        nonlocal next_start

        if min_interval > 0:
            with lock:
                now = time.monotonic()
                wait = next_start - now
                next_start = max(now, next_start) + min_interval

            if wait > 0:
                time.sleep(wait)

        try:
            return func(item)
        except Exception as error:
            return error

    if max_workers <= 1 or len(items) <= 1:
        return [call(item) for item in items]

    with ThreadPoolExecutor(max_workers = min(max_workers, len(items))) as pool:
        return list(pool.map(call, items))


def _cached_map(
    series: Iterable,
    fetch: Fetch | None,
    bucket: str | Sequence[str],
    save_every: int,
    *,
    cache_manager: CacheManager,
    func_name: str,
    key: Callable[[Any], Hashable] = _cache_value_key,
    is_cached: Callable[[Any, Any], bool] | None = None,
    fetch_many: FetchMany | None = None,
    on_fetch_start: Callable[[int], Any] | None = None,
    action: str = "fetch",
    label: str = "values",
) -> Any:
    """
    Resolve the unique values of `series` through a persistent cache.

    Every enrichment method follows the same loop: take the unique non-null
    values, skip the cached ones, fetch the rest, checkpoint the cache every
    `save_every` values, and return the cache for the caller to map back.

    Args:
        series: Values to resolve. Nulls and duplicates are dropped.
        fetch: Resolves one value. Ignored when `fetch_many` is given.
        bucket: Cache bucket name. With a sequence of names, each fetch
            returns a mapping of bucket name to value, a value counts as
            cached only when every bucket has it, and the result is a dict of
            bucket name to cache.
        save_every: Number of values fetched between cache checkpoints. It
            is also the chunk size passed to `fetch_many`.
        cache_manager: Manager that owns the cache.
        func_name: Cache function name.
        key: Builds the cache key of a value. Defaults to the normalized
            string key used by every bynat cache.
        is_cached: `(cache, value) -> bool`. Defaults to checking `key(value)`
            in every bucket.
        fetch_many: Resolves one chunk of values and returns one outcome per
            value, in order.
        on_fetch_start: Called once with the number of values to fetch,
            before the first fetch and only when something must be fetched.
            Its exceptions propagate, so it is the place for loading optional
            dependencies or models.
        action: Verb used in failure warnings, e.g. "translate".
        label: Plural noun used in progress messages, e.g. "translations".

    A fetch outcome is stored unless it is `SKIP` (left uncached quietly) or
    an exception (logged as a warning and left uncached, so it is retried on
    the next run).

    Returns:
        dict: The cache bucket, or a dict of buckets for multiple names.
    """
    bucket_names = (bucket,) if isinstance(bucket, str) else tuple(bucket)
    caches = {name: cache_manager.get_column_cache(func_name, name) for name in bucket_names}
    view = caches[bucket_names[0]] if isinstance(bucket, str) else caches

    def in_every_bucket(_view: Any, value: Any) -> bool:
        value_key = key(value)
        return all(value_key in cache for cache in caches.values())

    cached = is_cached if is_cached is not None else in_every_bucket
    pending: dict[Hashable, Any] = {}

    for value in _unique_values(series):
        if not cached(view, value):
            pending.setdefault(key(value), value)

    to_fetch = list(pending.values())
    total = len(to_fetch)

    if not total:
        logger.info("All requested %s are already cached.", label)
        return view

    if on_fetch_start is not None:
        on_fetch_start(total)

    unsaved = False
    processed = 0

    for start in range(0, total, save_every):
        chunk = to_fetch[start:start + save_every]

        if fetch_many is not None:
            try:
                outcomes = list(fetch_many(chunk))

                if len(outcomes) != len(chunk):
                    raise ValueError(
                        f"expected {len(chunk)} results from a batch, got {len(outcomes)}"
                    )

            except Exception as error:
                outcomes = [error] * len(chunk)

        elif fetch is not None:
            outcomes = []

            for value in chunk:
                try:
                    outcomes.append(fetch(value))
                except Exception as error:
                    outcomes.append(error)

        else:
            raise TypeError("_cached_map needs fetch or fetch_many")

        for value, outcome in zip(chunk, outcomes):
            if isinstance(outcome, Exception):
                logger.warning(
                    "Failed to %s: '%s'. Skipping cache for this item. Error: %s",
                    action, value, outcome,
                )
                continue

            if outcome is SKIP:
                continue

            value_key = key(value)

            if isinstance(bucket, str):
                caches[bucket][value_key] = outcome

            else:
                for name in bucket_names:
                    caches[name][value_key] = outcome[name] if isinstance(outcome, Mapping) else outcome

            unsaved = True

        processed += len(chunk)

        if unsaved and processed < total:
            cache_manager.save_function_cache(func_name)
            unsaved = False
            logger.info("Checkpoint: Safely saved %s / %s %s to cache...", processed, total, label)

    if unsaved:
        cache_manager.save_function_cache(func_name)

    logger.info("All %s completed and safely cached!", label)

    return view
