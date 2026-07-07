# pkgmanager

A single, reliable Python interface for package management on Arch Linux —
official repos via `pacman`, and the AUR via whichever helper (`yay`, `paru`,
`trizen`, `pikaur`) is installed. A lighter, AUR-helper-agnostic backend in
the spirit of `pamac`.

## Install

```bash
pip install -e .
```

## Quick start

```python
from pkgmanager import PackageManager

pm = PackageManager()
print(pm.helper_info(), pm.supports_aur())

for pkg in pm.search("firefox"):
    print(pkg.repository, pkg.name, pkg.version, "[installed]" if pkg.installed else "")

pm.install(["firefox"], callback=print)   # streams pacman/helper output line by line
detail = pm.info("firefox")
print(detail.depends, detail.license)
```

Every method is safe to call from a background thread — nothing touches UI
state, and `install`/`remove`/`update`/`refresh` stream output via callback
so a Tkinter/Qt window can update a log widget live instead of blocking.

```python
pm.refresh(callback=print)          # sync package databases (-Sy)
for pkg in pm.list_upgradable():    # installed packages with a newer version
    print(pkg.name, pkg.version, "->", pkg.new_version)
for pkg in pm.list_repo_packages("extra"):  # every package in one official repo
    print(pkg.name, pkg.version)
```

## GUI

`pkgmanager_gui.pyw` is a tabbed Tkinter front-end (Search/Gallery, Installed,
Updates) with a Settings dialog:

- **Search** — text search, an All/Official/AUR source filter, and a gallery
  (shown until the first search) of recent searches, popular packages, and
  repo-browse chips for core/extra/community/multilib/aur.
- **Installed** — every installed package with a live name filter and a
  Remove button.
- **Updates** — `list_upgradable()` results with an Update All button.
- **Settings** — force a specific helper (or `auto`) and toggle `--noconfirm`,
  writing to `~/.config/pkgmanager/config.json`. The chosen helper is
  validated by actually constructing a `PackageManager` before saving --
  picking one that isn't installed shows an error and leaves your current,
  working backend untouched rather than silently breaking it.

## Security model

- Every command is built and run as an argument list
  (`subprocess.Popen(..., shell=False)`) — nothing is ever concatenated into
  a shell string.
- `search` / `info` / `list_installed` run unprivileged.
- `install` / `remove` / `update` are elevated via `pkexec` in a graphical
  session (with `DISPLAY`/`XAUTHORITY`/`WAYLAND_DISPLAY` forwarded, since
  pkexec starts a clean environment) or `sudo` otherwise.
- If no AUR helper is installed, AUR operations are unavailable and
  `supports_aur()` reports `False`; official-repo operations keep working
  through plain `pacman`.

## Structure

```
pkgmanager/
├── __init__.py     # public exports
├── models.py       # Package, PackageDetail
├── exceptions.py   # exception hierarchy
├── helpers.py      # HELPER_COMMANDS, detect_helper(), output parsers
├── permissions.py  # command execution + pkexec/sudo elevation
├── backend.py      # PackageManager -- the public API
└── config.py       # optional ~/.config/pkgmanager/config.json loader
tests/
├── test_helpers.py
├── test_backend.py
└── test_permissions.py
```

## Where this deviates from the original spec

A few places where I made a judgment call or fixed something rather than
transcribing literally:

1. **Forcing an unmapped helper no longer silently becomes pacman.** The
   original fallback (`HELPER_COMMANDS.get(self.helper, HELPER_COMMANDS['pacman'])`)
   meant that forcing a helper with no command mapping (e.g. `pacaur`, which
   the spec explicitly allows forcing despite excluding it from
   auto-detection) would silently run *pacman's* commands while
   `helper_info()` kept reporting the forced name and `supports_aur()`
   returned `False` regardless of what that helper actually supports.
   `detect_helper()` now raises `HelperNotFoundError` immediately for any
   forced name with no entry in `HELPER_COMMANDS`.
2. **`list_installed` parsing accepts real pacman output, not just the
   spec's example.** The spec's example (`local/firefox 120.0.1-1`) has a
   `local/` prefix; real `pacman -Q` output has none — it's just
   `firefox 120.0.1-1`. The parser accepts both, defaulting to `'local'`
   when no prefix is present.
3. **Search results now flag already-installed packages.** Real
   pacman/AUR-helper `-Ss` output tags installed entries with `[installed]`;
   this is now parsed into `Package.installed`, so a GUI search view can
   badge them without a separate `list_installed()` call.
4. **`AURHelperMissingError` now has an actual trigger.** The spec left this
   open ("we can't know without checking – we'll trust the user"). `install()`
   takes an opt-in `known_aur=True` flag for callers that already know a
   package is AUR-only (e.g. from a `search()` result's `is_aur` flag),
   raising immediately instead of leaving pacman to fail confusingly. Not
   added to `remove()`/`update()` — neither needs the AUR helper to operate
   on already-installed packages.
5. **The streaming generator distinguishes early-exit from failure.** If a
   caller stops consuming `install`/`remove`/`update` output partway through
   (rather than the process actually failing), it cleans up without raising
   a spurious `CommandFailedError`.
6. **Repo-category browsing resolved with a real listing, not a stub.** The
   follow-up spec explicitly punted on this ("pragmatic: skip category
   filtering for now, but keep the buttons for future extension"). Rather
   than ship dead buttons, `list_repo_packages(repo)` wraps `pacman -Sl
   <repo>` to actually list everything in an official repo. It always calls
   `pacman` directly regardless of the active AUR helper, since this is a
   pure sync-database read with no helper-specific behavior. There's no AUR
   equivalent (no bulk-listing endpoint exists), so the 'aur' category chip
   instead sets the source filter to AUR and re-renders the current result
   set client-side.
7. **Settings validates before it commits.** The follow-up spec's
   `_save_settings` sketch replaced `self.pm` and wrote the config file
   unconditionally. If the chosen helper isn't actually installed, that
   would silently leave the app with a broken backend and a config file
   that fails on next launch too. The dialog now constructs the new
   `PackageManager` first; on failure it shows an error and leaves the
   previous, working backend and config untouched.
8. **Tab-switching no longer assumes the backend has finished initializing.**
   Switching to Installed/Updates before `PackageManager()` detection
   completes would otherwise hand `None` to a background thread, crash it
   silently, and leave the UI stuck in a "busy" state (buttons disabled,
   spinner running) forever.

## Tests

```bash
python -m unittest discover -s tests -t . -v
```

47 tests, all mocked (`subprocess.Popen` / `shutil.which`) — there's no live
pacman or AUR helper in scope here, so nothing actually shells out during
the test run. The GUI itself is checked with a headless smoke test under
Xvfb during development (not shipped -- it needs tkinter and a display,
neither guaranteed on a CI box) that builds the full tabbed UI, exercises
tab-switching, gallery/filter logic, and the settings dialog.
