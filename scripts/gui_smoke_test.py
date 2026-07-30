import os
import sys
import threading
import time

os.environ['DISPLAY'] = ':99'
sys.path.insert(0, '/home/claude/work/pkgmanager')
sys.argv = ['aur_gui.py']

import tkinter as tk
from tkinter import messagebox
from unittest import mock

import aur_gui

# Guard against any dialog accidentally popping up and hanging the script.
with mock.patch.object(messagebox, 'showinfo'), \
     mock.patch.object(messagebox, 'showerror'), \
     mock.patch.object(messagebox, 'askyesno', return_value=True):

    root = tk.Tk()
    app = aur_gui.PkgManagerApp(root)
    print('App instantiated OK')

    # Let the queue-poll loop run a few times so _init_backend's result
    # (an "error" event, since this sandbox has no pacman) gets processed.
    for _ in range(5):
        root.update()
        time.sleep(0.05)
    print('After backend init attempt, status:', app.status_var.get())
    assert not app.busy, "should not be stuck busy after backend init fails"

    # _run_preflight with a clean sandbox (no lock file, no hook installed)
    # should return True without popping any dialog.
    assert app._run_preflight() is True
    print('_run_preflight() on a clean system: True (as expected)')

    # Exercise the new event kinds directly -- these are what
    # _do_install/_do_update/_do_remove/_do_refresh_db push from a
    # background thread in real use.
    # io_wait event -> label wiring, tested in isolation from the real
    # watcher thread (list_descendant_pids/any_process_io_wait already have
    # dedicated fake-/proc unit tests in test_permissions.py; this is only
    # checking that _handle_event actually updates the widget).
    app.event_queue.put(("io_wait", True))
    time.sleep(0.1)
    root.update()
    print('io_wait_var after True event:', repr(app.io_wait_var.get()))
    assert app.io_wait_var.get() == "Waiting for disk I/O…"

    app.event_queue.put(("io_wait", False))
    time.sleep(0.1)
    root.update()
    print('io_wait_var after False event:', repr(app.io_wait_var.get()))
    assert app.io_wait_var.get() == ""

    # op_pid handling: confirm it schedules a watcher thread without
    # blocking the GUI thread (the watcher's own correctness is unit
    # tested separately; this only proves the wiring doesn't throw).
    threads_before = threading.active_count()
    app.event_queue.put(("op_pid", os.getpid()))
    time.sleep(0.1)
    root.update()
    assert threading.active_count() > threads_before
    print('op_pid event spawns a watcher thread: OK')
    time.sleep(0.3)  # let that real watcher thread finish and exit cleanly
    root.update()

    # _set_busy(False) must also clear io_wait_var as a safety net,
    # independent of the watcher thread's own final message.
    app.io_wait_var.set("Waiting for disk I/O…")
    app._set_busy(False)
    assert app.io_wait_var.get() == ""
    print('_set_busy(False) clears io_wait_var: OK')

    # _show_info with sizes populated -- make sure the new size_bits
    # branch doesn't throw.
    from pkgmanager.models import PackageDetail
    detail = PackageDetail(name='vim', version='9.0-1', description='editor', repository='extra',
                            download_size='2.05 MiB', installed_size='3.45 MiB')
    app._show_info(detail)
    print('_show_info with sizes: OK (no exception)')

    root.destroy()

print('ALL SMOKE TESTS PASSED')
