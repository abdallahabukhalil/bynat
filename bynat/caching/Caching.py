"""Compatibility shim: `bynat.caching.Caching` is now `bynat.caching.manager`."""

import sys

from bynat.caching import manager as _manager

# Alias the module so attribute access and monkeypatching reach the real code.
sys.modules[__name__] = _manager
