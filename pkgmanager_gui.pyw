#!/usr/bin/env python3
"""
pkgmanager_gui.pyw

Tkinter front-end for the pkgmanager library. Tokyo Night theme per
dark_mode_guide.md. Tabbed layout: Search/Gallery, Installed, Updates,
plus a Settings dialog wired to ~/.config/pkgmanager/config.json.

Every PackageManager call runs on a background thread; a queue.Queue
carries results back to the Tk main loop, which is the only thread that
ever touches a widget or mutates app-level state.
"""

import platform
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from pkgmanager import PackageManager, PackageManagerError
from pkgmanager.config import load_config, save_config

# --------------------------------------------------------------------------
# Theme: Tokyo Night (dark_mode_guide.md, Palette 1 -- "developer utilities")
# --------------------------------------------------------------------------
C = {
    "bg": "#1a1b26", "surface": "#24283b", "surface2": "#2a2e42",
    "surface3": "#32374d", "border": "#3b4261",
    "fg": "#c0caf5", "fg_muted": "#9aa5ce", "fg_dim": "#565f89",
    "accent": "#7aa2f7", "accent_dark": "#4a6fd8", "accent_darker": "#3a5bc7",
    "accent2": "#bb9af7",
    "success": "#9ece6a", "warning": "#e0af68", "error": "#f7768e",
    "console_bg": "#16161e", "console_fg": "#a9b1d6", "highlight": "#2d3561",
}

_SYS = platform.system()
if _SYS == "Windows":
    FACE, MONO = "Segoe UI", "Consolas"
elif _SYS == "Darwin":
    FACE, MONO = "SF Pro", "SF Mono"
else:
    FACE, MONO = "Ubuntu", "Ubuntu Mono"

F, F_BOLD, F_SM, F_MONO = (FACE, 9), (FACE, 9, "bold"), (FACE, 8), (MONO, 9)

POPULAR_PACKAGES = ["firefox", "vlc", "git", "python", "neovim",
                     "discord", "steam", "chromium", "libreoffice-fresh", "gimp"]
CATEGORIES = ["core", "extra", "community", "multilib", "aur"]
FORCE_HELPER_OPTIONS = ["auto", "pacman", "yay", "paru", "trizen", "pikaur"]


# ---------------------------------------------------------------------------
# Theme helpers
# ---------------------------------------------------------------------------

def apply_theme(root):
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=C["bg"], foreground=C["fg"],
                fieldbackground=C["surface2"], selectbackground=C["accent"],
                selectforeground=C["bg"], insertcolor=C["fg"],
                bordercolor=C["border"], troughcolor=C["surface"],
                relief="flat", font=F)
    s.configure("TFrame", background=C["bg"])
    s.configure("TLabel", background=C["bg"], foreground=C["fg"])
    s.configure("TButton", background=C["surface2"], padding=[8, 4])
    s.map("TButton", background=[("active", C["surface3"]), ("pressed", C["border"])])
    s.configure("TCombobox", fieldbackground=C["surface2"], background=C["surface2"],
                foreground=C["fg"], arrowcolor=C["fg_muted"])
    s.map("TCombobox", fieldbackground=[("readonly", C["surface2"])])
    s.configure("TCheckbutton", background=C["bg"], foreground=C["fg"])
    s.configure("Accent.Horizontal.TProgressbar", troughcolor=C["surface2"],
                background=C["accent"], borderwidth=0, thickness=6)
    s.configure("Treeview", background=C["surface"], fieldbackground=C["surface"],
                foreground=C["fg"], rowheight=24, borderwidth=0)
    s.configure("Treeview.Heading", background=C["surface2"], foreground=C["fg_muted"],
                relief="flat", font=F_BOLD)
    s.map("Treeview", background=[("selected", C["highlight"])], foreground=[("selected", C["fg"])])
    s.configure("TNotebook", background=C["bg"], borderwidth=0)
    s.configure("TNotebook.Tab", background=C["surface"], foreground=C["fg_muted"],
                padding=[14, 6], font=F)
    s.map("TNotebook.Tab", background=[("selected", C["bg"])], foreground=[("selected", C["accent"])])
    root.configure(bg=C["bg"])


def _hoverable(btn, normal, hover):
    btn.bind("<Enter>", lambda e: btn.config(bg=hover))
    btn.bind("<Leave>", lambda e: btn.config(bg=normal))


def accent_button(parent, text, command):
    b = tk.Button(parent, text=text, command=command, bg=C["accent"], fg=C["bg"],
                  activebackground=C["accent_dark"], activeforeground=C["bg"],
                  relief="flat", cursor="hand2", font=F_BOLD, padx=14, pady=6)
    b.bind("<Enter>", lambda e: b.config(bg=C["accent_dark"]))
    b.bind("<Leave>", lambda e: b.config(bg=C["accent"]))
    b.bind("<ButtonPress-1>", lambda e: b.config(bg=C["accent_darker"]))
    b.bind("<ButtonRelease-1>", lambda e: b.config(bg=C["accent_dark"]))
    return b


def destructive_button(parent, text, command):
    b = tk.Button(parent, text=text, command=command, bg=C["surface2"], fg=C["error"],
                  activebackground=C["border"], activeforeground=C["error"],
                  relief="flat", cursor="hand2", font=F, padx=10, pady=4)
    _hoverable(b, C["surface2"], C["border"])
    return b


def plain_button(parent, text, command):
    b = tk.Button(parent, text=text, command=command, bg=C["surface2"], fg=C["fg"],
                  activebackground=C["border"], activeforeground=C["fg"],
                  relief="flat", cursor="hand2", font=F, padx=10, pady=4)
    _hoverable(b, C["surface2"], C["border"])
    return b


def chip_button(parent, text, command, fg=None):
    """Small 'chip' button for gallery rows (recent / popular / category)."""
    colour = fg or C["accent"]
    b = tk.Button(parent, text=text, command=command, bg=C["surface2"], fg=colour,
                  activebackground=C["border"], activeforeground=colour,
                  relief="flat", cursor="hand2", font=F_SM, padx=10, pady=3)
    _hoverable(b, C["surface2"], C["border"])
    return b


def section_header(parent, text):
    outer = tk.Frame(parent, bg=C["bg"])
    outer.pack(fill="x", padx=12, pady=(10, 4))
    tk.Frame(outer, bg=C["accent"], width=3).pack(side="left", fill="y")
    tk.Label(outer, text=f"  {text.upper()}", bg=C["bg"], fg=C["fg_dim"], font=F_BOLD).pack(side="left", pady=2)
    return outer


def make_status_label(parent, var):
    lbl = tk.Label(parent, textvariable=var, bg=C["bg"], fg=C["fg_muted"], font=F_SM, anchor="w")

    def _refresh(*_):
        t = var.get()
        if t.startswith("Error"):
            lbl.config(fg=C["error"])
        elif any(x in t for x in ("Installed", "Removed", "up to date", "Ready", "Found", "refreshed", "applied")):
            lbl.config(fg=C["success"])
        elif any(x in t for x in ("…", "Searching", "Installing", "Removing", "Updating",
                                   "Detecting", "Browsing", "Refreshing", "Checking", "Loading")):
            lbl.config(fg=C["warning"])
        else:
            lbl.config(fg=C["fg_muted"])

    var.trace_add("write", _refresh)
    return lbl


# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

class SettingsDialog(tk.Toplevel):
    """
    Reads/writes ~/.config/pkgmanager/config.json (force_helper, use_noconfirm).
    Validates the chosen helper by actually constructing a PackageManager
    before saving anything -- an invalid choice (e.g. forcing a helper that
    isn't installed) shows an error and leaves the dialog open, rather than
    silently corrupting the running app's backend or writing a config that
    would fail on next launch.
    """

    def __init__(self, parent, current_helper, current_noconfirm, on_save):
        super().__init__(parent)
        self.title("Settings")
        self.configure(bg=C["bg"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self._on_save = on_save

        body = tk.Frame(self, bg=C["bg"], padx=16, pady=16)
        body.pack(fill="both", expand=True)

        tk.Label(body, text="Force AUR helper", bg=C["bg"], fg=C["fg"], font=F).grid(
            row=0, column=0, sticky="w", pady=(0, 4))
        self.helper_var = tk.StringVar(value=current_helper or "auto")
        ttk.Combobox(body, textvariable=self.helper_var, state="readonly",
                     values=FORCE_HELPER_OPTIONS, width=18).grid(row=1, column=0, sticky="w", pady=(0, 12))

        self.noconfirm_var = tk.BooleanVar(value=current_noconfirm)
        ttk.Checkbutton(body, text="Use --noconfirm (don't prompt during install/remove/update)",
                        variable=self.noconfirm_var).grid(row=2, column=0, sticky="w", pady=(0, 16))

        btn_row = tk.Frame(body, bg=C["bg"])
        btn_row.grid(row=3, column=0, sticky="e")
        plain_button(btn_row, "Cancel", self.destroy).pack(side="right", padx=(6, 0))
        accent_button(btn_row, "Save", self._save).pack(side="right")

    def _save(self):
        force_helper = self.helper_var.get()
        force_helper = None if force_helper == "auto" else force_helper
        self._on_save(force_helper, self.noconfirm_var.get(), self)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class PkgManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("pkgmanager")
        self.root.geometry("900x680")
        self.root.minsize(700, 520)
        apply_theme(root)

        self.pm = None
        self.results = {}           # search tab: tree item id -> Package
        self.installed_results = {}
        self.updates_results = {}
        self.all_results = []       # unfiltered last search/browse result set
        self._all_installed = []    # unfiltered last list_installed() result set
        self.recent_searches = []   # capped at 5, in-memory only (not persisted)
        self.event_queue = queue.Queue()
        self.status_var = tk.StringVar(value="Detecting helper…")
        self.busy = False
        self._action_buttons = []

        self._build_ui()
        self.root.after(80, self._poll_queue)
        self._spawn(self._init_backend)

    # ---- backend bootstrap ------------------------------------------

    def _init_backend(self):
        try:
            cfg = load_config()
            pm = PackageManager(force_helper=cfg.get('force_helper'),
                                 use_noconfirm=cfg.get('use_noconfirm', True))
            self.event_queue.put(("backend_ready", pm))
        except PackageManagerError as exc:
            self.event_queue.put(("error", str(exc)))

    # ---- top-level layout -----------------------------------------------

    def _build_ui(self):
        header = tk.Frame(self.root, bg=C["bg"])
        header.pack(fill="x", padx=12, pady=(12, 0))
        tk.Label(header, text="pkgmanager", bg=C["bg"], fg=C["fg"], font=(FACE, 12, "bold")).pack(side="left")
        plain_button(header, "\u2699 Settings", self._open_settings).pack(side="right")
        self.helper_var = tk.StringVar(value="…")
        tk.Label(header, textvariable=self.helper_var, bg=C["bg"], fg=C["fg_dim"], font=F_SM).pack(
            side="right", padx=(0, 12))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=12)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        search_tab = tk.Frame(self.notebook, bg=C["bg"])
        installed_tab = tk.Frame(self.notebook, bg=C["bg"])
        updates_tab = tk.Frame(self.notebook, bg=C["bg"])
        self.notebook.add(search_tab, text="Search")
        self.notebook.add(installed_tab, text="Installed")
        self.notebook.add(updates_tab, text="Updates")

        self._build_search_tab(search_tab)
        self._build_installed_tab(installed_tab)
        self._build_updates_tab(updates_tab)

        section_header(self.root, "Output")
        log_frame = tk.Frame(self.root, bg=C["bg"])
        log_frame.pack(fill="both", expand=False, padx=12, pady=(0, 4))
        self.log = tk.Text(log_frame, height=6, bg=C["console_bg"], fg=C["console_fg"],
                            insertbackground=C["console_fg"], relief="flat",
                            highlightthickness=1, highlightbackground=C["border"],
                            font=F_MONO, padx=6, pady=4, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)

        bottom = tk.Frame(self.root, bg=C["bg"])
        bottom.pack(fill="x", padx=12, pady=(0, 10))
        make_status_label(bottom, self.status_var).pack(side="left")
        self.progress = ttk.Progressbar(bottom, style="Accent.Horizontal.TProgressbar",
                                         mode="indeterminate", length=140)
        self.progress.pack(side="right")

    # ---- Search tab -------------------------------------------------------

    def _build_search_tab(self, parent):
        top = tk.Frame(parent, bg=C["bg"])
        top.pack(fill="x", padx=12, pady=(12, 8))

        self.source_var = tk.StringVar(value="All")
        source_box = ttk.Combobox(top, textvariable=self.source_var, state="readonly",
                                   values=["All", "Official", "AUR"], width=9)
        source_box.pack(side="left", padx=(0, 8))
        source_box.bind("<<ComboboxSelected>>", lambda e: self._apply_source_filter())

        self.query_var = tk.StringVar()
        entry = tk.Entry(top, textvariable=self.query_var, bg=C["surface2"], fg=C["fg"],
                          insertbackground=C["fg"], relief="flat", highlightthickness=1,
                          highlightbackground=C["border"], highlightcolor=C["accent"], font=F)
        entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        entry.bind("<Return>", lambda e: self._on_search())
        entry.focus_set()

        self.search_btn = accent_button(top, "Search", self._on_search)
        self.search_btn.pack(side="left", padx=(0, 6))
        self.refresh_db_btn = plain_button(top, "Refresh DB", self._on_refresh_db)
        self.refresh_db_btn.pack(side="left")
        self._action_buttons.extend([self.search_btn, self.refresh_db_btn])

        self.results_header = section_header(parent, "Results")

        self.gallery_frame = tk.Frame(parent, bg=C["bg"])
        self._build_gallery(self.gallery_frame)
        self.gallery_frame.pack(fill="x", padx=12, pady=(0, 4), before=self.results_header)

        mid = tk.Frame(parent, bg=C["bg"])
        mid.pack(fill="both", expand=True, padx=12)

        self.tree = ttk.Treeview(mid, columns=("repo", "version", "installed"),
                                  show="tree headings", selectmode="extended", height=8)
        self.tree.heading("#0", text="Package")
        self.tree.heading("repo", text="Repo")
        self.tree.heading("version", text="Version")
        self.tree.heading("installed", text="Installed")
        self.tree.column("#0", width=260)
        self.tree.column("repo", width=90, anchor="center")
        self.tree.column("version", width=160)
        self.tree.column("installed", width=80, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        scroll.pack(side="left", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)

        actions = tk.Frame(parent, bg=C["bg"])
        actions.pack(fill="x", padx=12, pady=8)
        self.install_btn = accent_button(actions, "Install", self._on_install)
        self.install_btn.pack(side="left", padx=(0, 6))
        self.remove_btn = destructive_button(actions, "Remove", self._on_remove)
        self.remove_btn.pack(side="left", padx=(0, 6))
        self.info_btn = plain_button(actions, "Info", self._on_info)
        self.info_btn.pack(side="left")
        self._action_buttons.extend([self.install_btn, self.remove_btn, self.info_btn])

    def _build_gallery(self, parent):
        tk.Label(parent, text="RECENT", bg=C["bg"], fg=C["fg_dim"], font=F_SM).pack(anchor="w", pady=(4, 2))
        self.recent_chip_holder = tk.Frame(parent, bg=C["bg"])
        self.recent_chip_holder.pack(fill="x", pady=(0, 8))
        self._update_recent_chips()

        tk.Label(parent, text="POPULAR", bg=C["bg"], fg=C["fg_dim"], font=F_SM).pack(anchor="w", pady=(0, 2))
        for row_items in (POPULAR_PACKAGES[:5], POPULAR_PACKAGES[5:]):
            row = tk.Frame(parent, bg=C["bg"])
            row.pack(fill="x", pady=2)
            for name in row_items:
                chip_button(row, name, lambda n=name: self._on_chip_click(n)).pack(side="left", padx=(0, 6))

        tk.Label(parent, text="BROWSE A REPO", bg=C["bg"], fg=C["fg_dim"], font=F_SM).pack(anchor="w", pady=(8, 2))
        cat_row = tk.Frame(parent, bg=C["bg"])
        cat_row.pack(fill="x", pady=2)
        for cat in CATEGORIES:
            chip_button(cat_row, cat, lambda c=cat: self._on_category_click(c),
                        fg=C["accent2"]).pack(side="left", padx=(0, 6))

    def _update_recent_chips(self):
        for child in self.recent_chip_holder.winfo_children():
            child.destroy()
        if not self.recent_searches:
            tk.Label(self.recent_chip_holder, text="(none yet this session)",
                      bg=C["bg"], fg=C["fg_dim"], font=F_SM).pack(side="left")
            return
        for q in self.recent_searches:
            chip_button(self.recent_chip_holder, q, lambda q=q: self._on_chip_click(q)).pack(side="left", padx=(0, 6))

    def _update_gallery_visibility(self):
        if self.tree.get_children():
            self.gallery_frame.pack_forget()
        else:
            self.gallery_frame.pack(fill="x", padx=12, pady=(0, 4), before=self.results_header)

    def _render_results(self, packages):
        self.tree.delete(*self.tree.get_children())
        self.results = {}
        for pkg in packages:
            iid = self.tree.insert("", "end", text=pkg.name,
                                    values=(pkg.repository, pkg.version, "Yes" if pkg.installed else ""))
            self.results[iid] = pkg
        self._update_gallery_visibility()

    def _apply_source_filter(self):
        src = self.source_var.get()
        if src == "Official":
            filtered = [p for p in self.all_results if not p.is_aur]
        elif src == "AUR":
            filtered = [p for p in self.all_results if p.is_aur]
        else:
            filtered = list(self.all_results)
        self._render_results(filtered)

    def _populate_results(self, packages):
        self.all_results = list(packages)
        self._apply_source_filter()

    def _selected_packages(self):
        return [self.results[iid] for iid in self.tree.selection() if iid in self.results]

    def _remember_search(self, query):
        if query in self.recent_searches:
            self.recent_searches.remove(query)
        self.recent_searches.insert(0, query)
        self.recent_searches = self.recent_searches[:5]
        self._update_recent_chips()

    def _on_search(self):
        if self.busy or not self.pm:
            return
        query = self.query_var.get().strip()
        if not query:
            return
        self.status_var.set(f"Searching for '{query}'…")
        self._set_busy(True)
        self._spawn(self._do_search, query)

    def _do_search(self, query):
        try:
            self.event_queue.put(("search_results", (query, self.pm.search(query))))
        except PackageManagerError as exc:
            self.event_queue.put(("error", str(exc)))

    def _on_chip_click(self, query):
        self.query_var.set(query)
        self._on_search()

    def _on_category_click(self, category):
        if category == 'aur':
            self.source_var.set("AUR")
            self._apply_source_filter()
            self.status_var.set("Filtered to AUR results from the current list.")
            return
        if self.busy or not self.pm:
            return
        self.source_var.set("All")
        self.status_var.set(f"Browsing '{category}'…")
        self._set_busy(True)
        self._spawn(self._do_browse_repo, category)

    def _do_browse_repo(self, repo):
        try:
            self.event_queue.put(("search_results", (None, self.pm.list_repo_packages(repo))))
        except PackageManagerError as exc:
            self.event_queue.put(("error", str(exc)))

    def _on_refresh_db(self):
        if self.busy or not self.pm:
            return
        self.status_var.set("Refreshing package databases…")
        self._set_busy(True)
        self._spawn(self._do_refresh_db)

    def _do_refresh_db(self):
        try:
            self.pm.refresh(callback=lambda line: self.event_queue.put(("log", line)))
            self.event_queue.put(("op_done", "Databases refreshed."))
        except PackageManagerError as exc:
            self.event_queue.put(("error", str(exc)))

    def _on_install(self):
        pkgs = self._selected_packages()
        if not pkgs or self.busy:
            return
        names = [p.name for p in pkgs]
        known_aur = any(p.is_aur for p in pkgs)
        self.status_var.set(f"Installing {', '.join(names)}…")
        self._set_busy(True)
        self._spawn(self._do_install, names, known_aur)

    def _do_install(self, names, known_aur):
        try:
            self.pm.install(names, callback=lambda line: self.event_queue.put(("log", line)),
                             known_aur=known_aur)
            self.event_queue.put(("op_done", f"Installed {', '.join(names)}."))
        except PackageManagerError as exc:
            self.event_queue.put(("error", str(exc)))

    def _on_remove(self):
        pkgs = self._selected_packages()
        if not pkgs or self.busy:
            return
        names = [p.name for p in pkgs]
        if not messagebox.askyesno("Confirm removal", f"Remove {', '.join(names)}?"):
            return
        self.status_var.set(f"Removing {', '.join(names)}…")
        self._set_busy(True)
        self._spawn(self._do_remove, names)

    def _do_remove(self, names):
        try:
            self.pm.remove(names, callback=lambda line: self.event_queue.put(("log", line)))
            self.event_queue.put(("op_done", f"Removed {', '.join(names)}."))
        except PackageManagerError as exc:
            self.event_queue.put(("error", str(exc)))

    def _on_info(self):
        pkgs = self._selected_packages()
        if not pkgs or self.busy:
            return
        pkg = pkgs[0]
        self._set_busy(True)
        self._spawn(self._do_info, pkg.name, pkg.installed)

    def _do_info(self, name, local):
        try:
            self.event_queue.put(("info", self.pm.info(name, local=local)))
        except PackageManagerError as exc:
            self.event_queue.put(("error", str(exc)))

    def _show_info(self, detail):
        lines = [f"{detail.name} {detail.version}  ({detail.repository})", "", detail.description]
        if detail.depends:
            lines.append("\nDepends: " + ", ".join(detail.depends))
        if detail.license:
            lines.append("License: " + ", ".join(detail.license))
        if detail.maintainer:
            lines.append(f"Maintainer: {detail.maintainer}   Votes: {detail.votes}   Popularity: {detail.popularity}")
        messagebox.showinfo(detail.name, "\n".join(lines))

    # ---- Installed tab ------------------------------------------------

    def _build_installed_tab(self, parent):
        top = tk.Frame(parent, bg=C["bg"])
        top.pack(fill="x", padx=12, pady=(12, 8))

        tk.Label(top, text="Filter:", bg=C["bg"], fg=C["fg_dim"], font=F_SM).pack(side="left", padx=(0, 6))
        self.installed_filter_var = tk.StringVar()
        filt = tk.Entry(top, textvariable=self.installed_filter_var, bg=C["surface2"], fg=C["fg"],
                         insertbackground=C["fg"], relief="flat", highlightthickness=1,
                         highlightbackground=C["border"], highlightcolor=C["accent"], font=F)
        filt.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))

        self.refresh_installed_btn = plain_button(top, "Refresh List", self._trigger_fetch_installed)
        self.refresh_installed_btn.pack(side="left")
        self._action_buttons.append(self.refresh_installed_btn)

        section_header(parent, "Installed Packages")

        mid = tk.Frame(parent, bg=C["bg"])
        mid.pack(fill="both", expand=True, padx=12)

        self.installed_tree = ttk.Treeview(mid, columns=("version", "repo"), show="tree headings",
                                            selectmode="extended", height=10)
        self.installed_tree.heading("#0", text="Package")
        self.installed_tree.heading("version", text="Version")
        self.installed_tree.heading("repo", text="Repository")
        self.installed_tree.column("#0", width=280)
        self.installed_tree.column("version", width=180)
        self.installed_tree.column("repo", width=100, anchor="center")
        self.installed_tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(mid, orient="vertical", command=self.installed_tree.yview)
        scroll.pack(side="left", fill="y")
        self.installed_tree.configure(yscrollcommand=scroll.set)

        actions = tk.Frame(parent, bg=C["bg"])
        actions.pack(fill="x", padx=12, pady=8)
        self.remove_installed_btn = destructive_button(actions, "Remove Selected", self._on_remove_installed)
        self.remove_installed_btn.pack(side="left", padx=(0, 6))
        self.installed_info_btn = plain_button(actions, "Info", self._on_info_installed)
        self.installed_info_btn.pack(side="left")
        self._action_buttons.extend([self.remove_installed_btn, self.installed_info_btn])

        # Registered last, once self.installed_tree already exists, since
        # the trace callback renders into it.
        self.installed_filter_var.trace_add("write", lambda *_: self._render_installed_filtered())

    def _trigger_fetch_installed(self):
        if self.busy or not self.pm:
            return
        self.status_var.set("Loading installed packages…")
        self._set_busy(True)
        self._spawn(self._fetch_installed)

    def _fetch_installed(self):
        try:
            self.event_queue.put(("installed_results", self.pm.list_installed()))
        except PackageManagerError as exc:
            self.event_queue.put(("error", str(exc)))

    def _populate_installed(self, packages):
        self._all_installed = list(packages)
        self._render_installed_filtered()

    def _render_installed_filtered(self):
        needle = self.installed_filter_var.get().strip().lower()
        packages = [p for p in self._all_installed if needle in p.name.lower()] if needle else list(self._all_installed)
        self.installed_tree.delete(*self.installed_tree.get_children())
        self.installed_results = {}
        for pkg in packages:
            iid = self.installed_tree.insert("", "end", text=pkg.name, values=(pkg.version, pkg.repository))
            self.installed_results[iid] = pkg

    def _selected_installed_packages(self):
        return [self.installed_results[iid] for iid in self.installed_tree.selection() if iid in self.installed_results]

    def _on_remove_installed(self):
        pkgs = self._selected_installed_packages()
        if not pkgs or self.busy:
            return
        names = [p.name for p in pkgs]
        if not messagebox.askyesno("Confirm removal", f"Remove {', '.join(names)}?"):
            return
        self.status_var.set(f"Removing {', '.join(names)}…")
        self._set_busy(True)
        self._spawn(self._do_remove, names)

    def _on_info_installed(self):
        pkgs = self._selected_installed_packages()
        if not pkgs or self.busy:
            return
        pkg = pkgs[0]
        self._set_busy(True)
        self._spawn(self._do_info, pkg.name, True)

    # ---- Updates tab ------------------------------------------------

    def _build_updates_tab(self, parent):
        top = tk.Frame(parent, bg=C["bg"])
        top.pack(fill="x", padx=12, pady=(12, 8))

        self.updates_summary_var = tk.StringVar(value="Switch to this tab to check for updates.")
        tk.Label(top, textvariable=self.updates_summary_var, bg=C["bg"], fg=C["fg_muted"], font=F).pack(side="left")

        self.refresh_updates_btn = plain_button(top, "Check Again", self._trigger_fetch_updates)
        self.refresh_updates_btn.pack(side="right")
        self._action_buttons.append(self.refresh_updates_btn)

        section_header(parent, "Upgradable Packages")

        mid = tk.Frame(parent, bg=C["bg"])
        mid.pack(fill="both", expand=True, padx=12)

        self.updates_tree = ttk.Treeview(mid, columns=("current", "new", "repo"), show="tree headings",
                                          selectmode="none", height=10)
        self.updates_tree.heading("#0", text="Package")
        self.updates_tree.heading("current", text="Current")
        self.updates_tree.heading("new", text="New")
        self.updates_tree.heading("repo", text="Repository")
        self.updates_tree.column("#0", width=260)
        self.updates_tree.column("current", width=150)
        self.updates_tree.column("new", width=150)
        self.updates_tree.column("repo", width=100, anchor="center")
        self.updates_tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(mid, orient="vertical", command=self.updates_tree.yview)
        scroll.pack(side="left", fill="y")
        self.updates_tree.configure(yscrollcommand=scroll.set)

        actions = tk.Frame(parent, bg=C["bg"])
        actions.pack(fill="x", padx=12, pady=8)
        self.update_all_btn = accent_button(actions, "Update All", self._on_update_all)
        self.update_all_btn.pack(side="left")
        self._action_buttons.append(self.update_all_btn)

    def _trigger_fetch_updates(self):
        if self.busy or not self.pm:
            return
        self.updates_summary_var.set("Checking for updates…")
        self._set_busy(True)
        self._spawn(self._fetch_updates)

    def _fetch_updates(self):
        try:
            self.event_queue.put(("updates_results", self.pm.list_upgradable()))
        except PackageManagerError as exc:
            self.event_queue.put(("error", str(exc)))

    def _populate_updates(self, packages):
        self.updates_tree.delete(*self.updates_tree.get_children())
        self.updates_results = {}
        for pkg in packages:
            iid = self.updates_tree.insert("", "end", text=pkg.name,
                                            values=(pkg.version, pkg.new_version or "", pkg.repository))
            self.updates_results[iid] = pkg
        n = len(packages)
        self.updates_summary_var.set("System is up to date." if n == 0 else f"{n} update(s) available.")

    def _on_update_all(self):
        if self.busy or not self.pm:
            return
        self.status_var.set("Updating system…")
        self._set_busy(True)
        self._spawn(self._do_update)

    def _do_update(self):
        try:
            self.pm.update(callback=lambda line: self.event_queue.put(("log", line)))
            self.event_queue.put(("op_done", "System up to date."))
        except PackageManagerError as exc:
            self.event_queue.put(("error", str(exc)))

    # ---- Settings -------------------------------------------------------

    def _open_settings(self):
        cfg = load_config()
        current_helper = cfg.get('force_helper') or "auto"
        current_noconfirm = cfg.get('use_noconfirm', True)
        SettingsDialog(self.root, current_helper, current_noconfirm, self._on_settings_saved)

    def _on_settings_saved(self, force_helper, use_noconfirm, dialog):
        try:
            new_pm = PackageManager(force_helper=force_helper, use_noconfirm=use_noconfirm)
        except PackageManagerError as exc:
            messagebox.showerror("Settings", f"Couldn't apply that: {exc}")
            return  # leave the dialog open so the user can pick something else
        save_config({'force_helper': force_helper, 'use_noconfirm': use_noconfirm})
        self.pm = new_pm
        self._refresh_helper_label()
        self.status_var.set("Settings applied.")
        dialog.destroy()

    # ---- threading plumbing -------------------------------------------

    def _spawn(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in self._action_buttons:
            btn.config(state=state, cursor="watch" if busy else "hand2")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _append_log(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.event_queue.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _refresh_helper_label(self):
        aur = "AUR-capable" if self.pm.supports_aur() else "no AUR helper (official repos only)"
        self.helper_var.set(f"{self.pm.helper_info()} · {aur}")

    def _active_tab_text(self):
        try:
            return self.notebook.tab(self.notebook.select(), "text")
        except tk.TclError:
            return ""

    def _on_tab_changed(self, _event):
        if self.busy or not self.pm:
            return
        tab = self._active_tab_text()
        if tab == "Installed":
            self._set_busy(True)
            self._spawn(self._fetch_installed)
        elif tab == "Updates":
            self._set_busy(True)
            self._spawn(self._fetch_updates)

    def _refresh_after_op(self):
        if not self.pm:
            return
        query = self.query_var.get().strip()
        active = self._active_tab_text()
        if active == "Search" and query:
            self._set_busy(True)
            self._spawn(self._do_search, query)
        elif active == "Installed":
            self._set_busy(True)
            self._spawn(self._fetch_installed)
        elif active == "Updates":
            self._set_busy(True)
            self._spawn(self._fetch_updates)

    def _handle_event(self, kind, payload):
        if kind == "backend_ready":
            self.pm = payload
            self._refresh_helper_label()
            self.status_var.set("Ready.")
        elif kind == "search_results":
            query, results = payload
            self._populate_results(results)
            if query:
                self._remember_search(query)
            self.status_var.set(f"Found {len(results)} package(s).")
            self._set_busy(False)
        elif kind == "installed_results":
            self._populate_installed(payload)
            self.status_var.set(f"{len(payload)} package(s) installed.")
            self._set_busy(False)
        elif kind == "updates_results":
            self._populate_updates(payload)
            self.status_var.set(self.updates_summary_var.get())
            self._set_busy(False)
        elif kind == "log":
            self._append_log(payload)
        elif kind == "op_done":
            self.status_var.set(payload)
            self._set_busy(False)
            self._refresh_after_op()
        elif kind == "info":
            self._show_info(payload)
            self._set_busy(False)
        elif kind == "error":
            self.status_var.set(f"Error: {payload}")
            self._append_log(f"[error] {payload}")
            self._set_busy(False)


def main():
    root = tk.Tk()
    PkgManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
