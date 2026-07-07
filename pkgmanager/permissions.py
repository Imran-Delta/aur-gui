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
from typing import Iterator, List, Optional, Union

from .exceptions import CommandFailedError, HelperNotFoundError, PermissionDeniedError

# Operations that mutate system state and therefore need elevated privileges.
# 'list_upgradable' (-Qu) and 'list_repo' (-Sl) are pure reads and stay
# unprivileged; 'refresh' (-Sy) writes to the sync database cache, so it
# needs elevation just like install/remove/update.
ROOT_REQUIRED_OPERATIONS = {'install', 'remove', 'update', 'refresh'}


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
        operation: Optional[str] = None) -> Union[str, Iterator[str]]:
    """
    Run `cmd` (already a fully-built argument list -- no shell metacharacters
    are ever interpreted).

    operation: the logical operation name ('install', 'remove', 'update',
    'search', 'info', 'info_remote', 'list_installed'). Used only to decide
    whether privilege elevation is needed; pass None for read-only calls.

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
