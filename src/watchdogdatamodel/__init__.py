"""watchdogdatamodel — generalized data model for timeseries quality ops."""
from . import models, store
from .evidence import excerpt
from .fingerprint import compute_fingerprint
from .scope import scope_covers, validate_scope
from .wire import dump_timeseries, load_timeseries

__version__ = "0.1.1"

__all__ = [
    "models", "store", "excerpt", "compute_fingerprint",
    "scope_covers", "validate_scope", "dump_timeseries", "load_timeseries",
]
