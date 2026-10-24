"""Cached geocoding of missing coordinates."""

from typing import Any

from bynat.caching import CacheManager

from .._common import FrameT, _require_positive_int, logger
from .engine import _cached_map
from .providers import Geocoder, NominatimGeocoder


def fill_missing_coordinates(
    df: FrameT,
    *fallback_cols: str,
    lat_col: str,
    long_col: str,
    save_every: int = 1000,
    user_agent: str = "data_cleaning_core_geocoder",
    retry_not_found: bool = False,
    provider: Geocoder | None = None,
    cache_manager: CacheManager,
) -> FrameT:
    """
    Fill missing latitude and longitude values by geocoding fallback location
    text.

    Notes:
        The method builds a location query from the supplied fallback
        columns, caches geocoding results, and progressively drops the
        leftmost (most specific) part of a failed query, e.g.
        `"street, city, country"` -> `"city, country"` -> `"country"`.
        Existing valid coordinates are preserved and only missing
        coordinate fields are updated.

        Queries that cannot be resolved are cached as `null`, so later runs
        do not re-query them. Network or service errors are not cached.

    Args:
        *fallback_cols: Ordered columns used to build the geocoding query.
        lat_col (str): Latitude column name.
        long_col (str): Longitude column name.
        save_every (int): Cache checkpoint frequency while geocoding.
            Defaults to 1000.
        user_agent (str): User agent sent to Nominatim by the default
            provider. Its usage policy asks for one that identifies your
            application. Defaults to `"data_cleaning_core_geocoder"`.
        retry_not_found (bool): If True, re-query locations previously
            cached as not found. Defaults to False.
        provider (Geocoder | None): Geocoding provider. Defaults to
            Nominatim through `geopy`, rate-limited to one request per
            second.

    Returns:
        A new dataframe with completed coordinates.

    Raises:
        ValueError: If `save_every` is not a positive integer.
    """

    _require_positive_int(save_every, "save_every")

    required_cols = [lat_col, long_col]
    if not all(col in df.columns for col in required_cols):
        logger.warning("Missing coordinate columns. Skipping geocoding.")
        return df.copy()

    result = df.copy()

    valid_fallbacks = [col for col in fallback_cols if col in result.columns]
    if not valid_fallbacks:
        logger.warning("No valid fallback columns found. Skipping geocoding.")
        return result

    mask_missing = result[lat_col].isna() | result[long_col].isna()
    temp_query_col = "__temp_geocode_query__"

    result[temp_query_col] = None
    result.loc[mask_missing, temp_query_col] = ""

    for col in valid_fallbacks:
        col_str = result[col].astype(str).str.strip()

        col_lower = col_str.str.lower()
        mask_valid_col = (
            result[col].notna() &
            (col_str != "") &
            (col_lower != "nan") &
            (col_lower != "none") &
            (~col_str.str.isnumeric())
        )

        update_mask = mask_missing & mask_valid_col

        empty_mask = update_mask & (result[temp_query_col] == "")
        result.loc[empty_mask, temp_query_col] = col_str[empty_mask]

        append_mask = update_mask & (result[temp_query_col] != "") & (~empty_mask)
        result.loc[append_mask, temp_query_col] += ", " + col_str[append_mask]

    result.loc[result[temp_query_col] == "", temp_query_col] = None

    queries = result.loc[mask_missing, temp_query_col].dropna()

    if queries.empty:
        return result.drop(columns = [temp_query_col])

    active: dict[str, Any] = {}

    def start(count: int) -> None:
        logger.info("preparing %s locations to be geocoded...", count)
        active["provider"] = provider if provider is not None else NominatimGeocoder(user_agent = user_agent)

    def is_cached(cache: dict, query: str) -> bool:
        return query in cache and not (retry_not_found and cache[query] is None)

    def geocode(query: str) -> tuple[float, float] | None:
        current_search = query

        while current_search:
            coordinates = active["provider"].geocode(current_search)

            if coordinates:
                return (coordinates[0], coordinates[1])

            if ',' not in current_search:
                break

            current_search = current_search.split(',', 1)[1].strip()

        logger.info("Location not found for '%s'. Cached as not found.", query)
        return None

    current_cache = _cached_map(
        queries,
        geocode,
        "geocode_queries",
        save_every,
        cache_manager = cache_manager,
        func_name = "fill_missing_coordinates",
        key = str,
        is_cached = is_cached,
        on_fetch_start = start,
        action = "geocode",
        label = "locations",
    )

    mapped_coords = result[temp_query_col].map(current_cache)

    valid_mapped = mapped_coords.notna()
    lat_update_mask = result[lat_col].isna() & valid_mapped
    long_update_mask = result[long_col].isna() & valid_mapped

    if lat_update_mask.any():
        result.loc[lat_update_mask, lat_col] = mapped_coords[lat_update_mask].str[0]

    if long_update_mask.any():
        result.loc[long_update_mask, long_col] = mapped_coords[long_update_mask].str[1]

    return result.drop(columns = [temp_query_col])
