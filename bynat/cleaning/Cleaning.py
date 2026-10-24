"""Compatibility shim: `bynat.cleaning.Cleaning` is now `bynat.cleaning.base`."""

import sys

from bynat.cleaning import base as _base

# Alias the module so attribute access and monkeypatching reach the real code.
sys.modules[__name__] = _base
