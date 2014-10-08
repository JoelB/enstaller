import logging

import egginst._compat


logging.getLogger("enstaller").addHandler(egginst._compat.NullHandler())

try:
    from enstaller._version import (full_version as __version__,
                                    is_released as __is_released__)
except ImportError as e: # pragma: no cover
    __version__ = "no-built"
    __is_released__ = False
