"""
Data models shared across pkgmanager.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Package:
    """A single search result, installed-package, or upgradable-package entry."""
    name: str
    version: str
    description: str
    repository: str  # 'core', 'extra', 'community', 'multilib', 'aur', 'local', or 'unknown'
    installed: bool = False
    is_aur: bool = False
    new_version: Optional[str] = None  # set only by list_upgradable()


@dataclass
class PackageDetail:
    """Extended information for a single package, as returned by info()."""
    name: str
    version: str
    description: str
    repository: str
    installed_version: Optional[str] = None
    install_date: Optional[str] = None
    size: Optional[str] = None
    depends: List[str] = field(default_factory=list)
    optional_deps: List[str] = field(default_factory=list)
    license: List[str] = field(default_factory=list)
    url: Optional[str] = None
    maintainer: Optional[str] = None
    votes: Optional[int] = None
    popularity: Optional[float] = None
    out_of_date: Optional[bool] = None
    # AUR-only fields (maintainer, votes, popularity, out_of_date) are simply
    # left at their defaults for official-repo packages.
