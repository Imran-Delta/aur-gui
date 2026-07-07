import unittest
from unittest import mock

from pkgmanager.exceptions import HelperNotFoundError, NoHelperError
from pkgmanager.helpers import (
    detect_helper,
    parse_installed_output,
    parse_info_output,
    parse_repo_listing_output,
    parse_search_output,
    parse_upgradable_output,
)

SEARCH_SAMPLE = (
    "core/linux 6.6.8.arch1-1 (base) [installed]\n"
    "    The Linux kernel and modules\n"
    "\n"
    "extra/vim 9.0.2100-1\n"
    "    Vi Improved, a highly configurable, improved version of the vi text editor\n"
    "\n"
    "aur/google-chrome 126.0.6478.126-1\n"
    "    The popular and trusted web browser by Google\n"
)

QI_SAMPLE = (
    "Name            : vim\n"
    "Version         : 9.0.2100-1\n"
    "Description     : Vi Improved, a highly configurable, improved version of the vi text editor\n"
    "Architecture    : x86_64\n"
    "URL             : https://www.vim.org\n"
    "Licenses        : custom:vim  GPL2\n"
    "Groups          : None\n"
    "Provides        : vi\n"
    "Depends On      : gpm  vim-runtime  libsodium.so=23-64\n"
    "Optional Deps   : python3: Python 3 language support\n"
    "                   ruby: Ruby language support\n"
    "Required By     : None\n"
    "Optional For    : None\n"
    "Conflicts With  : vi\n"
    "Replaces        : vi\n"
    "Installed Size  : 3.45 MiB\n"
    "Install Date    : Tue 02 Jan 2024 09:30:00 AM UTC\n"
    "Install Reason  : Explicitly installed\n"
    "Install Script  : No\n"
    "Validated By    : Signature\n"
)

SI_AUR_SAMPLE = (
    "Repository      : aur\n"
    "Name             : google-chrome\n"
    "Version          : 126.0.6478.126-1\n"
    "Description      : The popular and trusted web browser by Google\n"
    "URL              : https://www.google.com/chrome\n"
    "Licenses         : custom\n"
    "Depends On       : gtk3  libxss\n"
    "Optional Deps    : None\n"
    "Maintainer       : someuser\n"
    "Votes            : 1234\n"
    "Popularity       : 12.34\n"
    "Out-of-date      : No\n"
)

INSTALLED_SAMPLE = "firefox 120.0.1-1\nvim 9.0.2100-1\ngoogle-chrome 126.0.6478.126-1\n"

UPGRADABLE_SAMPLE = "firefox 120.0.1-1 -> 121.0-1\nvim 9.0.2100-1 -> 9.0.2110-1\n"

REPO_LISTING_SAMPLE = (
    "core linux 6.6.8.arch1-1\n"
    "core acl 2.3.2-1 [installed]\n"
    "extra vim 9.0.2100-1 [installed]\n"
)


class TestParseSearchOutput(unittest.TestCase):
    def test_parses_all_entries(self):
        self.assertEqual(len(parse_search_output(SEARCH_SAMPLE)), 3)

    def test_marks_installed_from_bracket_tag(self):
        packages = parse_search_output(SEARCH_SAMPLE)
        linux = next(p for p in packages if p.name == 'linux')
        self.assertTrue(linux.installed)
        self.assertEqual(linux.version, '6.6.8.arch1-1')

    def test_marks_non_installed(self):
        packages = parse_search_output(SEARCH_SAMPLE)
        vim = next(p for p in packages if p.name == 'vim')
        self.assertFalse(vim.installed)

    def test_flags_aur_repo(self):
        packages = parse_search_output(SEARCH_SAMPLE)
        chrome = next(p for p in packages if p.name == 'google-chrome')
        self.assertTrue(chrome.is_aur)
        self.assertEqual(chrome.repository, 'aur')

    def test_empty_input(self):
        self.assertEqual(parse_search_output(''), [])
        self.assertEqual(parse_search_output(None), [])


class TestParseInfoOutput(unittest.TestCase):
    def test_local_info_basic_fields(self):
        detail = parse_info_output(QI_SAMPLE)
        self.assertEqual(detail.name, 'vim')
        self.assertEqual(detail.version, '9.0.2100-1')
        self.assertIn('vi text editor', detail.description)

    def test_depends_parsed_as_list(self):
        detail = parse_info_output(QI_SAMPLE)
        self.assertIn('gpm', detail.depends)
        self.assertIn('vim-runtime', detail.depends)

    def test_optional_deps_continuation_lines(self):
        detail = parse_info_output(QI_SAMPLE)
        self.assertTrue(any('python3' in d for d in detail.optional_deps))
        self.assertTrue(any('ruby' in d for d in detail.optional_deps))

    def test_size_mapped_from_installed_size(self):
        detail = parse_info_output(QI_SAMPLE)
        self.assertEqual(detail.size, '3.45 MiB')

    def test_aur_remote_fields(self):
        detail = parse_info_output(SI_AUR_SAMPLE)
        self.assertEqual(detail.repository, 'aur')
        self.assertEqual(detail.maintainer, 'someuser')
        self.assertEqual(detail.votes, 1234)
        self.assertAlmostEqual(detail.popularity, 12.34)
        self.assertFalse(detail.out_of_date)


class TestParseInstalledOutput(unittest.TestCase):
    def test_parses_plain_name_version_lines(self):
        packages = parse_installed_output(INSTALLED_SAMPLE)
        self.assertEqual(len(packages), 3)
        self.assertEqual({p.name for p in packages}, {'firefox', 'vim', 'google-chrome'})

    def test_defaults_repository_to_local(self):
        packages = parse_installed_output(INSTALLED_SAMPLE)
        self.assertTrue(all(p.repository == 'local' for p in packages))
        self.assertTrue(all(p.installed for p in packages))

    def test_accepts_optional_repo_prefix(self):
        packages = parse_installed_output('aur/yay-bin 12.3.4-1\n')
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0].repository, 'aur')
        self.assertTrue(packages[0].is_aur)


class TestDetectHelper(unittest.TestCase):
    @mock.patch('pkgmanager.helpers.shutil.which')
    def test_prefers_yay_when_present(self, which):
        which.side_effect = lambda name: '/usr/bin/yay' if name == 'yay' else None
        self.assertEqual(detect_helper(), 'yay')

    @mock.patch('pkgmanager.helpers.shutil.which')
    def test_falls_back_to_pacman(self, which):
        which.side_effect = lambda name: '/usr/bin/pacman' if name == 'pacman' else None
        self.assertEqual(detect_helper(), 'pacman')

    @mock.patch('pkgmanager.helpers.shutil.which')
    def test_raises_when_nothing_found(self, which):
        which.return_value = None
        with self.assertRaises(NoHelperError):
            detect_helper()

    @mock.patch('pkgmanager.helpers.shutil.which')
    def test_forced_unknown_helper_raises_instead_of_masquerading_as_pacman(self, which):
        which.return_value = '/usr/bin/pacman'
        with self.assertRaises(HelperNotFoundError):
            detect_helper(force_helper='pacaur')

    @mock.patch('pkgmanager.helpers.shutil.which')
    def test_forced_helper_not_installed_raises(self, which):
        which.return_value = None
        with self.assertRaises(HelperNotFoundError):
            detect_helper(force_helper='paru')


class TestParseUpgradableOutput(unittest.TestCase):
    def test_parses_current_and_new_version(self):
        packages = parse_upgradable_output(UPGRADABLE_SAMPLE)
        self.assertEqual(len(packages), 2)
        firefox = next(p for p in packages if p.name == 'firefox')
        self.assertEqual(firefox.version, '120.0.1-1')
        self.assertEqual(firefox.new_version, '121.0-1')
        self.assertTrue(firefox.installed)

    def test_empty_input(self):
        self.assertEqual(parse_upgradable_output(''), [])
        self.assertEqual(parse_upgradable_output(None), [])


class TestParseRepoListingOutput(unittest.TestCase):
    def test_parses_space_separated_entries(self):
        packages = parse_repo_listing_output(REPO_LISTING_SAMPLE)
        self.assertEqual(len(packages), 3)
        self.assertTrue(all(p.repository == 'core' or p.repository == 'extra' for p in packages))

    def test_marks_installed_from_bracket_tag(self):
        packages = parse_repo_listing_output(REPO_LISTING_SAMPLE)
        acl = next(p for p in packages if p.name == 'acl')
        linux = next(p for p in packages if p.name == 'linux')
        self.assertTrue(acl.installed)
        self.assertFalse(linux.installed)

    def test_never_flags_aur(self):
        packages = parse_repo_listing_output(REPO_LISTING_SAMPLE)
        self.assertTrue(all(not p.is_aur for p in packages))


if __name__ == '__main__':
    unittest.main()
