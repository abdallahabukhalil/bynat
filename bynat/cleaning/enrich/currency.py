"""Cached conversion of local-currency amounts to USD."""

import time
from collections import defaultdict
from typing import Any

import pandas as pd

from bynat.caching import CacheManager

from .._common import FrameT, _import_optional, _left_merge_aligned, logger
from .engine import _cached_map
from .providers import RateSource, YFinanceRateSource


def _lookback_years(year: int, min_year: int) -> range:
    """Years checked for a rate, from `year` back to `min_year`."""
    return range(year, min_year - 1, -1)


def convert_to_usd(
    df: FrameT,
    *target_cols: Any,
    country_col: str,
    year_col: str,
    new_cols: bool = True,
    round_decimals: int | None = None,
    provider: RateSource | None = None,
    cache_manager: CacheManager,
) -> FrameT:
    """
    Convert numeric salary-like columns into USD using country currency codes
    and year-based exchange rates.

    Notes:
        Currency codes and exchange rates are cached to reduce repeated
        lookups. For non-USD currencies, the yearly mean rate of the
        requested year is used, falling back to earlier years (down to the
        earliest year in the data) when a year has no data. Uncached years
        are downloaded once per currency across the whole range needed.
        Unknown countries and past years without data are cached as misses;
        network errors are not cached.

    Args:
        *target_cols: Numeric columns to convert.
        country_col (str): Column containing the country name used to infer
            the local currency.
        year_col (str): Column containing the reference year for historical
            exchange rates.
        new_cols (bool): If True, create new `<column>_usd` columns.
            Otherwise overwrite the original columns. Defaults to True.
        round_decimals (int | None): Optional number of decimal places to
            round the converted values to. Defaults to None.
        provider (RateSource | None): Exchange-rate provider. Defaults to
            Yahoo Finance through `yfinance`.

    Returns:
        A new dataframe with converted values.
    """
    required_cols = [country_col, year_col]
    if not all(col in df.columns for col in required_cols):
        logger.warning("Missing required columns: %s. Skipping conversion.", required_cols)

        return df.copy()

    result = df.copy()

    valid_cols = [col for col in target_cols if col in result.columns]
    if not valid_cols:
        logger.warning("No valid target columns found.")

        return result

    if result.empty:
        return result

    pycountry = _import_optional("pycountry", "pycountry", "convert_to_usd")
    babel_numbers = _import_optional(
        "babel.numbers", "Babel", "convert_to_usd"
    )
    rate_source = provider if provider is not None else YFinanceRateSource()

    func_name = "convert_to_usd"

    unique_pairs = result[[country_col, year_col]].drop_duplicates().dropna()

    valid_years = pd.to_numeric(result[year_col], errors = 'coerce').dropna()
    if valid_years.empty or unique_pairs.empty:
        return result

    min_year_limit = int(valid_years.min())
    current_year = time.localtime().tm_year

    def find_currency(country_name: str) -> str | None:
        try:
            country = pycountry.countries.search_fuzzy(country_name)[0]

        except LookupError:
            # pycountry found no matching country; remember the miss.
            return None

        currencies = babel_numbers.get_territory_currencies(country.alpha_2)

        return currencies[0] if currencies else None

    currency_cache = _cached_map(
        [str(country).strip() for country in unique_pairs[country_col]],
        find_currency,
        "currency_codes",
        max(len(unique_pairs), 1),
        cache_manager = cache_manager,
        func_name = func_name,
        key = str,
        is_cached = lambda cache, country_name: country_name in cache,
        action = "find the currency of",
        label = "currency codes",
    )

    rates_cache = cache_manager.get_column_cache(func_name, "exchange_rates")

    pair_years: list[tuple[Any, Any, str | None, int | None]] = []
    missing_years: dict[str, set[int]] = defaultdict(set)

    for orig_c, orig_y in zip(unique_pairs[country_col], unique_pairs[year_col]):
        curr_code = currency_cache.get(str(orig_c).strip())
        year = None

        if curr_code and curr_code.upper() != 'USD':
            try:
                year = int(float(orig_y))
            except (TypeError, ValueError):
                year = None

            if year is not None:
                for lookback_year in _lookback_years(year, min_year_limit):
                    rate_key = f"{curr_code}_{lookback_year}"

                    if rates_cache.get(rate_key):
                        break

                    if rate_key not in rates_cache:
                        missing_years[curr_code].add(lookback_year)

        pair_years.append((orig_c, orig_y, curr_code, year))

    cache_updated = False

    for curr_code, years in missing_years.items():
        start_year, end_year = min(years), max(years)

        try:
            logger.info("Fetching rates for %s from %s to %s...", curr_code, start_year, end_year)
            yearly_rates = rate_source.yearly_rates(curr_code, start_year, end_year)

        except Exception as e:
            # Network or service errors are transient; do not cache them.
            logger.warning("Failed to fetch rates for %s from %s to %s: %s", curr_code, start_year, end_year, e)
            continue

        for year in range(start_year, end_year + 1):
            rate_key = f"{curr_code}_{year}"

            if rate_key in rates_cache and rates_cache[rate_key]:
                continue

            rate = yearly_rates.get(year)

            if rate:
                rates_cache[rate_key] = float(rate)
                cache_updated = True

            elif year < current_year and rate_key not in rates_cache:
                # Past years will not gain data later, so cache the miss.
                rates_cache[rate_key] = None
                cache_updated = True

    if cache_updated:
        cache_manager.save_function_cache(func_name)

    rates_mapping = {}

    for orig_c, orig_y, curr_code, year in pair_years:
        rate = None

        if curr_code and curr_code.upper() == 'USD':
            rate = 1.0

        elif curr_code and year is not None:
            for lookback_year in _lookback_years(year, min_year_limit):
                rate = rates_cache.get(f"{curr_code}_{lookback_year}")

                if rate:
                    break

        rates_mapping[(orig_c, orig_y)] = rate

    rates_df = pd.Series(rates_mapping).reset_index()
    if not rates_df.empty:
        rates_df.columns = [country_col, year_col, '_conversion_rate']

        temp_df = _left_merge_aligned(result, rates_df, [country_col, year_col])

        for col in valid_cols:
            col_name = f"{col}_usd" if new_cols else col
            result[col_name] = result[col] / temp_df['_conversion_rate']

            if round_decimals is not None:
                result[col_name] = result[col_name].round(round_decimals)

    return result
