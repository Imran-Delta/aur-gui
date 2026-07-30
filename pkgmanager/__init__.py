"""
pkgmanager: unified pacman/AUR-helper backend library.

Public surface re-exported here for `from pkgmanager import X` convenience;
everything is also importable from its actual submodule
(`pkgmanager.backend`, `pkgmanager.exceptions`, ...), which is what the
test suite uses throughout.
"""

from .backend import PackageManager
from .exceptions import (
    AURHelperMissingError,
    CommandFailedError,
    HelperNotFoundError,
    NoHelperError,
    PackageManagerError,
    PermissionDeniedError,
)

__all__ = [
    "PackageManager",
    "PackageManagerError",
    "AURHelperMissingError",
    "CommandFailedError",
    "HelperNotFoundError",
    "NoHelperError",
    "PermissionDeniedError",
]
