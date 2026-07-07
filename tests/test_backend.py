import unittest
from unittest import mock

from pkgmanager.backend import PackageManager
from pkgmanager.exceptions import AURHelperMissingError


def make_manager(helper='yay', use_noconfirm=True):
    with mock.patch('pkgmanager.backend.detect_helper', return_value=helper):
        return PackageManager(use_noconfirm=use_noconfirm)


class TestBuildCommand(unittest.TestCase):
    def test_search_substitutes_query(self):
        pm = make_manager('yay')
        cmd = pm._build_command('search', query='firefox')
        self.assertEqual(cmd, ['yay', '-Ss', 'firefox'])

    def test_install_expands_packages_as_separate_args(self):
        pm = make_manager('yay')
        cmd = pm._build_command('install', packages=['firefox', 'vim'])
        self.assertEqual(cmd, ['yay', '-S', '--needed', '--noconfirm', 'firefox', 'vim'])

    def test_noconfirm_dropped_when_disabled(self):
        pm = make_manager('yay', use_noconfirm=False)
        cmd = pm._build_command('install', packages=['firefox'])
        self.assertNotIn('--noconfirm', cmd)

    def test_unsupported_operation_raises(self):
        pm = make_manager('pacman')
        with self.assertRaises(Exception):
            pm._build_command('download_pkgbuild', package='firefox')


class TestInstall(unittest.TestCase):
    def test_known_aur_without_helper_raises_immediately(self):
        pm = make_manager('pacman')  # supports_aur() is False
        with mock.patch.object(pm, '_run') as run:
            with self.assertRaises(AURHelperMissingError):
                pm.install(['some-aur-only-pkg'], known_aur=True)
            run.assert_not_called()

    def test_regular_install_runs_command_and_streams_to_callback(self):
        pm = make_manager('yay')
        lines_seen = []
        with mock.patch.object(pm, '_run', return_value=iter(['downloading...', 'installed'])) as run:
            pm.install(['firefox'], callback=lines_seen.append)
        run.assert_called_once()
        self.assertEqual(lines_seen, ['downloading...', 'installed'])

    def test_empty_package_list_is_a_no_op(self):
        pm = make_manager('yay')
        with mock.patch.object(pm, '_run') as run:
            pm.install([])
        run.assert_not_called()


class TestUpdate(unittest.TestCase):
    def test_pikaur_runs_two_commands_in_sequence(self):
        pm = make_manager('pikaur')
        seen_cmds = []

        def fake_run(cmd, stream=False, operation=None):
            seen_cmds.append(cmd)
            return iter([])

        with mock.patch.object(pm, '_run', side_effect=fake_run):
            pm.update()

        self.assertEqual(seen_cmds, [
            ['pikaur', '-Sy', '--noconfirm'],
            ['pikaur', '-Su', '--noconfirm'],
        ])

    def test_yay_runs_single_command(self):
        pm = make_manager('yay')
        with mock.patch.object(pm, '_run', return_value=iter([])) as run:
            pm.update()
        run.assert_called_once()
        args, _kwargs = run.call_args
        self.assertEqual(args[0], ['yay', '-Syu', '--noconfirm'])


class TestSupportsAur(unittest.TestCase):
    def test_yay_supports_aur(self):
        self.assertTrue(make_manager('yay').supports_aur())

    def test_pacman_does_not_support_aur(self):
        self.assertFalse(make_manager('pacman').supports_aur())


class TestRefresh(unittest.TestCase):
    def test_builds_sync_only_command(self):
        pm = make_manager('yay')
        with mock.patch.object(pm, '_run', return_value=iter([])) as run:
            pm.refresh()
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['yay', '-Sy', '--noconfirm'])
        self.assertEqual(kwargs.get('operation'), 'refresh')


class TestListUpgradable(unittest.TestCase):
    def test_uses_qu_and_parses_result(self):
        pm = make_manager('yay')
        with mock.patch.object(pm, '_run', return_value='firefox 120.0.1-1 -> 121.0-1\n') as run:
            packages = pm.list_upgradable()
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['yay', '-Qu'])
        self.assertEqual(kwargs.get('stream'), False)
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0].new_version, '121.0-1')


class TestListRepoPackages(unittest.TestCase):
    def test_always_uses_pacman_directly_even_under_yay(self):
        pm = make_manager('yay')
        with mock.patch.object(pm, '_run', return_value='core acl 2.3.2-1\n') as run:
            packages = pm.list_repo_packages('core')
        args, _kwargs = run.call_args
        self.assertEqual(args[0], ['pacman', '-Sl', 'core'])
        self.assertEqual(packages[0].repository, 'core')


if __name__ == '__main__':
    unittest.main()
