import os
import tempfile
import unittest
from unittest import mock

from pkgmanager import permissions
from pkgmanager.exceptions import CommandFailedError, HelperNotFoundError, PermissionDeniedError


def _write_fake_proc(root, processes):
    """processes: {pid: (comm, state, ppid)}. Mirrors the real
    /proc/[pid]/stat layout: 'pid (comm) state ppid ...'."""
    for pid, (comm, state, ppid) in processes.items():
        d = os.path.join(root, str(pid))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'stat'), 'w') as fh:
            fh.write(f"{pid} ({comm}) {state} {ppid} 0 0 0 0 0\n")


class TestElevation(unittest.TestCase):
    @mock.patch('pkgmanager.permissions.shutil.which')
    def test_read_only_operation_not_elevated(self, which):
        which.side_effect = lambda name: f'/usr/bin/{name}'
        cmd = permissions._elevate(['pacman', '-Ss', 'vim'], use_pkexec=None, operation='search')
        self.assertEqual(cmd, ['pacman', '-Ss', 'vim'])

    @mock.patch('pkgmanager.permissions.shutil.which')
    @mock.patch('pkgmanager.permissions.is_gui_environment', return_value=True)
    def test_root_operation_in_gui_uses_pkexec(self, _is_gui, which):
        which.side_effect = lambda name: f'/usr/bin/{name}'
        cmd = permissions._elevate(['pacman', '-S', 'vim'], use_pkexec=None, operation='install')
        self.assertEqual(cmd[0], 'pkexec')

    @mock.patch('pkgmanager.permissions.shutil.which')
    @mock.patch('pkgmanager.permissions.is_gui_environment', return_value=False)
    def test_root_operation_headless_uses_sudo(self, _is_gui, which):
        which.side_effect = lambda name: f'/usr/bin/{name}'
        cmd = permissions._elevate(['pacman', '-S', 'vim'], use_pkexec=None, operation='install')
        self.assertEqual(cmd[0], 'sudo')

    @mock.patch('pkgmanager.permissions.shutil.which', return_value=None)
    def test_missing_pkexec_raises(self, _which):
        with self.assertRaises(PermissionDeniedError):
            permissions._elevate(['pacman', '-S', 'vim'], use_pkexec=True, operation='install')

    @mock.patch('pkgmanager.permissions.shutil.which')
    def test_refresh_requires_elevation(self, which):
        which.side_effect = lambda name: f'/usr/bin/{name}'
        cmd = permissions._elevate(['pacman', '-Sy', '--noconfirm'], use_pkexec=True, operation='refresh')
        self.assertEqual(cmd[0], 'pkexec')

    @mock.patch('pkgmanager.permissions.shutil.which')
    def test_list_upgradable_and_list_repo_stay_unprivileged(self, which):
        which.side_effect = lambda name: f'/usr/bin/{name}'
        for op in ('list_upgradable', 'list_repo'):
            cmd = permissions._elevate(['pacman', '-Qu'], use_pkexec=True, operation=op)
            self.assertEqual(cmd, ['pacman', '-Qu'])


class TestRun(unittest.TestCase):
    @mock.patch('pkgmanager.permissions.shutil.which', return_value=None)
    def test_missing_binary_raises_helper_not_found(self, _which):
        with self.assertRaises(HelperNotFoundError):
            permissions.run(['definitely-not-a-real-binary', '-Ss', 'vim'], operation='search')

    @mock.patch('pkgmanager.permissions.subprocess.Popen')
    @mock.patch('pkgmanager.permissions.shutil.which', return_value='/usr/bin/pacman')
    def test_nonzero_exit_raises_command_failed(self, _which, popen):
        process = mock.Mock()
        process.stdout = mock.Mock()
        process.stdout.read.return_value = 'error: target not found: nope\n'
        process.wait.return_value = 1
        popen.return_value = process

        with self.assertRaises(CommandFailedError):
            permissions.run(['pacman', '-Ss', 'nope'], operation='search')

    @mock.patch('pkgmanager.permissions.subprocess.Popen')
    @mock.patch('pkgmanager.permissions.shutil.which', return_value='/usr/bin/pacman')
    def test_streaming_yields_lines_then_raises_on_failure(self, _which, popen):
        process = mock.Mock()
        process.stdout = iter(['line one\n', 'line two\n'])
        process.wait.return_value = 1
        popen.return_value = process

        gen = permissions.run(['pacman', '-S', 'nope'], stream=True, operation='install')
        seen = []
        with self.assertRaises(CommandFailedError):
            for line in gen:
                seen.append(line)
        self.assertEqual(seen, ['line one', 'line two'])

    @mock.patch('pkgmanager.permissions.subprocess.Popen')
    @mock.patch('pkgmanager.permissions.shutil.which', return_value='/usr/bin/pacman')
    def test_never_uses_shell(self, _which, popen):
        process = mock.Mock()
        process.stdout = mock.Mock()
        process.stdout.read.return_value = ''
        process.wait.return_value = 0
        popen.return_value = process

        permissions.run(['pacman', '-Ss', 'vim'], operation='search')

        _args, kwargs = popen.call_args
        self.assertFalse(kwargs.get('shell'))


class TestListDescendantPids(unittest.TestCase):
    def test_finds_direct_and_transitive_children(self):
        # pkexec(100) -> yay(200) -> pacman(300): the exact chain that makes
        # watching Popen's own PID (100) useless for I/O-wait purposes.
        with tempfile.TemporaryDirectory() as root:
            _write_fake_proc(root, {
                100: ('pkexec', 'S', 1),
                200: ('yay', 'S', 100),
                300: ('pacman', 'D', 200),
                999: ('unrelated', 'S', 1),
            })
            pids = permissions.list_descendant_pids(100, proc_root=root)
        self.assertEqual(pids, {100, 200, 300})

    def test_root_not_alive_returns_empty(self):
        with tempfile.TemporaryDirectory() as root:
            _write_fake_proc(root, {999: ('unrelated', 'S', 1)})
            pids = permissions.list_descendant_pids(100, proc_root=root)
        self.assertEqual(pids, set())

    def test_comm_with_spaces_does_not_break_parsing(self):
        # comm is parenthesized specifically because it can contain spaces;
        # parsing must split after the *last* ')', not the first.
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, '100'))
            with open(os.path.join(root, '100', 'stat'), 'w') as fh:
                fh.write("100 (some (weird) name) D 1 0 0 0 0 0\n")
            pids = permissions.list_descendant_pids(100, proc_root=root)
        self.assertEqual(pids, {100})


class TestAnyProcessIoWait(unittest.TestCase):
    def test_true_when_any_pid_in_d_state(self):
        with tempfile.TemporaryDirectory() as root:
            _write_fake_proc(root, {100: ('pkexec', 'S', 1), 300: ('pacman', 'D', 100)})
            self.assertTrue(permissions.any_process_io_wait({100, 300}, proc_root=root))

    def test_false_when_none_in_d_state(self):
        with tempfile.TemporaryDirectory() as root:
            _write_fake_proc(root, {100: ('pkexec', 'S', 1), 300: ('pacman', 'R', 100)})
            self.assertFalse(permissions.any_process_io_wait({100, 300}, proc_root=root))

    def test_watching_only_the_wrapper_pid_would_miss_it(self):
        # Demonstrates the exact bug: pkexec (100) is sleeping while its
        # child pacman (300) does I/O. Checking only {100} misses it;
        # checking the full descendant set catches it.
        with tempfile.TemporaryDirectory() as root:
            _write_fake_proc(root, {100: ('pkexec', 'S', 1), 300: ('pacman', 'D', 100)})
            self.assertFalse(permissions.any_process_io_wait({100}, proc_root=root))
            full_tree = permissions.list_descendant_pids(100, proc_root=root)
            self.assertTrue(permissions.any_process_io_wait(full_tree, proc_root=root))


class TestWatchIoWait(unittest.TestCase):
    def test_returns_once_process_tree_exits(self):
        with tempfile.TemporaryDirectory() as root:
            _write_fake_proc(root, {100: ('pacman', 'R', 1)})
            changes = []
            # No process ever appears in this fake /proc for pid 100 after
            # the first check removes it, so the loop should return quickly
            # rather than spinning forever.
            import shutil as _shutil
            _shutil.rmtree(os.path.join(root, '100'))
            permissions.watch_io_wait(100, changes.append, poll_interval=0.01, proc_root=root)
        self.assertEqual(changes, [])

    def test_announces_final_false_if_tree_vanishes_mid_wait(self):
        # If the whole tree disappears while the last known state was
        # "waiting", the caller (a GUI status label) needs one last False,
        # or it's stuck showing "Waiting for disk I/O..." forever.
        with tempfile.TemporaryDirectory() as root:
            _write_fake_proc(root, {100: ('pacman', 'D', 1)})
            changes = []
            calls = {'n': 0}
            real_list = permissions.list_descendant_pids

            def fake_list(pid, proc_root):
                calls['n'] += 1
                if calls['n'] == 1:
                    return real_list(pid, proc_root)
                return set()  # tree gone on the second poll

            with mock.patch('pkgmanager.permissions.list_descendant_pids', side_effect=fake_list):
                permissions.watch_io_wait(100, changes.append, poll_interval=0.01, proc_root=root)
            self.assertEqual(changes, [True, False])


class TestRunPidCallback(unittest.TestCase):
    @mock.patch('pkgmanager.permissions.subprocess.Popen')
    @mock.patch('pkgmanager.permissions.shutil.which', return_value='/usr/bin/pacman')
    def test_pid_callback_fires_with_process_pid(self, _which, popen):
        process = mock.Mock()
        process.pid = 4242
        process.stdout = mock.Mock()
        process.stdout.read.return_value = ''
        process.wait.return_value = 0
        popen.return_value = process

        seen = []
        permissions.run(['pacman', '-Ss', 'vim'], operation='search', pid_callback=seen.append)
        self.assertEqual(seen, [4242])


if __name__ == '__main__':
    unittest.main()
