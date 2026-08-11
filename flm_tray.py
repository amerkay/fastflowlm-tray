#!/usr/bin/env python3
"""
flm-tray: a Plasma 6 tray control for the `flm.service` systemd user unit.

The tray does not own the server's process. systemd does — it grants
LimitMEMLOCK=infinity and restarts on failure. This process sends systemctl
verbs, polls the unit's state, and follows the journal for load/generate phase.

Nothing starts automatically: the tray comes up grey and waits. The chosen model
is written to ~/.config/flm-tray/env, which the unit reads as FLM_MODEL. The
server is stopped on quit and after IDLE_STOP_S without a request — ~20 GB of
pinned DRAM is not worth holding idle.

Note FLM_MODEL only picks what is *preloaded*: flm hot-swaps per request, so the
tooltip reports the model actually resident, taken from the journal.

All decision logic lives in flm_state.py (pure, tested); this file is the shell.
Install the unit before first use; see CLAUDE.md.
"""

import math
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from PyQt6.QtCore import (
    QLockFile, QProcess, QProcessEnvironment, QSettings, QStandardPaths,
    QTimer, Qt,
)
from PyQt6.QtGui import (
    QAction, QActionGroup, QBrush, QColor, QIcon, QPainter, QPixmap,
)
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from flm_state import (
    COLOUR_IDLE, COLOUR_RUNNING, model_dir_for_line, parse_model_list,
    phase_for_line, read_model, resolve_model_tag, should_idle_stop,
    visual_state, write_model,
)

UNIT = "flm.service"
POLL_MS = 2000
IDLE_STOP_S = 30 * 60

JOURNAL_CMD = ["journalctl", "--user", "-u", UNIT, "-n", "200", "-f"]
# -o cat strips the syslog prefix, leaving flm's own line verbatim.
JOURNAL_FOLLOW = ["--user", "-u", UNIT, "-n", "50", "-f", "-o", "cat"]

# Read by flm.service as EnvironmentFile; also our record of the last model used.
ENV_FILE = Path.home() / ".config" / "flm-tray" / "env"
FLM = shutil.which("flm") or str(Path.home() / ".local" / "bin" / "flm")

# Breathing pulse while generating: one cycle ≈ 1.7 s, alpha 110→255. Deliberately
# shallow — it should read as "alive", not as an alert.
# Breeze draws the submenu arrow flush against the label, and only the text
# width feeds the item's size hint — so pad the title to make room for it.
# Non-breaking, because trailing ASCII spaces are liable to be trimmed.
TITLE_PAD = "  "

BREATH_FRAMES = 24
BREATH_MS = 70
BREATH_ALPHA = tuple(
    round(110 + 145 * (0.5 - 0.5 * math.cos(2 * math.pi * i / BREATH_FRAMES)))
    for i in range(BREATH_FRAMES)
)


def make_dot_icon(colour: str, alpha: int = 255) -> QIcon:
    """Build a coloured-circle icon at runtime so we don't depend on any
    particular icon theme being installed."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    c = QColor(colour)
    c.setAlpha(alpha)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(c))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(6, 6, 52, 52)
    p.end()
    return QIcon(pix)


def unit_props() -> tuple[str, str]:
    """(LoadState, ActiveState) for UNIT.

    `systemctl show` succeeds even for an unknown unit, which is what lets us
    tell "not installed" apart from "installed but stopped" — `is-active`
    reports both as inactive.
    """
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", UNIT,
             "--property=LoadState", "--property=ActiveState"],
            capture_output=True, text=True, timeout=2,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown", "unknown"
    props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return props.get("LoadState", "unknown"), props.get("ActiveState", "unknown")


class FlmTray:
    def __init__(self, app: QApplication) -> None:
        self.app = app
        self._icons: dict[tuple[str, int], QIcon] = {}
        self._shown: tuple | None = None
        self._models: list[tuple[str, bool]] | None = None  # None = not scanned
        self._model_actions: dict[str, QAction] = {}
        self._scan_failed = False
        self._phase: str | None = None      # journal: loading / busy / ready
        self._loaded_dir: str | None = None  # journal: model actually resident
        self._last_used = time.monotonic()
        self._breath_colour = COLOUR_RUNNING
        self._frame = 0
        # Held (not detached) so a second left-click reuses the open terminal.
        self._log_proc = QProcess()
        self._settings = QSettings("flm-tray", "flm-tray")

        # Needs an icon before show(); Qt warns and may not draw otherwise.
        self.tray = QSystemTrayIcon(self._icon(COLOUR_IDLE))
        self._build_menu()
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

        self._anim = QTimer(interval=BREATH_MS, timeout=self._tick_breath)
        # Also keeps the interpreter ticking so Ctrl-C reaches Python.
        self._poll = QTimer(interval=POLL_MS, timeout=self._refresh)
        self._poll.start()

        # `flm list` is slow enough to stutter the GUI, so it runs async and the
        # menu fills itself in when the result lands.
        env = QProcessEnvironment.systemEnvironment()
        env.insert("FLM_DISABLE_UPDATE_CHECK", "1")  # else `list` re-pulls stale models
        self._scan = QProcess()
        self._scan.setProcessEnvironment(env)
        self._scan.finished.connect(self._on_scan_done)
        # A binary that fails to start emits errorOccurred and never finished,
        # which would leave the menu saying "scanning…" forever.
        self._scan.errorOccurred.connect(self._on_scan_failed)

        self._jbuf = ""
        self._journal = QProcess()
        self._journal.readyReadStandardOutput.connect(self._read_journal)
        self._journal.finished.connect(
            lambda *_: QTimer.singleShot(2000, self._start_journal)
        )
        app.aboutToQuit.connect(self._journal.terminate)

        self._start_journal()
        self._start_scan()
        self._refresh()

    # -- menu ---------------------------------------------------------------

    def _build_menu(self) -> None:
        menu = self.menu = QMenu()  # kept alive: setContextMenu does not take ownership
        self.act_start = QAction("Start", menu, triggered=lambda: self._systemctl("start"))
        self.act_stop = QAction("Stop", menu, triggered=lambda: self._systemctl("stop"))
        self.act_restart = QAction("Restart", menu, triggered=lambda: self._systemctl("restart"))

        self.model_menu = QMenu("Model", menu)
        self.model_menu.aboutToShow.connect(self._sync_model_menu)

        self.act_idle = QAction(f"Stop after {IDLE_STOP_S // 60} minutes idle",
                                menu, checkable=True)
        self.act_idle.setChecked(self._settings.value("idleStop", True, type=bool))
        self.act_idle.toggled.connect(self._set_idle_stop)

        for act in (self.act_start, self.act_stop, self.act_restart):
            menu.addAction(act)
        menu.addSeparator()
        menu.addMenu(self.model_menu)
        menu.addSeparator()
        menu.addAction(self.act_idle)
        menu.addSeparator()
        menu.addAction(QAction("View logs", menu, triggered=self.view_logs))
        menu.addAction(QAction("Stop server && quit", menu, triggered=self.quit))

        self.tray.setContextMenu(menu)
        self._build_model_items()

    def _build_model_items(self) -> None:
        self.model_menu.clear()
        self._model_actions.clear()
        group = self._model_group = QActionGroup(self.model_menu)

        for name, outdated in self._models or []:
            act = QAction(f"{name} (outdated)" if outdated else name,
                          group, checkable=True)
            act.triggered.connect(lambda _, n=name: self._choose_model(n))
            self.model_menu.addAction(act)
            self._model_actions[name] = act
        if not self._models:
            placeholder = (f"({Path(FLM).name} not found)" if self._scan_failed
                           else "(scanning…)" if self._models is None
                           else "(none downloaded)")
            self.model_menu.addAction(
                QAction(placeholder, self.model_menu, enabled=False)
            )
        self.model_menu.addSeparator()
        self.model_menu.addAction(QAction("Rescan", self.model_menu, triggered=self._rescan))
        self._sync_model_menu()

    def _sync_model_menu(self) -> None:
        """Re-read the env file so the radio can't drift from what the unit will
        actually load — it changes outside our clicks (rescan, manual edit)."""
        current = read_model(ENV_FILE)
        for name, act in self._model_actions.items():
            act.setChecked(name == current)

    def _start_scan(self) -> None:
        if self._scan.state() == QProcess.ProcessState.NotRunning:
            self._scan.start(FLM, ["list"])

    def _on_scan_done(self, *_) -> None:
        text = bytes(self._scan.readAllStandardOutput()).decode(errors="replace")
        self._models = parse_model_list(text)
        self._build_model_items()
        self._refresh()  # the tag list is what resolves _loaded_dir for the tooltip

    def _on_scan_failed(self, *_) -> None:
        self._scan_failed = True
        self._models = []
        self._build_model_items()

    def _set_idle_stop(self, on: bool) -> None:
        self._settings.setValue("idleStop", on)
        # Turning it back on must not kill a server that has already sat idle
        # past the limit while the option was off.
        self._last_used = time.monotonic()

    def _rescan(self) -> None:
        self._models = None
        self._scan_failed = False
        self._build_model_items()
        self._start_scan()

    # -- journal ------------------------------------------------------------

    def _start_journal(self) -> None:
        self._jbuf = ""
        self._journal.start("journalctl", JOURNAL_FOLLOW)

    def _read_journal(self) -> None:
        self._jbuf += bytes(self._journal.readAllStandardOutput()).decode(errors="replace")
        *lines, self._jbuf = self._jbuf.split("\n")
        for line in lines:
            if (phase := phase_for_line(line)) is not None:
                self._phase = phase
                self._last_used = time.monotonic()
            if (model_dir := model_dir_for_line(line)) is not None:
                self._loaded_dir = model_dir
        self._refresh()

    # -- state --------------------------------------------------------------

    def _icon(self, colour: str, alpha: int = 255) -> QIcon:
        return self._icons.setdefault((colour, alpha), make_dot_icon(colour, alpha))

    def _tick_breath(self) -> None:
        self._frame = (self._frame + 1) % BREATH_FRAMES
        self.tray.setIcon(self._icon(self._breath_colour, BREATH_ALPHA[self._frame]))

    def _paint(self, colour: str, tooltip: str, animate: bool) -> None:
        self.tray.setToolTip(tooltip)
        if not animate:
            self._anim.stop()
            self.tray.setIcon(self._icon(colour))
            return
        self._breath_colour = colour
        if not self._anim.isActive():
            self._frame = 0
            self.tray.setIcon(self._icon(colour, BREATH_ALPHA[0]))  # don't wait a tick
            self._anim.start()

    def _serving(self) -> str | None:
        """Model actually resident, resolved from the journal's directory name."""
        if self._loaded_dir is None:
            return None
        return resolve_model_tag(self._loaded_dir, [n for n, _ in self._models or []])

    def _refresh(self) -> None:
        load, active = unit_props()
        limit = IDLE_STOP_S if self.act_idle.isChecked() else math.inf
        if should_idle_stop(active, self._phase,
                            time.monotonic() - self._last_used, limit):
            self._systemctl("stop")
            return

        configured, serving = read_model(ENV_FILE), self._serving()
        state = (load, active, self._phase, configured, serving)
        if state == self._shown:
            return
        self._shown = state

        self.model_menu.setTitle(f"Model ({configured or 'unit default'}){TITLE_PAD}")
        self._sync_model_menu()

        colour, note, animate = visual_state(load, active, self._phase)
        if load != "loaded":
            note = f"{UNIT} not installed ({load})"
            shown = None
        else:
            shown = serving if active == "active" and serving else configured
        self._paint(colour, f"flm: {note} — {shown or 'unit default'}", animate)

        up = active in ("active", "activating")
        self.act_start.setEnabled(load == "loaded" and not up)
        self.act_stop.setEnabled(load == "loaded" and active != "inactive")
        self.act_restart.setEnabled(load == "loaded" and up)

    # -- actions ------------------------------------------------------------

    def _systemctl(self, verb: str) -> None:
        self._phase = None  # stale until the journal says otherwise
        self._loaded_dir = None
        self._last_used = time.monotonic()  # a fresh start is not idle
        QProcess.startDetached("systemctl", ["--user", verb, UNIT])
        QTimer.singleShot(400, self._refresh)  # reflect it before the next poll

    def _choose_model(self, name: str) -> None:
        """Record the model and bring the server up on it.

        `restart` rather than stop-then-start: it is one systemd job, so the
        new instance is ordered after the old one has finished unloading
        (up to TimeoutStopSec), and it starts a stopped unit too.
        """
        write_model(ENV_FILE, name)
        self._systemctl("restart")

    def quit(self) -> None:
        """Stop the server, then exit. systemd carries the stop to completion
        (TimeoutStopSec) on its own — the detached call outlives us."""
        self._systemctl("stop")
        self.app.quit()

    def view_logs(self) -> None:
        if self._log_proc.state() != QProcess.ProcessState.NotRunning:
            return
        term = next((t for t in ("konsole", "x-terminal-emulator", "xterm")
                     if shutil.which(t)), None)
        if term is None:
            QMessageBox.information(
                None, "flm-tray", "Run:\n" + " ".join(JOURNAL_CMD)
            )
            return
        self._log_proc.start(term, ["-e", *JOURNAL_CMD])

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.view_logs()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self._systemctl("restart")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("flm-tray")
    app.setQuitOnLastWindowClosed(False)

    runtime = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.RuntimeLocation
    ) or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)
    lock = QLockFile(str(Path(runtime) / "flm-tray.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        return 0  # already running (autostart + manual launch)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "flm-tray", "No system tray detected on this session.")
        return 1

    tray_app = FlmTray(app)
    signal.signal(signal.SIGINT, lambda *_: tray_app.quit())
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
