"""
Pre-flight safety checks run before a privileged pacman/helper operation:
stale-lock detection, a free-space estimate, and an optional site-local
hook script.

Deliberate deviation from the original spec: check_stale_lock() only
*reports* whether the lock looks stale. It never deletes anything itself.
Auto-deleting another process's lock file has a TOCTOU race between the
"is pacman running" check and the delete, and if that race is ever lost
the result is a corrupted mid-transaction pacman database -- exactly the
kind of destructive operation that should require one explicit
confirmation, not fire silently. remove_stale_lock() exists separately and
is meant to be called only after the caller (GUI) has shown the detected
state to the user and gotten a yes.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

from .exceptions import PackageManagerError
from .helpers import parse_size_to_bytes
from .models import PackageDetail
from .permissions import run as _run_elevated

DEFAULT_LOCK_PATH = '/var/lib/pacman/db.lck'
DEFAULT_FREE_SPACE_BUFFER = 1 << 30  # 1 GiB, matches the original spec's buffer
DEFAULT_PREFLIGHT_HOOK = '/usr/local/bin/aur-gui-preflight'
DEFAULT_HOOK_TIMEOUT = 10  # seconds

# Any AUR helper ultimately hands the actual transaction to pacman itself,
# so checking for a running pacman process covers "is yay/paru/etc mid-run"
# too without needing a name per helper.
_PACMAN_PROCESS_NAMES = {'pacman', 'pacman-static'}


class LockState:
    """Plain string constants (this codebase doesn't use enum.Enum
    elsewhere) for the three possible lock states."""
    NONE = 'none'    # no lock file -- nothing to do
    STALE = 'stale'  # lock file present, no pacman process running
    HELD = 'held'    # lock file present, pacman process running


def _proc_comm(pid: int, proc_root: str) -> Optional[str]:
    try:
        with open(f'{proc_root}/{pid}/stat', 'r') as fh:
            raw = fh.read()
    except OSError:
        return None
    start, end = raw.find('('), raw.rfind(')')
    if start == -1 or end == -1 or end <= start:
        return None
    return raw[start + 1:end]


def _is_pacman_running(proc_root: str = '/proc') -> bool:
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return False
    return any(
        _proc_comm(int(entry), proc_root) in _PACMAN_PROCESS_NAMES
        for entry in entries if entry.isdigit()
    )


def check_stale_lock(lock_path: str = DEFAULT_LOCK_PATH, proc_root: str = '/proc') -> str:
    """One of LockState.{NONE,STALE,HELD}. Read-only -- see module docstring."""
    if not os.path.exists(lock_path):
        return LockState.NONE
    return LockState.HELD if _is_pacman_running(proc_root) else LockState.STALE


def remove_stale_lock(lock_path: str = DEFAULT_LOCK_PATH) -> bool:
    """
    Deletes the lock file via the same pkexec/sudo elevation every other
    privileged operation uses (the lock is root-owned; this process isn't).
    Only call this after check_stale_lock() returned STALE *and* the user
    has explicitly confirmed -- this does not re-check on its own, since
    asking twice doesn't close the race, it just moves it.
    """
    try:
        _run_elevated(['rm', '-f', lock_path], operation='remove_lock')
        return True
    except PackageManagerError:
        return False


@dataclass
class SpaceEstimate:
    required_bytes: int
    free_bytes: int
    has_enough: bool
    unknown_packages: List[str] = field(default_factory=list)


def estimate_required_space(details: Iterable[PackageDetail], path: str = '/',
                             buffer_bytes: int = DEFAULT_FREE_SPACE_BUFFER) -> SpaceEstimate:
    """
    Sums download_size + installed_size across all given package details,
    plus a fixed buffer, and compares against shutil.disk_usage(path).free.

    Sums both sizes rather than taking max() of the two: pacman keeps the
    downloaded file in its cache *while* writing the installed copy, so
    both consume space concurrently during the operation -- max() would
    under-count peak usage, which is the wrong direction to be wrong in for
    a guard whose entire purpose is avoiding a mid-operation ENOSPC.

    A package with no parseable size contributes 0 to the total and its
    name to unknown_packages, rather than silently being treated as
    "definitely fits" -- callers should surface that list, not hide it.
    """
    total = 0
    unknown: List[str] = []
    for detail in details:
        dl = parse_size_to_bytes(detail.download_size)
        inst = parse_size_to_bytes(detail.installed_size) or parse_size_to_bytes(detail.size)
        if dl is None and inst is None:
            unknown.append(detail.name)
            continue
        total += (dl or 0) + (inst or 0)

    required = total + buffer_bytes
    free = shutil.disk_usage(path).free
    return SpaceEstimate(required_bytes=required, free_bytes=free,
                          has_enough=(free >= required), unknown_packages=unknown)


@dataclass
class HookResult:
    ran: bool               # False if the hook doesn't exist/isn't executable -- not itself a failure
    ok: bool
    stderr: str = ''
    timed_out: bool = False


def run_preflight_hook(path: str = DEFAULT_PREFLIGHT_HOOK,
                        timeout: int = DEFAULT_HOOK_TIMEOUT) -> HookResult:
    """
    Runs an optional site-local script as the *current*, unprivileged user
    (deliberately not elevated -- this is for platform checks like "is the
    NTFS partition dirty", not a way to grant it root). Silently skipped
    (ran=False, ok=True) if the path doesn't exist or isn't executable,
    since this is an opt-in extension point, not a required file.

    Always bounded by `timeout`. The original spec has this running
    synchronously on the GUI thread, which is fine for a fast check but not
    for an arbitrary user-supplied script with no bound on its runtime --
    a timeout is treated as failure (timed_out=True) rather than left
    unbounded.
    """
    if not (os.path.isfile(path) and os.access(path, os.X_OK)):
        return HookResult(ran=False, ok=True)
    try:
        proc = subprocess.run([path], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return HookResult(ran=True, ok=False, stderr=f'Timed out after {timeout}s', timed_out=True)
    except OSError as exc:
        return HookResult(ran=True, ok=False, stderr=str(exc))
    return HookResult(ran=True, ok=(proc.returncode == 0), stderr=proc.stderr)
