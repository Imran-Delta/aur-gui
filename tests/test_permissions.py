import unittest
from unittest import mock

from pkgmanager import permissions
from pkgmanager.exceptions import CommandFailedError, HelperNotFoundError, PermissionDeniedError


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


if __name__ == '__main__':
    unittest.main()
