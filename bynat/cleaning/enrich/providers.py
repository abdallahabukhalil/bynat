"""Provider protocols for external services, and the default implementations."""

import threading
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

import pandas as pd

from .._common import _import_optional


@runtime_checkable
class Translator(Protocol):
    """
    Translate one text into `target_lang`.

    A provider may also define `translate_batch(texts, target_lang)`
    returning one translation per text; it is then used instead of calling
    `translate` per text. Without it, `translate` runs in a thread pool sized
    by the optional `max_workers` attribute (default 1), with call starts
    spaced by the optional `min_interval` attribute in seconds (default 0).
    Return a falsy value to leave a text untranslated and uncached.
    """

    def translate(self, text: str, target_lang: str) -> str | None: ...


@runtime_checkable
class Geocoder(Protocol):
    """Resolve a location query to `(latitude, longitude)`, or None when not found."""

    def geocode(self, query: str) -> tuple[float, float] | None: ...


@runtime_checkable
class RateSource(Protocol):
    """
    Return the mean exchange rate of one USD in `currency` for each year
    between `start_year` and `end_year` inclusive. Years without data are
    left out.
    """

    def yearly_rates(self, currency: str, start_year: int, end_year: int) -> Mapping[int, float]: ...


class GoogleTranslatorProvider:
    """
    Default `Translator`, backed by `deep_translator.GoogleTranslator`.

    `GoogleTranslator` keeps per-request state on the instance, so each
    worker thread gets its own translator. Up to four requests run at once,
    started at most five per second, the same request rate as the previous
    sequential loop with a 0.2-second pause.
    """

    max_workers = 4
    min_interval = 0.2

    def __init__(self, feature: str = "translation") -> None:
        self._module = _import_optional("deep_translator", "deep-translator", feature)
        self._local = threading.local()

    def translate(self, text: str, target_lang: str) -> str | None:
        translators = self._local.__dict__.setdefault("translators", {})
        translator = translators.get(target_lang)

        if translator is None:
            translator = self._module.GoogleTranslator(source = 'auto', target = target_lang)
            translators[target_lang] = translator

        return translator.translate(text)


class NominatimGeocoder:
    """Default `Geocoder`, backed by geopy's Nominatim with a rate limiter."""

    def __init__(
        self,
        user_agent: str = "data_cleaning_core_geocoder",
        min_delay_seconds: float = 1,
        feature: str = "fill_missing_coordinates",
    ) -> None:
        geocoders = _import_optional("geopy.geocoders", "geopy", feature)
        rate_limiter = _import_optional("geopy.extra.rate_limiter", "geopy", feature)

        geolocator = geocoders.Nominatim(user_agent = user_agent)
        self._geocode = rate_limiter.RateLimiter(geolocator.geocode, min_delay_seconds = min_delay_seconds)

    def geocode(self, query: str) -> tuple[float, float] | None:
        location = self._geocode(query)

        if not location:
            return None

        return (location.latitude, location.longitude)


class YFinanceRateSource:
    """
    Default `RateSource`, backed by Yahoo Finance history through `yfinance`.

    One `Ticker(...).history` download covers the whole year range, and the
    yearly means are computed from its daily closes.
    """

    def __init__(self, feature: str = "convert_to_usd") -> None:
        self._yfinance = _import_optional("yfinance", "yfinance", feature)

    def yearly_rates(self, currency: str, start_year: int, end_year: int) -> dict[int, float]:
        ticker = self._yfinance.Ticker(f"{currency}=X")
        history = ticker.history(start = f"{start_year}-01-01", end = f"{end_year}-12-31")

        if history is None or history.empty:
            return {}

        closes = history['Close']
        years = pd.DatetimeIndex(closes.index).year
        means = closes.groupby(years).mean()

        return {
            int(year): float(rate)
            for year, rate in means.items()
            if pd.notna(rate) and start_year <= int(year) <= end_year
        }
