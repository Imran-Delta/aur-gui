import os
import stat
import tempfile
import unittest
from unittest import mock

from pkgmanager.exceptions import PermissionDeniedError
from pkgmanager.models import PackageDetail
from pkgmanager.preflight import (
    HookResult,
    LockState,
    check_stale_lock,
    estimate_required_space,
    remove_stale_lock,
    run_preflight_hook,
)


def _write_fake_proc(root, processes):
    for pid, (comm, state, ppid) in processes.items():
        d = os.path.join(root, str(pid))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'stat'), 'w') as fh:
            fh.write(f"{pid} ({comm}) {state} {ppid} 0 0 0 0 0\n")


def _write_script(path, contents):
    with open(path, 'w') as fh:
        fh.write(contents)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


class TestCheckStaleLock(unittest.TestCase):
    def test_no_lock_file(self):
        with tempfile.TemporaryDirectory() as d:
            lock_path = os.path.join(d, 'db.lck')
            self.assertEqual(check_stale_lock(lock_path, proc_root=d), LockState.NONE)

    def test_lock_present_pacman_not_running_is_stale(self):
        with tempfile.TemporaryDirectory() as d:
            lock_path = os.path.join(d, 'db.lck')
            open(lock_path, 'w').close()
            _write_fake_proc(d, {999: ('unrelated', 'S', 1)})
            self.assertEqual(check_stale_lock(lock_path, proc_root=d), LockState.STALE)

    def test_lock_present_pacman_running_is_held(self):
        with tempfile.TemporaryDirectory() as d:
            lock_path = os.path.join(d, 'db.lck')
            open(lock_path, 'w').close()
            _write_fake_proc(d, {500: ('pacman', 'R', 1)})
            self.assertEqual(check_stale_lock(lock_path, proc_root=d), LockState.HELD)


class TestRemoveStaleLock(unittest.TestCase):
    @mock.patch('pkgmanager.preflight._run_elevated')
    def test_success_returns_true(self, run):
        run.return_value = ''
        self.assertTrue(remove_stale_lock('/var/lib/pacman/db.lck'))
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['rm', '-f', '/var/lib/pacman/db.lck'])
        self.assertEqual(kwargs.get('operation'), 'remove_lock')

    @mock.patch('pkgmanager.preflight._run_elevated')
    def test_elevation_failure_returns_false_not_raise(self, run):
        run.side_effect = PermissionDeniedError("pkexec dismissed")
        self.assertFalse(remove_stale_lock('/var/lib/pacman/db.lck'))


class TestEstimateRequiredSpace(unittest.TestCase):
    def _detail(self, name, download=None, installed=None):
        return PackageDetail(name=name, version='1.0', description='', repository='extra',
                              download_size=download, installed_size=installed)

    @mock.patch('pkgmanager.preflight.shutil.disk_usage')
    def test_sums_download_and_installed_not_max(self, disk_usage):
        disk_usage.return_value = mock.Mock(free=100 * 1024 ** 3)
        details = [self._detail('a', '10.00 MiB', '20.00 MiB')]
        result = estimate_required_space(details, buffer_bytes=0)
        expected = round(10 * 1024 ** 2) + round(20 * 1024 ** 2)
        self.assertEqual(result.required_bytes, expected)

    @mock.patch('pkgmanager.preflight.shutil.disk_usage')
    def test_buffer_is_added(self, disk_usage):
        disk_usage.return_value = mock.Mock(free=100 * 1024 ** 3)
        details = [self._detail('a', '0.00 B', '0.00 B')]
        result = estimate_required_space(details, buffer_bytes=1024)
        self.assertEqual(result.required_bytes, 1024)

    @mock.patch('pkgmanager.preflight.shutil.disk_usage')
    def test_unparseable_size_tracked_as_unknown_not_silently_zero(self, disk_usage):
        disk_usage.return_value = mock.Mock(free=100 * 1024 ** 3)
        details = [self._detail('mystery-pkg', None, None)]
        result = estimate_required_space(details, buffer_bytes=0)
        self.assertEqual(result.unknown_packages, ['mystery-pkg'])
        self.assertEqual(result.required_bytes, 0)

    @mock.patch('pkgmanager.preflight.shutil.disk_usage')
    def test_has_enough_reflects_free_space(self, disk_usage):
        disk_usage.return_value = mock.Mock(free=500)
        details = [self._detail('a', '0.00 B', '1.00 KiB')]
        result = estimate_required_space(details, buffer_bytes=0)
        self.assertFalse(result.has_enough)
        self.assertEqual(result.free_bytes, 500)


class TestRunPreflightHook(unittest.TestCase):
    def test_missing_hook_is_skipped_not_a_failure(self):
        result = run_preflight_hook('/definitely/not/a/real/path', timeout=1)
        self.assertFalse(result.ran)
        self.assertTrue(result.ok)

    def test_non_executable_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'hook')
            open(path, 'w').close()  # exists, but not chmod +x
            result = run_preflight_hook(path, timeout=1)
        self.assertFalse(result.ran)
        self.assertTrue(result.ok)

    def test_successful_hook(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'hook')
            _write_script(path, "#!/bin/sh\nexit 0\n")
            result = run_preflight_hook(path, timeout=5)
        self.assertTrue(result.ran)
        self.assertTrue(result.ok)

    def test_failing_hook_reports_stderr(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'hook')
            _write_script(path, "#!/bin/sh\necho 'partition is dirty' >&2\nexit 1\n")
            result = run_preflight_hook(path, timeout=5)
        self.assertTrue(result.ran)
        self.assertFalse(result.ok)
        self.assertIn('dirty', result.stderr)

    def test_hung_hook_times_out_instead_of_blocking_forever(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'hook')
            _write_script(path, "#!/bin/sh\nsleep 30\n")
            result = run_preflight_hook(path, timeout=0.2)
        self.assertTrue(result.ran)
        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)


if __name__ == '__main__':
    unittest.main()
