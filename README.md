# aur-gui

A single, reliable Python interface for package management on Arch Linux —
official repos via `pacman`, and the AUR via whichever helper (`yay`, `paru`,
`trizen`, `pikaur`) is installed. A lighter, AUR-helper-agnostic backend in
the spirit of `pamac`, plus a Tkinter GUI (`aur-gui`).

The importable library keeps its original name, `pkgmanager` — only the
distribution name, the GUI script, and the desktop entry are branded
`aur-gui`. `pip install aur-gui` gives you both `import pkgmanager` and the
`aur-gui` command.

## Install

```bash
pip install -e .
```

This registers the `aur-gui` command (see `[project.scripts]` in
`pyproject.toml`) in addition to the `pkgmanager` library.

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

`aur_gui.py` (installed as the `aur-gui` command) is a tabbed Tkinter
front-end (Search/Gallery, Installed, Updates) with a Settings dialog:

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
  working backend untouched rather than silently breaking it. Also has a
  **Run Diagnostics** button (see `--self-test` below).

Before every install/remove/update/refresh, the GUI runs a pre-flight pass:

- **Stale lock detection.** If `/var/lib/pacman/db.lck` exists and no
  pacman process is running, you're asked to confirm before it's removed
  (via the same `pkexec`/`sudo` path as everything else) -- it's never
  deleted automatically. If pacman *is* running, the operation is blocked
  with an error instead.
- **Free-space guard**, before install/update only: sizes for every
  package about to be installed/upgraded are fetched in one batched call
  (`PackageManager.info_many()`) and compared against `shutil.disk_usage`,
  with a 1 GiB buffer. Blocks with the shortfall shown if there isn't
  enough room.
- **Optional site hook** at `/usr/local/bin/aur-gui-preflight` -- run as
  your normal user (never elevated) with a 10s timeout, for anything
  environment-specific (e.g. checking an NTFS partition isn't dirty before
  touching it). Skipped silently if the file doesn't exist.

While an operation is running, a small **"Waiting for disk I/O…"** label
lights up next to the status bar whenever the underlying pacman/helper
process (found by walking the whole process tree under the `pkexec`/`sudo`
wrapper, not just watching that wrapper's own PID) is blocked in
uninterruptible sleep -- useful on slow storage, where it's otherwise hard
to tell "still working" apart from "hung."

## Diagnostics (`--self-test`)

```bash
aur-gui --self-test
```

Runs a battery of **real, read-only checks against the live system** --
binary presence (`pacman`, `sudo`, `pkexec`, the detected helper), a real
unprivileged `-Q`/`-Qi` call fed through the actual parser, lock state,
free disk space, and whether a cached-auth Polkit policy is installed --
and exits 0 if everything passes, 1 otherwise. Also reachable from the GUI
via Settings → Run Diagnostics.

This is deliberately **not** the `unittest` suite below. That suite mocks
every subprocess call by design (it's testing this package's own
parsing/command-building logic against controlled fixtures), so it can't
tell you anything about *your* pacman, AUR helper, or Polkit setup -- only
about whether this codebase's own logic is self-consistent. `--self-test`
is the one that actually shells out to your real system.

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
- `remove_lock` (deleting a confirmed-stale pacman lock file) goes through
  the identical elevation path as install/remove/update -- there's no
  separate, less-audited way to gain root here.

### Optional: cached auth for `pkexec`

By default every privileged action prompts for a fresh password/auth
dialog. Polkit can be configured to remember auth for a while
(`auth_admin_keep`) -- but it takes **two** files, not one, if you want
that to apply only to database refresh (`-Sy`) and not to install/remove
(the two example files below live in `packaging/polkit/`):

1. `org.example.aur-gui.pkexec.policy` -- an action definition. This alone
   can only grant/require auth for pkexec-ing a *whole binary path*; it
   cannot look at arguments. Adjust `org.example` to a reverse-domain you
   actually control before shipping this anywhere.
2. `10-aur-gui-refresh.rules` -- a Polkit JS rule (in
   `/etc/polkit-1/rules.d/`) that inspects `action.lookup("command_line")`
   and only returns `auth_admin_keep` when the invoked command line ends
   in `-Sy`, leaving install/remove at the normal (non-cached) prompt.

Install both, restart `polkit`, and `--self-test` will report the
cached-auth policy as detected.

### Flatpak

Out of scope. This project is pacman + AUR only; for Flatpak, use
`gnome-software` or `flatpak` directly.

## Structure

```
pkgmanager/
├── __init__.py     # public exports
├── models.py       # Package, PackageDetail
├── exceptions.py   # exception hierarchy
├── helpers.py      # HELPER_COMMANDS, detect_helper(), output parsers
├── permissions.py  # command execution, pkexec/sudo elevation, I/O-wait watcher
├── backend.py      # PackageManager -- the public API
├── preflight.py    # lock/space/hook checks run before a privileged operation
├── diagnostics.py  # --self-test: real, read-only checks against the live system
└── config.py       # optional ~/.config/pkgmanager/config.json loader
aur_gui.py           # Tkinter GUI + `aur-gui` / `aur-gui --self-test` entry point
tests/
├── test_helpers.py
├── test_backend.py
├── test_permissions.py
├── test_preflight.py
└── test_diagnostics.py
scripts/
└── gui_smoke_test.py   # manual, Xvfb-based -- not part of `unittest discover`
packaging/
├── aur-gui.desktop
└── polkit/
    ├── org.example.aur-gui.pkexec.policy
    └── 10-aur-gui-refresh.rules
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

The 1.2 follow-up spec (rebrand, I/O-wait indicator, pre-flight checks,
size metadata) needed more correction than transcription:

9. **I/O-wait watches the whole process tree, not one PID.** A tracked PID
   from a root-required operation is `pkexec`'s or `sudo`'s -- the wrapper
   typically just sits idle waiting on its child while *that* child (or, for
   an AUR helper, a grandchild) does the actual disk I/O. Watching only the
   wrapper's own `/proc/<pid>/stat` would almost never show `D` state. Fixed
   by walking every live descendant (`permissions.list_descendant_pids`) and
   checking whether any of them is blocked, not just the one PID `Popen`
   returns. That PID also wasn't exposed to callers at all before this --
   `permissions.run()` now takes an optional `pid_callback`, threaded up
   through every streaming `PackageManager` method.
10. **The GUI couldn't have shipped as a console-script entry point as
    specified.** `.pyw` isn't a suffix Python's import machinery resolves
    (`importlib.import_module` looks for `.py`), and there was no
    `py-modules` declaration in `pyproject.toml` for a standalone script
    either -- the file wasn't even landing in a built wheel. Renamed to
    `aur_gui.py` and added `py-modules = ["aur_gui"]`.
11. **Free-space guard batches size lookups instead of one-per-package.**
    The spec's per-upgradable-package `info()` loop is an N-subprocess-spawn
    problem for a full system update -- exactly the cost the "slow storage"
    framing was trying to avoid. `-Si`/`-Qi` accept multiple names in one
    call (same blank-line-per-block format `-Ss` already used), so
    `info_many()` does one call, falling back to per-package only if the
    batch itself fails to resolve.
12. **`download_size`/`installed_size` no longer collide.** Both mapped to
    the same `size` field before; for `-Si` output, which has both, the
    installed-size line silently overwrote the download-size line every
    time. Split into their own fields; `size` keeps its original behavior
    so nothing depending on it breaks.
13. **The free-space guard sums both sizes instead of taking the larger
    one.** pacman keeps the downloaded file in its cache while writing the
    installed copy, so both consume space at the same time during the
    operation -- taking `max()` would under-count peak usage, which is the
    wrong direction to be wrong in for a guard whose whole job is avoiding
    a mid-operation out-of-space failure.
14. **Stale lock removal requires confirmation; it's never automatic.**
    Auto-deleting `/var/lib/pacman/db.lck` has a race between "check if
    pacman is running" and "delete it," and getting that race wrong risks
    corrupting a mid-transaction database. `check_stale_lock()` only
    reports the state; the GUI shows it and asks before `remove_stale_lock()`
    ever runs.
15. **The pre-flight hook has a timeout and doesn't run on the GUI thread
    the way everything else does.** As specified ("run these in the GUI
    thread, synchronously"), an arbitrary user-supplied script with no
    bound on its runtime would freeze the whole window -- which directly
    contradicts this project's own "GUI stays responsive" design. It's
    bounded to 10s, and a timeout counts as failure. The free-space check
    goes further: `info_many()` is a real subprocess/network call with no
    bound we control at all (AUR latency), so unlike the lock check and the
    now-bounded hook, it runs on the background thread, not the GUI thread,
    as the first thing `_do_install`/`_do_update` do.
16. **The Polkit mechanism needed a second file to actually do what was
    asked.** A `.policy` file alone can grant/require auth for pkexec-ing a
    binary path as a whole; it can't distinguish `-Sy` from `-S`/`-R` by
    argument. Getting the "cached auth for refresh only" behavior the spec
    described needs a `.rules` file too -- see "Optional: cached auth for
    `pkexec`" above.

## Tests

```bash
python -m unittest discover -s tests -t . -v
```

94 tests, all mocked (`subprocess.Popen` / `shutil.which` / a fake `/proc`
directory for the process-tree tests) -- there's no live pacman or AUR
helper in scope here, so nothing actually shells out during the test run.
That's what `aur-gui --self-test` is for (see Diagnostics above). The GUI
itself is checked with a headless smoke test under Xvfb during development
(`scripts/gui_smoke_test.py`, not auto-discovered -- it needs tkinter and a
display, neither guaranteed on a CI box) that instantiates the real Tk app
and exercises tab-switching, the pre-flight gate, and the I/O-wait event
wiring end to end.
