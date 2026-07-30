import unittest
from unittest import mock

from pkgmanager.backend import PackageManager
from pkgmanager.exceptions import AURHelperMissingError, CommandFailedError


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

        def fake_run(cmd, stream=False, operation=None, pid_callback=None):
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


class TestInfoMany(unittest.TestCase):
    SI_TWO_PACKAGES = (
        "Name             : firefox\n"
        "Version          : 121.0-1\n"
        "Description      : Web browser\n"
        "Repository       : extra\n"
        "Download Size    : 55.00 MiB\n"
        "Installed Size   : 200.00 MiB\n"
        "\n"
        "Name             : vim\n"
        "Version          : 9.0.2100-1\n"
        "Description      : Vi Improved\n"
        "Repository       : extra\n"
        "Download Size    : 2.05 MiB\n"
        "Installed Size   : 3.45 MiB\n"
    )

    def test_empty_list_is_a_no_op(self):
        pm = make_manager('yay')
        with mock.patch.object(pm, '_run') as run:
            self.assertEqual(pm.info_many([]), [])
        run.assert_not_called()

    def test_single_batched_call_for_multiple_packages(self):
        pm = make_manager('yay')
        with mock.patch.object(pm, '_run', return_value=self.SI_TWO_PACKAGES) as run:
            details = pm.info_many(['firefox', 'vim'], local=False)
        run.assert_called_once()  # one subprocess call, not one per package
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['yay', '-Si', 'firefox', 'vim'])
        self.assertEqual(len(details), 2)
        self.assertEqual({d.name for d in details}, {'firefox', 'vim'})

    def test_falls_back_to_per_package_on_batch_failure(self):
        pm = make_manager('yay')
        call_log = []

        def fake_run(cmd, stream=False, operation=None, pid_callback=None):
            call_log.append(cmd)
            if operation == 'info_remote_many':
                raise CommandFailedError(cmd, 1, '', 'error: package not found')
            # per-package fallback path
            name = cmd[-1]
            return f"Name             : {name}\nVersion          : 1.0-1\nDescription      : x\nRepository       : extra\n"

        with mock.patch.object(pm, '_run', side_effect=fake_run):
            details = pm.info_many(['firefox', 'nonexistent-pkg'], local=False)

        # batched call attempted first, then one fallback call per package
        self.assertEqual(len(call_log), 3)
        self.assertEqual(len(details), 2)

    def test_fallback_drops_packages_that_still_fail(self):
        pm = make_manager('yay')

        def fake_run(cmd, stream=False, operation=None, pid_callback=None):
            if operation == 'info_remote_many':
                raise CommandFailedError(cmd, 1, '', '')
            name = cmd[-1]
            if name == 'nonexistent-pkg':
                raise CommandFailedError(cmd, 1, '', 'error: target not found')
            return "Name             : firefox\nVersion          : 1.0-1\nDescription      : x\nRepository       : extra\n"

        with mock.patch.object(pm, '_run', side_effect=fake_run):
            details = pm.info_many(['firefox', 'nonexistent-pkg'], local=False)

        self.assertEqual(len(details), 1)
        self.assertEqual(details[0].name, 'firefox')


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
