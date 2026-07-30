import os
import tempfile
import unittest
from unittest import mock

from pkgmanager.diagnostics import (
    DiagnosticResult,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_WARN,
    format_report,
    has_failures,
    run_diagnostics,
)
from pkgmanager.exceptions import CommandFailedError, NoHelperError
from pkgmanager.models import Package, PackageDetail


def make_pm(helper='pacman'):
    pm = mock.Mock()
    pm.helper_info.return_value = helper
    return pm


class TestRunDiagnostics(unittest.TestCase):
    @mock.patch('pkgmanager.diagnostics._check_polkit_policy')
    @mock.patch('pkgmanager.diagnostics._check_disk_space')
    @mock.patch('pkgmanager.diagnostics._check_lock')
    @mock.patch('pkgmanager.diagnostics.shutil.which')
    @mock.patch('pkgmanager.diagnostics.detect_helper')
    @mock.patch('pkgmanager.diagnostics.PackageManager')
    def test_reports_failure_when_no_backend_available(self, pm_cls, detect_helper, which,
                                                          check_lock, check_disk, check_polkit):
        # Simulates a machine with no pacman/helper at all -- should not
        # raise, should still return a full report with the gap flagged.
        pm_cls.side_effect = NoHelperError("no supported package manager found")
        detect_helper.side_effect = NoHelperError("no supported package manager found")
        which.return_value = None
        check_lock.return_value = DiagnosticResult('lock_state', STATUS_PASS, 'no lock file')
        check_disk.return_value = DiagnosticResult('disk_space', STATUS_PASS, 'plenty')
        results = run_diagnostics()
        self.assertTrue(has_failures(results))
        names = {r.name for r in results}
        self.assertIn('backend_init', names)

    @mock.patch('pkgmanager.diagnostics._check_polkit_policy')
    @mock.patch('pkgmanager.diagnostics._check_disk_space')
    @mock.patch('pkgmanager.diagnostics._check_lock')
    @mock.patch('pkgmanager.diagnostics.shutil.which')
    @mock.patch('pkgmanager.diagnostics.detect_helper', return_value='pacman')
    def test_healthy_system_reports_no_failures(self, detect_helper, which, check_lock,
                                                   check_disk, check_polkit):
        which.side_effect = lambda name: f'/usr/bin/{name}'
        check_lock.return_value = DiagnosticResult('lock_state', STATUS_PASS, 'no lock file')
        check_disk.return_value = DiagnosticResult('disk_space', STATUS_PASS, 'plenty')
        check_polkit.return_value = DiagnosticResult('polkit_policy', STATUS_WARN, 'none found')

        pm = make_pm('pacman')
        pm.list_installed.return_value = [
            Package(name='pacman', version='6.0.1-1', description='package manager', repository='core',
                    installed=True)
        ]
        pm.info.return_value = PackageDetail(name='pacman', version='6.0.1-1', description='',
                                              repository='core')

        results = run_diagnostics(pm=pm)
        self.assertFalse(has_failures(results))

    @mock.patch('pkgmanager.diagnostics._check_polkit_policy')
    @mock.patch('pkgmanager.diagnostics._check_disk_space')
    @mock.patch('pkgmanager.diagnostics._check_lock')
    @mock.patch('pkgmanager.diagnostics.shutil.which')
    @mock.patch('pkgmanager.diagnostics.detect_helper', return_value='pacman')
    def test_broken_parser_on_real_output_is_a_failure_not_silent(self, detect_helper, which,
                                                                     check_lock, check_disk, check_polkit):
        which.side_effect = lambda name: f'/usr/bin/{name}'
        check_lock.return_value = DiagnosticResult('lock_state', STATUS_PASS, 'no lock file')
        check_disk.return_value = DiagnosticResult('disk_space', STATUS_PASS, 'plenty')
        check_polkit.return_value = DiagnosticResult('polkit_policy', STATUS_WARN, 'none found')

        pm = make_pm('pacman')
        pm.list_installed.side_effect = CommandFailedError(['pacman', '-Q'], 1, '', 'unexpected format')
        pm.info.return_value = PackageDetail(name='pacman', version='6.0.1-1', description='',
                                              repository='core')

        results = run_diagnostics(pm=pm)
        self.assertTrue(has_failures(results))


class TestFormatReport(unittest.TestCase):
    def test_counts_and_tags(self):
        results = [
            DiagnosticResult('a', STATUS_PASS, 'ok'),
            DiagnosticResult('b', STATUS_WARN, 'hmm'),
            DiagnosticResult('c', STATUS_FAIL, 'broken'),
        ]
        report = format_report(results)
        self.assertIn('[PASS] a: ok', report)
        self.assertIn('[WARN] b: hmm', report)
        self.assertIn('[FAIL] c: broken', report)
        self.assertIn('1 passed, 1 warning(s), 1 failed', report)


class TestHasFailures(unittest.TestCase):
    def test_true_when_any_fail_present(self):
        results = [DiagnosticResult('a', STATUS_PASS, ''), DiagnosticResult('b', STATUS_FAIL, '')]
        self.assertTrue(has_failures(results))

    def test_false_when_only_pass_and_warn(self):
        results = [DiagnosticResult('a', STATUS_PASS, ''), DiagnosticResult('b', STATUS_WARN, '')]
        self.assertFalse(has_failures(results))


if __name__ == '__main__':
    unittest.main()
