"""
Helper detection, command templates, and output parsers for pkgmanager.
"""

import re
import shutil
from typing import List, Optional

from .exceptions import HelperNotFoundError, NoHelperError
from .models import Package, PackageDetail

# Priority order for auto-detection. 'pacaur' is intentionally excluded: it's
# unmaintained upstream, so it won't be auto-detected, though a caller may
# still force it explicitly via PackageManager(force_helper=...) provided a
# command mapping exists for it and the binary is actually installed.
HELPER_PRIORITY: List[str] = ['yay', 'paru', 'trizen', 'pikaur']

HELPER_COMMANDS = {
    'yay': {
        'search': ['yay', '-Ss', '{query}'],
        'install': ['yay', '-S', '--needed', '--noconfirm', '{packages}'],
        'remove': ['yay', '-Rns', '--noconfirm', '{packages}'],
        'update': ['yay', '-Syu', '--noconfirm'],
        'info': ['yay', '-Qi', '{package}'],
        'info_remote': ['yay', '-Si', '{package}'],
        'list_installed': ['yay', '-Q'],
        'download_pkgbuild': ['yay', '-G', '{package}'],
        'refresh': ['yay', '-Sy', '--noconfirm'],
        'list_upgradable': ['yay', '-Qu'],
        'list_repo': ['pacman', '-Sl', '{repo}'],
        'supports_aur': True,
        'uses_pacman_flags': True,
        'needs_confirm_override': True,
    },
    'paru': {
        'search': ['paru', '-Ss', '{query}'],
        'install': ['paru', '-S', '--needed', '--noconfirm', '{packages}'],
        'remove': ['paru', '-Rns', '--noconfirm', '{packages}'],
        'update': ['paru', '-Syu', '--noconfirm'],
        'info': ['paru', '-Qi', '{package}'],
        'info_remote': ['paru', '-Si', '{package}'],
        'list_installed': ['paru', '-Q'],
        'download_pkgbuild': ['paru', '-G', '{package}'],
        'refresh': ['paru', '-Sy', '--noconfirm'],
        'list_upgradable': ['paru', '-Qu'],
        'list_repo': ['pacman', '-Sl', '{repo}'],
        'supports_aur': True,
        'uses_pacman_flags': True,
    },
    'trizen': {
        'search': ['trizen', '-Ss', '{query}'],
        'install': ['trizen', '-S', '--needed', '--noconfirm', '{packages}'],
        'remove': ['trizen', '-Rs', '--noconfirm', '{packages}'],
        'update': ['trizen', '-Syua', '--noconfirm'],
        'info': ['trizen', '-Qi', '{package}'],
        'info_remote': ['trizen', '-Si', '{package}'],
        'list_installed': ['trizen', '-Qe'],
        'download_pkgbuild': None,
        'refresh': ['trizen', '-Sy', '--noconfirm'],
        'list_upgradable': ['trizen', '-Qu'],
        'list_repo': ['pacman', '-Sl', '{repo}'],
        'supports_aur': True,
        'uses_pacman_flags': True,
    },
    'pikaur': {
        'search': ['pikaur', '-Ss', '{query}'],
        'install': ['pikaur', '-S', '--needed', '--noconfirm', '{packages}'],
        'remove': ['pikaur', '-Rns', '--noconfirm', '{packages}'],
        'update': [
            ['pikaur', '-Sy', '--noconfirm'],
            ['pikaur', '-Su', '--noconfirm'],
        ],
        'info': ['pikaur', '-Qi', '{package}'],
        'info_remote': ['pikaur', '-Si', '{package}'],
        'list_installed': ['pikaur', '-Q'],
        'download_pkgbuild': ['pikaur', '-G', '{package}'],
        'refresh': ['pikaur', '-Sy', '--noconfirm'],
        'list_upgradable': ['pikaur', '-Qu'],
        'list_repo': ['pacman', '-Sl', '{repo}'],
        'supports_aur': True,
        'uses_pacman_flags': True,
        'update_split': True,
    },
    'pacman': {
        'search': ['pacman', '-Ss', '{query}'],
        'install': ['pacman', '-S', '--needed', '--noconfirm', '{packages}'],
        'remove': ['pacman', '-Rns', '--noconfirm', '{packages}'],
        'update': ['pacman', '-Syu', '--noconfirm'],
        'info': ['pacman', '-Qi', '{package}'],
        'info_remote': ['pacman', '-Si', '{package}'],
        'list_installed': ['pacman', '-Q'],
        'download_pkgbuild': None,
        'refresh': ['pacman', '-Sy', '--noconfirm'],
        'list_upgradable': ['pacman', '-Qu'],
        'list_repo': ['pacman', '-Sl', '{repo}'],
        'supports_aur': False,
        'uses_pacman_flags': True,
    },
}


def detect_helper(force_helper: Optional[str] = None) -> str:
    """
    Determine which helper to use.

    If force_helper is given, it is used as-is provided a command mapping
    exists for it and (unless it's plain pacman) the binary is actually on
    PATH. Otherwise HELPER_PRIORITY is checked in order, falling back to
    pacman. Raises NoHelperError if not even pacman is available.
    """
    if force_helper:
        if force_helper not in HELPER_COMMANDS:
            raise HelperNotFoundError(
                f"'{force_helper}' has no known command mapping in pkgmanager"
            )
        if force_helper != 'pacman' and shutil.which(force_helper) is None:
            raise HelperNotFoundError(f"'{force_helper}' was not found in PATH")
        return force_helper

    for helper in HELPER_PRIORITY:
        if shutil.which(helper):
            return helper

    if shutil.which('pacman'):
        return 'pacman'

    raise NoHelperError("No package manager helper (not even pacman) was found in PATH")


_SEARCH_HEADER_RE = re.compile(r'^(?P<repo>[^/\s]+)/(?P<name>\S+)\s+(?P<version>\S+)(?P<extra>.*)$')
_INSTALLED_LINE_RE = re.compile(r'^(?:(?P<repo>[^/\s]+)/)?(?P<name>\S+)\s+(?P<version>\S+)')
_UPGRADABLE_LINE_RE = re.compile(r'^(?P<name>\S+)\s+(?P<current>\S+)\s*->\s*(?P<new>\S+)')
_REPO_LISTING_RE = re.compile(r'^(?P<repo>\S+)\s+(?P<name>\S+)\s+(?P<version>\S+)(?P<extra>.*)$')


def parse_search_output(raw_output: str) -> List[Package]:
    """
    Parse `-Ss`-style search output shared by pacman, yay, paru, trizen and
    pikaur:

        repo/name version [(group)] [[installed]]
            description line(s)

    Entries are separated by a blank line. Blocks that don't match the
    expected header shape are skipped rather than raising, since a helper
    printing an occasional warning line shouldn't take down the whole search.
    """
    packages: List[Package] = []
    if not raw_output or not raw_output.strip():
        return packages

    for block in raw_output.strip('\n').split('\n\n'):
        lines = block.split('\n')
        if not lines or not lines[0].strip():
            continue
        match = _SEARCH_HEADER_RE.match(lines[0].strip())
        if not match:
            continue

        repo = match.group('repo').strip('()')
        name = match.group('name')
        version = match.group('version')
        extra = match.group('extra') or ''
        installed = '[installed' in extra.lower()

        description = ' '.join(l.strip() for l in lines[1:] if l.strip())

        packages.append(Package(
            name=name,
            version=version,
            description=description,
            repository=repo,
            installed=installed,
            is_aur=(repo.lower() == 'aur'),
        ))

    return packages


def parse_installed_output(raw_output: str) -> List[Package]:
    """
    Parse `-Q`-style installed-package listings.

    Real pacman/AUR-helper output is just `name version` per line with no
    repo prefix (pacman -Q doesn't know or report which repo a package came
    from). An optional `repo/` prefix is still accepted for helpers or
    configurations that do include one, defaulting to 'local' when absent.
    """
    packages: List[Package] = []
    if not raw_output or not raw_output.strip():
        return packages

    for line in raw_output.strip('\n').split('\n'):
        line = line.strip()
        if not line:
            continue
        match = _INSTALLED_LINE_RE.match(line)
        if not match:
            continue
        repo = match.group('repo') or 'local'
        packages.append(Package(
            name=match.group('name'),
            version=match.group('version'),
            description='',
            repository=repo,
            installed=True,
            is_aur=(repo.lower() == 'aur'),
        ))

    return packages


def parse_upgradable_output(raw_output: str) -> List[Package]:
    """
    Parse `-Qu`-style output: 'name current_version -> new_version' per line.

    `-Qu` is a pure local-database comparison (it never touches the network),
    so it doesn't tell us which repo a package came from -- repository is
    reported as 'local' (matching parse_installed_output's convention) and
    is_aur is left False rather than guessed at.
    """
    packages: List[Package] = []
    if not raw_output or not raw_output.strip():
        return packages

    for line in raw_output.strip('\n').split('\n'):
        line = line.strip()
        if not line:
            continue
        match = _UPGRADABLE_LINE_RE.match(line)
        if not match:
            continue
        packages.append(Package(
            name=match.group('name'),
            version=match.group('current'),
            description='',
            repository='local',
            installed=True,
            is_aur=False,
            new_version=match.group('new'),
        ))

    return packages


def parse_repo_listing_output(raw_output: str) -> List[Package]:
    """
    Parse `pacman -Sl <repo>`-style output: 'repo name version [installed]'
    per line (space-separated -- note this is *not* the same layout as
    `-Ss`, which uses 'repo/name'). This only ever lists official sync
    repos (core/extra/community/multilib/...); there's no AUR equivalent,
    so is_aur is always False here.
    """
    packages: List[Package] = []
    if not raw_output or not raw_output.strip():
        return packages

    for line in raw_output.strip('\n').split('\n'):
        line = line.strip()
        if not line:
            continue
        match = _REPO_LISTING_RE.match(line)
        if not match:
            continue
        extra = (match.group('extra') or '').lower()
        packages.append(Package(
            name=match.group('name'),
            version=match.group('version'),
            description='',
            repository=match.group('repo'),
            installed=('[installed' in extra),
            is_aur=False,
        ))

    return packages


# pacman/AUR-helper field name (lowercased) -> PackageDetail attribute.
# Fields not listed here (Architecture, Groups, Provides, Required By,
# Optional For, Conflicts With, Replaces, Install Reason, Install Script,
# Validated By, Build Date, First/Last ...) aren't modeled and are ignored.
_INFO_FIELD_MAP = {
    'name': 'name',
    'version': 'version',
    'description': 'description',
    'repository': 'repository',
    'repo': 'repository',
    'url': 'url',
    'aur url': None,  # the project URL is already covered by 'url'
    'licenses': 'license',
    'license': 'license',
    'depends on': 'depends',
    'make deps': None,
    'optional deps': 'optional_deps',
    'installed size': 'size',
    'download size': 'size',
    'install date': 'install_date',
    'maintainer': 'maintainer',
    'votes': 'votes',
    'popularity': 'popularity',
    'out-of-date': 'out_of_date',
}

_LIST_FIELDS = {'depends', 'optional_deps', 'license'}


def _split_list_value(value: str) -> List[str]:
    if not value or value.strip().lower() == 'none':
        return []
    parts = re.split(r'\n|\s{2,}', value.strip())
    return [p.strip() for p in parts if p.strip()]


def parse_info_output(raw_output: str) -> PackageDetail:
    """
    Parse `-Qi`/`-Si`-style detailed info output (pacman and every supported
    AUR helper share this "Key : value" format; AUR helpers add extra fields
    like Votes/Popularity/Maintainer, simply absent for official-repo
    packages). Assumes raw_output came from a successful command invocation
    -- a non-zero exit is expected to have already raised CommandFailedError
    upstream, so this never needs to handle "package not found" text.
    """
    raw_fields: dict = {}
    current_key: Optional[str] = None

    for line in (raw_output or '').split('\n'):
        if not line.strip():
            continue
        stripped = line.strip()
        # A continuation line (e.g. wrapped Optional Deps) has no leading
        # key and is indented; append it to whatever field we last read.
        looks_like_new_field = ':' in line and not line.startswith((' ', '\t'))
        if looks_like_new_field:
            key, _, value = line.partition(':')
            key = key.strip().lower()
            value = value.strip()
            raw_fields[key] = value
            current_key = key
        elif current_key is not None:
            raw_fields[current_key] = raw_fields[current_key] + '\n' + stripped

    detail = PackageDetail(
        name=raw_fields.get('name', ''),
        version=raw_fields.get('version', ''),
        description=raw_fields.get('description', ''),
        repository=raw_fields.get('repository', raw_fields.get('repo', 'unknown')),
    )

    for raw_key, value in raw_fields.items():
        target = _INFO_FIELD_MAP.get(raw_key)
        if not target or target in ('name', 'version', 'description', 'repository'):
            continue
        if target in _LIST_FIELDS:
            setattr(detail, target, _split_list_value(value))
        elif target == 'votes':
            try:
                detail.votes = int(value.replace(',', ''))
            except ValueError:
                pass
        elif target == 'popularity':
            try:
                detail.popularity = float(value)
            except ValueError:
                pass
        elif target == 'out_of_date':
            detail.out_of_date = value.strip().lower() not in ('no', 'none', '')
        else:
            setattr(detail, target, value if value else None)

    return detail
