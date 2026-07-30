"""
Read-only system diagnostics.

This deliberately does NOT run the unittest suite. That suite mocks
subprocess.Popen/shutil.which throughout (by design -- it's testing this
package's own parsing/command-building logic against controlled input, not
the machine it happens to run on) and so shells out to nothing real; running
it on an end user's machine would exercise the exact same mocked code paths
and could not tell you anything about *their* pacman, AUR helper, Polkit
setup, or disk. What actually answers "does something look wrong with my
installed system" is a distinct battery of real, non-destructive checks
against that system, which is what this module runs instead.

Every check here is read-only: nothing here installs, removes, upgrades,
or deletes anything.
"""

import os
import shutil
from dataclasses import dataclass
from typing import List, Optional
from xml.etree import ElementTree

from .backend import PackageManager
from .exceptions import PackageManagerError
from .helpers import detect_helper
from .preflight import LockState, check_stale_lock

STATUS_PASS = 'pass'
STATUS_WARN = 'warn'
STATUS_FAIL = 'fail'

POLKIT_ACTIONS_DIR = '/usr/share/polkit-1/actions'
LOW_DISK_WARNING_BYTES = 500 * 1024 * 1024  # 500 MiB


@dataclass
class DiagnosticResult:
    name: str
    status: str  # one of STATUS_*
    message: str


def _binary_check(name: str) -> DiagnosticResult:
    path = shutil.which(name)
    if path:
        return DiagnosticResult(f'binary:{name}', STATUS_PASS, f'{name} found at {path}')
    return DiagnosticResult(f'binary:{name}', STATUS_FAIL, f'{name} not found in PATH')


def _check_helper_detection() -> DiagnosticResult:
    try:
        helper = detect_helper()
    except PackageManagerError as exc:
        return DiagnosticResult('helper_detection', STATUS_FAIL, str(exc))
    return DiagnosticResult('helper_detection', STATUS_PASS, f'active helper: {helper}')


def _check_live_parse(pm: PackageManager) -> DiagnosticResult:
    """Runs a real, unprivileged -Q and feeds it through the real parser --
    this is the check most likely to catch a distro/locale/helper-version
    quirk the mocked unit tests can't see, since those tests only ever see
    curated fixture text."""
    try:
        installed = pm.list_installed()
    except PackageManagerError as exc:
        return DiagnosticResult('live_parse:list_installed', STATUS_FAIL,
                                 f"'-Q' failed or didn't parse: {exc}")
    if not installed:
        return DiagnosticResult('live_parse:list_installed', STATUS_WARN,
                                 "'-Q' returned no packages -- unexpected on a real system")
    return DiagnosticResult('live_parse:list_installed', STATUS_PASS,
                             f'parsed {len(installed)} installed package(s)')


def _check_live_info(pm: PackageManager) -> DiagnosticResult:
    """Same idea via -Qi (a different output shape than -Q), against
    'pacman' itself since that package is guaranteed to be installed
    wherever pacman can be invoked at all."""
    try:
        detail = pm.info('pacman', local=True)
    except PackageManagerError as exc:
        return DiagnosticResult('live_parse:info', STATUS_FAIL,
                                 f"'-Qi pacman' failed or didn't parse: {exc}")
    if not detail.name or not detail.version:
        return DiagnosticResult('live_parse:info', STATUS_WARN,
                                 "'-Qi pacman' parsed but is missing name/version")
    return DiagnosticResult('live_parse:info', STATUS_PASS,
                             f'parsed detail for pacman {detail.version}')


def _check_lock() -> DiagnosticResult:
    state = check_stale_lock()
    if state == LockState.NONE:
        return DiagnosticResult('lock_state', STATUS_PASS, 'no lock file present')
    if state == LockState.HELD:
        return DiagnosticResult('lock_state', STATUS_WARN, 'lock file present; pacman is currently running')
    return DiagnosticResult('lock_state', STATUS_WARN,
                             'lock file present but no pacman process is running (stale)')


def _check_disk_space(path: str = '/') -> DiagnosticResult:
    try:
        free = shutil.disk_usage(path).free
    except OSError as exc:
        return DiagnosticResult('disk_space', STATUS_FAIL, str(exc))
    mib = free / (1024 * 1024)
    if free < LOW_DISK_WARNING_BYTES:
        return DiagnosticResult('disk_space', STATUS_WARN, f'only {mib:.0f} MiB free on {path}')
    return DiagnosticResult('disk_space', STATUS_PASS, f'{mib:.0f} MiB free on {path}')


def _check_polkit_policy(target_binary_path: Optional[str],
                          actions_dir: str = POLKIT_ACTIONS_DIR) -> DiagnosticResult:
    """
    Looks for any installed .policy file that grants cached auth
    (org.freedesktop.policykit.exec.path annotation) for the binary that
    actually gets pkexec'd. A plain .policy file can only grant/require auth
    for a whole binary path, not distinguish by argument (e.g. -Sy vs -S) --
    see the README for the .rules file that adds argument-level behavior;
    this check only confirms the simpler, always-required base policy.
    """
    if not target_binary_path:
        return DiagnosticResult('polkit_policy', STATUS_WARN, 'could not resolve a target binary to check')
    try:
        entries = os.listdir(actions_dir)
    except OSError:
        return DiagnosticResult('polkit_policy', STATUS_WARN,
                                 f'{actions_dir} not readable; cached-auth policy status unknown')
    for entry in entries:
        if not entry.endswith('.policy'):
            continue
        try:
            tree = ElementTree.parse(os.path.join(actions_dir, entry))
        except ElementTree.ParseError:
            continue
        for annotate in tree.getroot().iter('annotate'):
            if (annotate.get('key') == 'org.freedesktop.policykit.exec.path'
                    and (annotate.text or '').strip() == target_binary_path):
                return DiagnosticResult('polkit_policy', STATUS_PASS,
                                         f'cached-auth policy found in {entry}')
    return DiagnosticResult('polkit_policy', STATUS_WARN,
                             'no cached-auth Polkit policy found; every privileged action will '
                             'prompt fresh -- see README for the optional example policy')


def run_diagnostics(pm: Optional[PackageManager] = None) -> List[DiagnosticResult]:
    """
    Runs every check and returns the full list. Constructing a
    PackageManager can itself fail (no helper *and* no pacman found) --
    that's reported as its own failing result rather than raising, so a
    completely broken environment still gets a full report instead of a
    traceback.
    """
    results = [_binary_check(name) for name in ('pacman', 'sudo', 'pkexec')]
    results.append(_check_helper_detection())

    if pm is None:
        try:
            pm = PackageManager()
        except PackageManagerError as exc:
            results.append(DiagnosticResult('backend_init', STATUS_FAIL, str(exc)))
            pm = None

    if pm is not None:
        if pm.helper_info() != 'pacman':
            results.append(_binary_check(pm.helper_info()))
        results.append(_check_live_parse(pm))
        results.append(_check_live_info(pm))
        results.append(_check_polkit_policy(shutil.which(pm.helper_info())))

    results.append(_check_lock())
    results.append(_check_disk_space())
    return results


def format_report(results: List[DiagnosticResult]) -> str:
    """Plain-text report for CLI/log output."""
    tags = {STATUS_PASS: 'PASS', STATUS_WARN: 'WARN', STATUS_FAIL: 'FAIL'}
    counts = {STATUS_PASS: 0, STATUS_WARN: 0, STATUS_FAIL: 0}
    lines = []
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
        lines.append(f'[{tags.get(r.status, r.status.upper())}] {r.name}: {r.message}')
    lines.append('')
    lines.append(f"{counts[STATUS_PASS]} passed, {counts[STATUS_WARN]} warning(s), {counts[STATUS_FAIL]} failed")
    return '\n'.join(lines)


def has_failures(results: List[DiagnosticResult]) -> bool:
    """Used for the CLI flag's process exit code."""
    return any(r.status == STATUS_FAIL for r in results)
