"""
pkgmanager: unified Python interface for pacman + any installed AUR helper
(yay, paru, trizen, pikaur), falling back to plain pacman when none of them
is installed.
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
from .models import Package, PackageDetail

__all__ = [
    'PackageManager',
    'Package',
    'PackageDetail',
    'PackageManagerError',
    'HelperNotFoundError',
    'NoHelperError',
    'AURHelperMissingError',
    'CommandFailedError',
    'PermissionDeniedError',
]

__version__ = '0.1.0'
