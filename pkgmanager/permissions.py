"""
Command execution and privilege elevation.

Every external command is executed as an argument list (never through a
shell), and privilege escalation (pkexec/sudo) is only ever added as a
prefix to that list -- nothing here ever concatenates user input into a
shell string.
"""

import os
import shutil
import subprocess
import time
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Set, Tuple, Union

from .exceptions import CommandFailedError, HelperNotFoundError, PermissionDeniedError

# Operations that mutate system state and therefore need elevated privileges.
# 'list_upgradable' (-Qu) and 'list_repo' (-Sl) are pure reads and stay
# unprivileged; 'refresh' (-Sy) writes to the sync database cache, so it
# needs elevation just like install/remove/update.
ROOT_REQUIRED_OPERATIONS = {'install', 'remove', 'update', 'refresh', 'remove_lock'}


def is_gui_environment() -> bool:
    """True if a graphical session is detected (X11 or Wayland)."""
    return bool(os.environ.get('DISPLAY')) or bool(os.environ.get('WAYLAND_DISPLAY'))


def _requires_root(operation: Optional[str]) -> bool:
    return operation in ROOT_REQUIRED_OPERATIONS if operation else False


def _elevate(cmd: List[str], use_pkexec: Optional[bool], operation: Optional[str]) -> List[str]:
    if not _requires_root(operation):
        return cmd

    if use_pkexec is None:
        use_pkexec = is_gui_environment()

    if use_pkexec:
        if shutil.which('pkexec') is None:
            raise PermissionDeniedError("pkexec not found in PATH; cannot elevate for a GUI session")
        return ['pkexec'] + cmd

    if shutil.which('sudo') is None:
        raise PermissionDeniedError("sudo not found in PATH; cannot elevate privileges")
    return ['sudo'] + cmd


def run(cmd: List[str], stream: bool = False, use_pkexec: Optional[bool] = None,
        operation: Optional[str] = None,
        pid_callback: Optional[Callable[[int], None]] = None) -> Union[str, Iterator[str]]:
    """
    Run `cmd` (already a fully-built argument list -- no shell metacharacters
    are ever interpreted).

    operation: the logical operation name ('install', 'remove', 'update',
    'search', 'info', 'info_remote', 'list_installed'). Used only to decide
    whether privilege elevation is needed; pass None for read-only calls.

    pid_callback, if given, is called once with process.pid immediately
    after a successful launch. For a root-required operation this is the
    PID of `pkexec`/`sudo`, *not* the pacman/helper process underneath it --
    use list_descendant_pids()/watch_io_wait() below rather than polling
    this PID's own /proc state directly, since the wrapper process typically
    just sits idle (S state) waiting on its child while the child does the
    actual (possibly I/O-bound) work.

    stream=False returns the combined stdout+stderr as one string, raising
    CommandFailedError on a non-zero exit.
    stream=True returns a generator yielding output lines as they arrive;
    the caller should fully consume it -- CommandFailedError (if any) is
    only raised once the process actually exits, at the end of iteration.
    """
    if shutil.which(cmd[0]) is None:
        raise HelperNotFoundError(f"'{cmd[0]}' was not found in PATH")

    full_cmd = _elevate(cmd, use_pkexec, operation)

    env = os.environ.copy()
    if full_cmd[0] == 'pkexec':
        # pkexec starts a fresh environment; forward what a GUI child needs.
        for var in ('DISPLAY', 'XAUTHORITY', 'WAYLAND_DISPLAY'):
            if var in os.environ:
                env[var] = os.environ[var]

    try:
        process = subprocess.Popen(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise HelperNotFoundError(f"Could not execute '{full_cmd[0]}': {exc}") from exc
    except OSError as exc:
        raise PermissionDeniedError(f"Failed to launch '{full_cmd[0]}': {exc}") from exc

    if pid_callback is not None:
        pid_callback(process.pid)

    if stream:
        return _stream_lines(process, full_cmd)

    output = process.stdout.read()
    returncode = process.wait()
    if returncode != 0:
        raise CommandFailedError(full_cmd, returncode, output, '')
    return output


def _stream_lines(process: subprocess.Popen, full_cmd: List[str]) -> Iterator[str]:
    try:
        for line in process.stdout:
            yield line.rstrip('\n')
    except GeneratorExit:
        # Caller stopped consuming early (e.g. broke out of the loop); just
        # clean up rather than raising a spurious error over the top of it.
        process.terminate()
        raise
    else:
        returncode = process.wait()
        if returncode != 0:
            raise CommandFailedError(full_cmd, returncode, '', '')


# --------------------------------------------------------------------------
# I/O-wait monitoring.
#
# A tracked PID from a root-required run() call is pkexec's or sudo's, not
# pacman's -- these functions walk the whole descendant tree instead of
# reading one PID's state, so "pkexec -> yay -> pacman" (or any other depth)
# is covered regardless of exactly how many hops the elevation + helper add.
# --------------------------------------------------------------------------

def _read_proc_stat(pid: int, proc_root: str = '/proc') -> Optional[Tuple[str, int]]:
    """(state, ppid) for `pid`, or None if it no longer exists. Parsed from
    the last ')' in the line rather than a naive whitespace split, since the
    comm field (in parentheses, field 2) can itself contain spaces."""
    try:
        with open(f'{proc_root}/{pid}/stat', 'r') as fh:
            raw = fh.read()
    except (FileNotFoundError, ProcessLookupError, OSError):
        return None
    idx = raw.rfind(')')
    if idx == -1:
        return None
    fields = raw[idx + 1:].split()
    if len(fields) < 2:
        return None
    try:
        return fields[0], int(fields[1])
    except ValueError:
        return None


def list_descendant_pids(root_pid: int, proc_root: str = '/proc') -> Set[int]:
    """All live descendants of root_pid, including root_pid itself -- empty
    if root_pid isn't running. Scans /proc once and follows ppid links
    rather than re-scanning per level."""
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return set()

    children: Dict[int, List[int]] = {}
    alive: Set[int] = set()
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        stat = _read_proc_stat(pid, proc_root)
        if stat is None:
            continue
        _, ppid = stat
        alive.add(pid)
        children.setdefault(ppid, []).append(pid)

    if root_pid not in alive:
        return set()

    result = {root_pid}
    frontier = [root_pid]
    while frontier:
        pid = frontier.pop()
        for child in children.get(pid, []):
            if child not in result:
                result.add(child)
                frontier.append(child)
    return result


def any_process_io_wait(pids: Iterable[int], proc_root: str = '/proc') -> bool:
    """True if any of `pids` is currently in D (uninterruptible sleep --
    blocked on I/O) state."""
    for pid in pids:
        stat = _read_proc_stat(pid, proc_root)
        if stat is not None and stat[0] == 'D':
            return True
    return False


def watch_io_wait(root_pid: int, on_change: Callable[[bool], None],
                   poll_interval: float = 0.2, proc_root: str = '/proc') -> None:
    """
    Blocking loop -- run this on its own daemon thread. Polls every
    poll_interval seconds for as long as root_pid or any of its descendants
    is alive, calling on_change(True)/on_change(False) exactly when the
    aggregate I/O-wait state changes (not on every poll), and returning on
    its own once the whole tracked process tree has exited.
    """
    last_state: Optional[bool] = None
    while True:
        pids = list_descendant_pids(root_pid, proc_root)
        if not pids:
            if last_state:
                on_change(False)
            return
        waiting = any_process_io_wait(pids, proc_root)
        if waiting != last_state:
            on_change(waiting)
            last_state = waiting
        time.sleep(poll_interval)
