"""
PackageManager: the public API consumed by the GUI layer.
"""

from typing import Callable, Iterator, List, Optional, Union

from .config import load_config
from .exceptions import AURHelperMissingError, CommandFailedError, PackageManagerError
from .helpers import (
    HELPER_COMMANDS,
    detect_helper,
    parse_installed_output,
    parse_info_output,
    parse_info_output_many,
    parse_repo_listing_output,
    parse_search_output,
    parse_upgradable_output,
)
from .models import Package, PackageDetail
from .permissions import run as _run_impl

_PLACEHOLDER_QUERY = '{query}'
_PLACEHOLDER_PACKAGE = '{package}'
_PLACEHOLDER_PACKAGES = '{packages}'
_PLACEHOLDER_REPO = '{repo}'


class PackageManager:
    """
    Unified interface to pacman + whichever AUR helper (yay/paru/trizen/pikaur)
    is installed, or plain pacman if none is.

    Thread-safe: an instance holds no mutable state beyond what's fixed at
    construction (helper name, command map, noconfirm preference), so the
    same instance can safely be driven from a background thread while the
    GUI thread stays responsive.
    """

    def __init__(self, force_helper: Optional[str] = None, use_noconfirm: bool = True):
        self.helper = detect_helper(force_helper)
        self.use_noconfirm = use_noconfirm
        self._command_map = HELPER_COMMANDS.get(self.helper, HELPER_COMMANDS['pacman'])

    @classmethod
    def from_config(cls, path=None) -> 'PackageManager':
        """Construct using ~/.config/pkgmanager/config.json (or a custom
        path), falling back to defaults for anything unset."""
        cfg = load_config(path)
        return cls(force_helper=cfg.get('force_helper'), use_noconfirm=cfg.get('use_noconfirm', True))

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def search(self, query: str) -> List[Package]:
        """Search official repos + AUR (if supported) for `query`."""
        cmd = self._build_command('search', query=query)
        output = self._run(cmd, stream=False, operation='search')
        return parse_search_output(output)

    def install(self, packages: List[str], callback: Optional[Callable[[str], None]] = None,
                known_aur: bool = False,
                pid_callback: Optional[Callable[[int], None]] = None) -> None:
        """
        Install one or more packages, streaming output lines to `callback` as
        they arrive (or silently discarding them if callback is None).

        The library can't tell on its own whether a given name is AUR-only
        -- doing so would mean an extra query before every install. If the
        caller already knows (e.g. it came from a search() result with
        is_aur=True), pass known_aur=True to fail fast with
        AURHelperMissingError instead of letting the underlying pacman call
        fail on an unresolvable target.

        pid_callback, if given, is invoked once with the PID of the
        (possibly pkexec/sudo-wrapped) process as soon as it's launched --
        note this is the elevation wrapper's PID, not the underlying
        pacman/helper process; see permissions.watch_io_wait for why that
        distinction matters for I/O-wait monitoring.
        """
        if not packages:
            return
        if known_aur and not self.supports_aur():
            raise AURHelperMissingError(
                f"No AUR helper is installed; cannot install AUR package(s): {', '.join(packages)}"
            )
        cmd = self._build_command('install', packages=packages)
        self._stream_or_collect(cmd, operation='install', callback=callback, pid_callback=pid_callback)

    def remove(self, packages: List[str], callback: Optional[Callable[[str], None]] = None,
               pid_callback: Optional[Callable[[int], None]] = None) -> None:
        """Remove one or more installed packages. Works regardless of AUR
        support, since removal never needs to fetch anything."""
        if not packages:
            return
        cmd = self._build_command('remove', packages=packages)
        self._stream_or_collect(cmd, operation='remove', callback=callback, pid_callback=pid_callback)

    def update(self, callback: Optional[Callable[[str], None]] = None,
               pid_callback: Optional[Callable[[int], None]] = None) -> None:
        """Sync + upgrade. pikaur splits this into two sequential commands;
        every other supported helper does it in one. pid_callback (if given)
        fires once per sub-command for a split update, since each is a
        separate process."""
        template = self._command_map.get('update')
        if template is None:
            raise PackageManagerError(f"'update' is not supported by helper '{self.helper}'")

        if self._command_map.get('update_split'):
            for sub_cmd in template:
                self._stream_or_collect(self._finalize_tokens(sub_cmd), operation='update',
                                         callback=callback, pid_callback=pid_callback)
        else:
            self._stream_or_collect(self._finalize_tokens(template), operation='update',
                                     callback=callback, pid_callback=pid_callback)

    def info(self, package: str, local: bool = True) -> PackageDetail:
        """Get detailed info for `package`. local=True queries the installed
        copy (-Qi); local=False queries the repo/AUR copy (-Si)."""
        operation = 'info' if local else 'info_remote'
        cmd = self._build_command(operation, package=package)
        output = self._run(cmd, stream=False, operation=operation)
        return parse_info_output(output)

    def info_many(self, packages: List[str], local: bool = True) -> List[PackageDetail]:
        """
        Get detailed info for several packages in one call instead of one
        subprocess per package -- matters for things like a pre-update
        free-space estimate, where "one query per upgradable package" can
        mean dozens of separate pacman/helper invocations.

        If any name in the batch fails to resolve, the whole batched call
        fails (pacman exits non-zero and stdout/stderr for the invalid name
        get merged into the same stream as the valid blocks, which isn't
        safe to try to pick apart). Rather than lose every result over one
        bad name, this falls back to querying each package individually and
        silently drops the ones that still fail -- callers that need to know
        *which* packages came back unresolved should compare the length of
        the result against `packages`.
        """
        if not packages:
            return []
        operation = 'info_many' if local else 'info_remote_many'
        cmd = self._build_command(operation, packages=packages)
        try:
            output = self._run(cmd, stream=False, operation=operation)
            return parse_info_output_many(output)
        except CommandFailedError:
            results: List[PackageDetail] = []
            for pkg in packages:
                try:
                    results.append(self.info(pkg, local=local))
                except PackageManagerError:
                    continue
            return results

    def list_installed(self) -> List[Package]:
        """List all installed packages."""
        cmd = self._build_command('list_installed')
        output = self._run(cmd, stream=False, operation='list_installed')
        return parse_installed_output(output)

    def refresh(self, callback: Optional[Callable[[str], None]] = None,
                pid_callback: Optional[Callable[[int], None]] = None) -> None:
        """Sync the package databases (-Sy) without upgrading anything."""
        cmd = self._build_command('refresh')
        self._stream_or_collect(cmd, operation='refresh', callback=callback, pid_callback=pid_callback)

    def list_upgradable(self) -> List[Package]:
        """List installed packages that have a newer version available
        (requires a prior refresh() for the comparison to be current)."""
        cmd = self._build_command('list_upgradable')
        output = self._run(cmd, stream=False, operation='list_upgradable')
        return parse_upgradable_output(output)

    def list_repo_packages(self, repo: str) -> List[Package]:
        """
        List every package in one official sync repo (e.g. 'core', 'extra',
        'community', 'multilib') via `pacman -Sl <repo>`. Always goes
        through pacman directly -- this is a pure sync-database read with
        no AUR-helper-specific behavior, so there's no reason to route it
        through yay/paru/etc. Not meaningful for 'aur': the AUR has no bulk
        listing endpoint, so browsing AUR results works by filtering the
        current search() results client-side instead (see the GUI).
        """
        cmd = self._build_command('list_repo', repo=repo)
        output = self._run(cmd, stream=False, operation='list_repo')
        return parse_repo_listing_output(output)

    def helper_info(self) -> str:
        """Name of the detected/forced helper ('pacman' if none is installed)."""
        return self.helper

    def supports_aur(self) -> bool:
        """Whether the active helper can operate on the AUR."""
        return bool(self._command_map.get('supports_aur', False))

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _finalize_tokens(self, tokens: List[str]) -> List[str]:
        """Drop a literal '--noconfirm' token when use_noconfirm is False."""
        if self.use_noconfirm:
            return list(tokens)
        return [t for t in tokens if t != '--noconfirm']

    def _build_command(self, operation: str, query: Optional[str] = None,
                        package: Optional[str] = None,
                        packages: Optional[List[str]] = None,
                        repo: Optional[str] = None) -> List[str]:
        template = self._command_map.get(operation)
        if template is None:
            raise PackageManagerError(
                f"Operation '{operation}' is not supported by helper '{self.helper}'"
            )

        cmd: List[str] = []
        for token in template:
            if token == _PLACEHOLDER_QUERY:
                cmd.append(query or '')
            elif token == _PLACEHOLDER_PACKAGE:
                cmd.append(package or '')
            elif token == _PLACEHOLDER_PACKAGES:
                cmd.extend(packages or [])
            elif token == _PLACEHOLDER_REPO:
                cmd.append(repo or '')
            else:
                cmd.append(token)
        return self._finalize_tokens(cmd)

    def _run(self, cmd: List[str], stream: bool = False, use_pkexec: Optional[bool] = None,
              operation: Optional[str] = None,
              pid_callback: Optional[Callable[[int], None]] = None) -> Union[str, Iterator[str]]:
        """Thin wrapper over permissions.run(), kept as a method for parity
        with the original design (and so subclasses/tests can override it)."""
        return _run_impl(cmd, stream=stream, use_pkexec=use_pkexec, operation=operation,
                          pid_callback=pid_callback)

    def _stream_or_collect(self, cmd: List[str], operation: str,
                            callback: Optional[Callable[[str], None]],
                            pid_callback: Optional[Callable[[int], None]] = None) -> None:
        lines = self._run(cmd, stream=True, operation=operation, pid_callback=pid_callback)
        if callback is None:
            for _ in lines:
                pass
            return
        for line in lines:
            callback(line)
