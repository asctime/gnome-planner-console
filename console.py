# -*- coding: utf-8 -*-
# Planner Python Shell plugin (PyGTK2, Python 2.7, single file)
#
# Provides View -> Python Shell in Planner.
# Shell window includes its own File/Edit/Settings menus, scrollable console,
# persistent settings (colors, font size, history file), and exposes the
# plugin-available API (window, application, planner, gtk, etc.) for ad-hoc testing.

import os
import sys
import time
import traceback
import ConfigParser

# PyGTK2
import pygtk
pygtk.require('2.0')
import gtk
import gobject

# Try GLib convenience if present
try:
    import glib
except Exception:
    glib = None

# Globals 'window', 'application', 'planner' are expected to be injected by Planner's
# Python host per the standard planner-python-plugin.c bridge.
# Do not import gtk.main() anywhere; Planner controls the GTK main loop.

# -------------------------------
# Configuration helpers
# -------------------------------

def _planner_user_dir():
    """
    Best-effort resolve of Planner's per-user directory (parent of 'python').
    Prefer XDG user data dir if available, else fallback to ~/.gnome2/planner.
    """
    # Prefer GLib's user data dir on modern setups
    base = None
    try:
        if glib is not None and hasattr(glib, 'get_user_data_dir'):
            base = os.path.join(glib.get_user_data_dir(), 'planner')
    except Exception:
        base = None

    if not base:
        # Legacy path used by Planner's Python loader on many systems
        base = os.path.join(os.path.expanduser('~'), '.gnome2', 'planner')

    # Ensure directory exists
    try:
        if not os.path.isdir(base):
            os.makedirs(base)
    except Exception:
        pass
    return base

def _config_path():
    return os.path.join(_planner_user_dir(), 'planner-python-shell.ini')

def _default_history_path():
    return os.path.join(_planner_user_dir(), 'python-shell-history.txt')

# -------------------------------
# The interactive console window
# -------------------------------

class PlannerPythonShell(gtk.Window):
    def __init__(self):
        gtk.Window.__init__(self, gtk.WINDOW_TOPLEVEL)
        self.set_title("Planner Python Shell")
        self.set_default_size(760, 520)
        self.set_position(gtk.WIN_POS_CENTER)
        self.set_border_width(6)

        # Restore settings
        self.settings = {
            'fg': '#E8E8E8',
            'bg': '#202020',
            'font_size': 10,
            'history_file': _default_history_path(),
        }
        self._load_settings()

        # Build UI
        self.vbox = gtk.VBox(False, 4)
        self.add(self.vbox)

        self._build_menubar()
        self._build_console()
        self._apply_settings_to_widgets()

        self.connect("destroy", self._on_destroy)

        # Eval environment seeded with plugin API
        self.locals = {}
        self._seed_environment()

        # History
        self.history = self._read_history_file()
        self.history_pos = len(self.history)

        self.show_all()

    # ---------- Menubar ----------

    def _build_menubar(self):
        menubar = gtk.MenuBar()

        # File
        file_menu = gtk.Menu()
        mi_file = gtk.MenuItem("File")
        mi_file.set_submenu(file_menu)

        mi_open = gtk.MenuItem("Open File…")
        mi_open.connect("activate", self._on_open_file)
        file_menu.append(mi_open)

        # Edit
        edit_menu = gtk.Menu()
        mi_edit = gtk.MenuItem("Edit")
        mi_edit.set_submenu(edit_menu)

        mi_cut = gtk.MenuItem("Cut")
        mi_cut.connect("activate", self._on_cut)
        edit_menu.append(mi_cut)

        mi_copy = gtk.MenuItem("Copy")
        mi_copy.connect("activate", self._on_copy)
        edit_menu.append(mi_copy)

        mi_paste = gtk.MenuItem("Paste")
        mi_paste.connect("activate", self._on_paste)
        edit_menu.append(mi_paste)

        # Settings
        settings_menu = gtk.Menu()
        mi_settings_root = gtk.MenuItem("Settings")
        mi_settings_root.set_submenu(settings_menu)

        mi_prefs = gtk.MenuItem("Preferences…")
        mi_prefs.connect("activate", self._on_settings)
        settings_menu.append(mi_prefs)

        menubar.append(mi_file)
        menubar.append(mi_edit)
        menubar.append(mi_settings_root)
        self.vbox.pack_start(menubar, False, False, 0)

    # ---------- Console widgets ----------

    def _build_console(self):
        # Output (read-only, scrollable)
        self.output = gtk.TextView()
        self.output.set_editable(False)
        self.output.set_cursor_visible(False)
        self.output_buf = self.output.get_buffer()

        scrolled = gtk.ScrolledWindow()
        scrolled.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_ALWAYS)
        scrolled.add(self.output)
        self.vbox.pack_start(scrolled, True, True, 0)

        # Input prompt
        hbox = gtk.HBox(False, 6)
        lbl = gtk.Label(">>>")
        hbox.pack_start(lbl, False, False, 0)

        self.entry = gtk.Entry()
        self.entry.connect("activate", self._execute_entry)
        self.entry.connect("key-press-event", self._on_keypress_history)
        hbox.pack_start(self.entry, True, True, 0)

        self.vbox.pack_start(hbox, False, False, 0)

        # Initial banner
        self._writeln("Planner Python Shell (PyGTK2, Python 2.7)")
        self._writeln("Built-in: window, application, planner, gtk, gobject, glib")
        self._writeln("Helpers: demo_list_tasks(), demo_new_task(name=None), demo_benchmark_iter()")
        self._writeln("Tip: File→Open File… will exec a .py in this context.\n")

    def _apply_settings_to_widgets(self):
        # Colors
        try:
            fg = gtk.gdk.color_parse(self.settings['fg'])
            bg = gtk.gdk.color_parse(self.settings['bg'])
            for w in (self.output, self.entry):
                w.modify_text(gtk.STATE_NORMAL, fg)
                w.modify_base(gtk.STATE_NORMAL, bg)
        except Exception:
            pass

        # Font size
        try:
            import pango
            desc = pango.FontDescription()
            # Pango size in PANGO_SCALE units (size * 1024)
            desc.set_size(int(self.settings['font_size']) * pango.SCALE)
            self.output.modify_font(desc)
            self.entry.modify_font(desc)
        except Exception:
            pass

    # ---------- Settings persistence ----------

    def _load_settings(self):
        cfg = ConfigParser.ConfigParser()
        path = _config_path()
        try:
            if os.path.isfile(path):
                cfg.read(path)
                self.settings['fg'] = cfg.get('shell', 'fg')
                self.settings['bg'] = cfg.get('shell', 'bg')
                self.settings['font_size'] = cfg.getint('shell', 'font_size')
                self.settings['history_file'] = cfg.get('shell', 'history_file')
        except Exception:
            pass

    def _save_settings(self):
        cfg = ConfigParser.RawConfigParser()
        cfg.add_section('shell')
        cfg.set('shell', 'fg', self.settings['fg'])
        cfg.set('shell', 'bg', self.settings['bg'])
        cfg.set('shell', 'font_size', int(self.settings['font_size']))
        cfg.set('shell', 'history_file', self.settings['history_file'])
        try:
            with open(_config_path(), 'wb') as f:
                cfg.write(f)
        except Exception:
            self._writeln("[warn] Could not save settings to %s" % _config_path())

    # ---------- History ----------

    def _read_history_file(self):
        path = self.settings.get('history_file') or _default_history_path()
        try:
            if os.path.isfile(path):
                with open(path, 'rb') as f:
                    return [line.rstrip('\r\n') for line in f.readlines() if line.strip()]
        except Exception:
            pass
        return []

    def _append_history(self, line):
        self.history.append(line)
        self.history_pos = len(self.history)
        path = self.settings.get('history_file') or _default_history_path()
        try:
            d = os.path.dirname(path)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            with open(path, 'ab') as f:
                f.write((line + "\n"))
        except Exception:
            pass

    # ---------- Environment ----------

    def _seed_environment(self):
        # Inject items the host already imported and passed to our module scope
        # If something is missing, we attempt imports gracefully.
        env = self.locals
        # Basic builtins
        env['__name__'] = '__console__'
        env['__builtins__'] = __builtins__

        # Host-provided objects
        for name in ('window', 'application', 'planner', 'gtk'):
            if name in globals():
                env[name] = globals()[name]
        # Extra conveniences
        env['gobject'] = gobject
        try:
            env['glib'] = glib if glib is not None else __import__('glib')
        except Exception:
            pass

        # Demo helpers (documented in banner)
        env['demo_list_tasks'] = self.demo_list_tasks
        env['demo_new_task'] = self.demo_new_task
        env['demo_benchmark_iter'] = self.demo_benchmark_iter
        env['shell'] = self

    # ---------- Demo helpers ----------

    def _get_project(self):
        proj = None
        try:
            proj = window.get_project()
        except Exception:
            proj = getattr(window, 'project', None)
        return proj

    def _get_tasks_seq(self, proj):
        # Try common bindings: project.get_tasks(), or project.tasks
        tasks = []
        try:
            if hasattr(proj, 'get_tasks') and callable(proj.get_tasks):
                tasks = list(proj.get_tasks())
            elif hasattr(proj, 'tasks'):
                tasks = list(proj.tasks)
        except Exception:
            tasks = []
        return tasks

    def demo_list_tasks(self):
        """Print task names and count (safe across binding variants)."""
        proj = self._get_project()
        if not proj:
            print("No active project.")
            return
        tasks = self._get_tasks_seq(proj)
        print("Tasks: %d" % len(tasks))
        for t in tasks[:50]:
            name = None
            for key in ('get_name', 'name'):
                try:
                    v = getattr(t, key)
                    name = v() if callable(v) else v
                    if name:
                        break
                except Exception:
                    pass
            print(" - %s" % (name or "<unnamed>"))

    def demo_new_task(self, name=None):
        """Create a simple task if API variant is available."""
        proj = self._get_project()
        if not proj:
            print("No active project.")
            return
        if not name:
            name = "PythonShell Task %s" % time.strftime("%Y-%m-%d %H:%M:%S")

        created = False
        # Try several likely bindings patterns:
        try:
            if hasattr(proj, 'create_task') and callable(proj.create_task):
                t = proj.create_task(name)
                created = True
        except Exception:
            pass

        if not created:
            try:
                if hasattr(planner, 'Task'):
                    # Some bindings expose a Task constructor
                    t = planner.Task()
                    # Try set_name / set_start if available
                    for setter, val in (('set_name', name),):
                        if hasattr(t, setter):
                            getattr(t, setter)(val)
                    if hasattr(proj, 'add_task'):
                        proj.add_task(t)
                        created = True
            except Exception:
                pass

        if created:
            print("Created task: %s" % name)
        else:
            print("Could not locate a supported create/add task API on this build.")

    def demo_benchmark_iter(self, loops=1):
        """Very simple microbenchmark: iterate tasks N times and count names."""
        proj = self._get_project()
        if not proj:
            print("No active project.")
            return
        t0 = time.time()
        total = 0
        for _ in range(int(loops)):
            for t in self._get_tasks_seq(proj):
                total += 1
        dt = (time.time() - t0)
        print("Iterated %d tasks x %d loops in %.3f sec" % (total or 0, int(loops), dt))

    # ---------- I/O ----------

    def _write(self, text):
        gobject.idle_add(self._append_text, text)

    def _writeln(self, text):
        self._write(text + "\n")

    def _append_text(self, text):
        buf = self.output_buf
        end = buf.get_end_iter()
        buf.insert(end, text)
        mark = buf.create_mark("end", buf.get_end_iter(), False)
        self.output.scroll_mark_onscreen(mark)
        return False

    # ---------- Exec ----------

    def _execute_entry(self, widget):
        code = self.entry.get_text()
        if not code.strip():
            return
        # Echo prompt
        self._writeln(">>> " + code)
        self.entry.set_text("")
        self._append_history(code)
        self._exec_code_block(code)

    def _exec_code_block(self, code):
        # Redirect temporarily
        old_out, old_err = sys.stdout, sys.stderr
        class _Catcher(object):
            def write(_, s): self._write(s)
            def flush(_): pass
        sys.stdout = sys.stderr = _Catcher()
        try:
            # Compile in 'single' mode to get repr/print behavior like a REPL
            co = compile(code, "<shell>", "single")
            exec(co, self.locals, self.locals)
        except SystemExit:
            pass
        except Exception:
            traceback.print_exc()
        finally:
            sys.stdout, sys.stderr = old_out, old_err

    # ---------- Key handling (history) ----------

    def _on_keypress_history(self, entry, event):
        # Up/Down to navigate history
        key = event.keyval
        try:
            from gtk import keysyms
        except Exception:
            return False
        if key == gtk.keysyms.Up:
            if self.history and self.history_pos > 0:
                self.history_pos -= 1
                entry.set_text(self.history[self.history_pos])
                entry.set_position(len(entry.get_text()))
                return True
        elif key == gtk.keysyms.Down:
            if self.history and self.history_pos < len(self.history) - 1:
                self.history_pos += 1
                entry.set_text(self.history[self.history_pos])
            else:
                self.history_pos = len(self.history)
                entry.set_text("")
            entry.set_position(len(entry.get_text()))
            return True
        return False

    # ---------- Menu actions ----------

    def _focused_editable(self):
        w = self.get_focus()
        if isinstance(w, gtk.TextView) or isinstance(w, gtk.Entry):
            return w
        return None

    def _on_cut(self, *_):
        w = self._focused_editable()
        if isinstance(w, gtk.TextView):
            w.get_buffer().cut_clipboard(gtk.clipboard_get(), True)
        elif isinstance(w, gtk.Entry):
            w.cut_clipboard()
    def _on_copy(self, *_):
        w = self._focused_editable()
        if isinstance(w, gtk.TextView):
            w.get_buffer().copy_clipboard(gtk.clipboard_get())
        elif isinstance(w, gtk.Entry):
            w.copy_clipboard()
    def _on_paste(self, *_):
        w = self._focused_editable()
        if isinstance(w, gtk.TextView):
            w.get_buffer().paste_clipboard(gtk.clipboard_get(), None, True)
        elif isinstance(w, gtk.Entry):
            w.paste_clipboard()

    def _on_open_file(self, *_):
        dlg = gtk.FileChooserDialog(
            title="Execute Python File",
            parent=self,
            action=gtk.FILE_CHOOSER_ACTION_OPEN,
            buttons=(gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                     gtk.STOCK_OPEN, gtk.RESPONSE_OK))
        filt = gtk.FileFilter()
        filt.set_name("Python files (*.py)")
        filt.add_pattern("*.py")
        dlg.add_filter(filt)
        resp = dlg.run()
        if resp == gtk.RESPONSE_OK:
            path = dlg.get_filename()
            self._writeln("# execfile('%s')" % path)
            try:
                code = open(path, 'rb').read()
            except Exception as e:
                self._writeln("[error] %s" % e)
            else:
                self._exec_code_block(code)
        dlg.destroy()

    def _on_settings(self, *_):
        SettingsDialog(self).run()

    def _on_destroy(self, *_):
        # Nothing special; window may be reopened from View menu
        pass

# -------------------------------
# Settings dialog
# -------------------------------

class SettingsDialog(gtk.Dialog):
    def __init__(self, shell):
        gtk.Dialog.__init__(self, "Python Shell Settings", shell,
                            gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
                            (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                             gtk.STOCK_APPLY, gtk.RESPONSE_APPLY))
        self.shell = shell
        self.set_default_size(420, 220)

        box = self.get_content_area()
        grid = gtk.Table(rows=4, columns=3, homogeneous=False)
        grid.set_row_spacings(6)
        grid.set_col_spacings(6)
        box.add(grid)

        # Foreground color
        lbl_fg = gtk.Label("Foreground:")
        lbl_fg.set_alignment(0, 0.5)
        grid.attach(lbl_fg, 0, 1, 0, 1)
        self.btn_fg = gtk.ColorButton()
        try:
            self.btn_fg.set_color(gtk.gdk.color_parse(self.shell.settings['fg']))
        except Exception:
            pass
        grid.attach(self.btn_fg, 1, 3, 0, 1)

        # Background color
        lbl_bg = gtk.Label("Background:")
        lbl_bg.set_alignment(0, 0.5)
        grid.attach(lbl_bg, 0, 1, 1, 2)
        self.btn_bg = gtk.ColorButton()
        try:
            self.btn_bg.set_color(gtk.gdk.color_parse(self.shell.settings['bg']))
        except Exception:
            pass
        grid.attach(self.btn_bg, 1, 3, 1, 2)

        # Font size
        lbl_fs = gtk.Label("Font size:")
        lbl_fs.set_alignment(0, 0.5)
        grid.attach(lbl_fs, 0, 1, 2, 3)
        adj = gtk.Adjustment(float(self.shell.settings['font_size']), 6.0, 48.0, 1.0, 2.0)
        self.spin_fs = gtk.SpinButton(adj, climb_rate=1.0, digits=0)
        grid.attach(self.spin_fs, 1, 3, 2, 3)

        # History file chooser
        lbl_h = gtk.Label("History file:")
        lbl_h.set_alignment(0, 0.5)
        grid.attach(lbl_h, 0, 1, 3, 4)
        self.hist_btn = gtk.FileChooserButton("Choose history file")
        # Ensure a default path/directory exists
        hist_path = self.shell.settings.get('history_file') or _default_history_path()
        try:
            d = os.path.dirname(hist_path)
            if d and not os.path.isdir(d):
                os.makedirs(d)
        except Exception:
            pass
        try:
            self.hist_btn.set_filename(hist_path)
        except Exception:
            pass
        grid.attach(self.hist_btn, 1, 3, 3, 4)

        self.show_all()

    def run(self):
        resp = gtk.Dialog.run(self)
        if resp == gtk.RESPONSE_APPLY:
            # Persist settings
            try:
                fg = self.btn_fg.get_color()
                bg = self.btn_bg.get_color()
                def _to_hex(c):
                    # GdkColor channels are 0..65535
                    return "#%02X%02X%02X" % (c.red/257, c.green/257, c.blue/257)
                self.shell.settings['fg'] = _to_hex(fg)
                self.shell.settings['bg'] = _to_hex(bg)
            except Exception:
                pass

            try:
                self.shell.settings['font_size'] = int(self.spin_fs.get_value())
            except Exception:
                pass

            try:
                path = self.hist_btn.get_filename()
                if path:
                    self.shell.settings['history_file'] = path
            except Exception:
                pass

            self.shell._save_settings()
            self.shell._apply_settings_to_widgets()
        self.destroy()

# -------------------------------
# Planner menu integration
# -------------------------------

_shell_singleton = {'win': None}

def _open_shell_action(action, *args):
    w = _shell_singleton.get('win')
    if w is None or not gtk.Widget.flags(w) & gtk.VISIBLE:
        w = PlannerPythonShell()
        _shell_singleton['win'] = w
    else:
        w.present()

# Add "Python Shell" into the main "View" menu of Planner using GtkUIManager,
# following the same mechanism as the standard example plugin.
_ui_xml = """
<ui>
  <menubar name='MenuBar'>
    <menu action='View'>
      <placeholder name='PlannerViewExtra'>
        <menuitem action='PythonShellAction'/>
      </placeholder>
    </menu>
  </menubar>
</ui>
"""

def _install_menu_item():
    try:
        uimgr = window.get_ui_manager()
    except Exception:
        # Fallback attribute name if bindings differ
        uimgr = getattr(window, 'ui_manager', None)

    if uimgr is None:
        # As a last resort, try the stock getter again and bail if unavailable
        raise RuntimeError("No UI manager available on Planner window")

    group = gtk.ActionGroup('PlannerPythonShellActions')
    group.add_actions([
        ('PythonShellAction', None, 'Python Shell', None,
         'Open the Planner Python Shell', _open_shell_action),
    ])
    uimgr.insert_action_group(group, 0)

    try:
        uimgr.add_ui_from_string(_ui_xml)
    except Exception:
        # If placeholder name doesn't exist on this build, inject directly under View
        _ui_xml_fallback = """
        <ui>
          <menubar name='MenuBar'>
            <menu action='View'>
              <menuitem action='PythonShellAction'/>
            </menu>
          </menubar>
        </ui>
        """
        uimgr.add_ui_from_string(_ui_xml_fallback)

    uimgr.ensure_update()

# Install immediately when the script is loaded by planner-python-plugin
try:
    _install_menu_item()
    print "Planner Python Shell plugin loaded."
except Exception as e:
    print "Planner Python Shell plugin failed to load:", e
