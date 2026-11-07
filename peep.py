"""Peep & Honey: tiny pastel desktop pets who learn to help you focus.

Run with:  python peep.py
Each folder in pets/ is one pet (made by tools/prepare_sprites.py).
"""
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QTime, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFormLayout, QMenu,
                               QMessageBox, QSystemTrayIcon, QTimeEdit)

from bedtime import Bedtime
from brain import ACTIONS, Brain
from context import APP_DETECTION, DATA_DIR, AppCategories, ContextTracker
from effects import mac_tweaks, set_on_top
from focus import FocusCoach, Stats
from pet import SIZES, Pet

HERE = Path(__file__).resolve().parent
FAST_MS = 16    # ~60 fps while pets move
SLOW_MS = 100   # 10 fps while they sit, blink, or sleep (saves battery)
PETS_DIR = HERE / "pets"


class PetApp:
    def __init__(self, app, test_mode=False):
        self.app = app
        self.test_mode = test_mode
        self.settings = QSettings("ElizabethKao", "Peep")
        self.size_name = self.settings.value("size", "Small")
        if self.size_name not in SIZES:
            self.size_name = "Small"

        folders = sorted((d for d in PETS_DIR.iterdir() if (d / "meta.json").exists()),
                         key=lambda d: d.name != "peep")
        if not folders:
            raise SystemExit("No pets found. Run: python tools/prepare_sprites.py")

        area = QGuiApplication.primaryScreen().availableGeometry()
        self.pets = []
        for i, folder in enumerate(folders):
            start_x = area.left() + area.width() * (i + 1) / (len(folders) + 1)
            self.pets.append(Pet(folder.name.capitalize(), folder, SIZES[self.size_name], start_x))

        # Test mode keeps its own brain, stats, and log so testing doesn't skew real learning.
        suffix = "-test" if test_mode else ""
        self.stats = Stats(DATA_DIR / f"stats{suffix}.json")
        self.categories = AppCategories()
        self.tracker = ContextTracker(self.categories)
        self.brain = Brain(DATA_DIR / f"brain{suffix}.json")
        self.coach = FocusCoach(self.pets, self.tracker, self.brain, self.stats,
                                log_path=DATA_DIR / f"events{suffix}.jsonl")
        if test_mode:
            self.coach.use_test_timing()
            print("TEST MODE: decisions every 20s, distracting apps count after 5s", flush=True)
        for pet in self.pets:
            pet.on_click = self.coach.on_click
            pet.on_drag = self.coach.on_drag
        self.bedtime = Bedtime(self.pets, self.tracker, self.coach,
                               bed=self.settings.value("bed", "23:30"),
                               wake=self.settings.value("wake", "08:00"),
                               enabled=self.settings.value("bedtime_on", "false") == "true")
        self.all_hidden = False

        self._build_menu()
        set_on_top([], self.settings.value("on_top", "true") == "true")

        for pet in self.pets:
            if self.settings.value(f"show_{pet.name}", "true") == "true":
                pet.show()
                mac_tweaks(pet)

        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(FAST_MS)
        self.coach_timer = QTimer()
        self.coach_timer.timeout.connect(self.slow_tick)
        self.coach_timer.start(1000)  # context, decisions, and bedtime once a second

    def tick(self):
        for pet in self.pets:
            pet.tick()
        # Smooth 60 fps only while something moves; 10 fps when the pets are sitting or asleep.
        want = FAST_MS if any(p.is_moving() for p in self.pets) else SLOW_MS
        if self.timer.interval() != want:
            self.timer.setInterval(want)

    def slow_tick(self):
        self.coach.tick()
        self.bedtime.check()

    # ---------- Menus ----------

    def _build_menu(self):
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self.refresh_menu)

        self.status_action = self.menu.addAction("")
        self.status_action.setEnabled(False)
        self.start25 = self.menu.addAction("Start a 25-minute focus session", lambda: self.start_focus(25))
        self.start50 = self.menu.addAction("Start a 50-minute focus session", lambda: self.start_focus(50))
        self.start2 = self.menu.addAction("Start a 2-minute TEST session", lambda: self.start_focus(2))
        self.start2.setVisible(self.test_mode)
        self.end_action = self.menu.addAction("End focus session early", self.end_focus)
        if self.test_mode:
            try_menu = self.menu.addMenu("Try a behavior")
            for action in ACTIONS:
                try_menu.addAction(action, lambda a=action: self.try_behavior(a))
        self.menu.addSeparator()

        for pet in self.pets:
            sub = self.menu.addMenu(pet.icon, pet.name)
            self._fill_pet_menu(sub, pet)
            pet.menu = QMenu()
            pet.menu.aboutToShow.connect(self.refresh_menu)
            self._fill_pet_menu(pet.menu, pet)
            pet.menu.addSeparator()
            pet.menu.addAction("Quit", QApplication.quit)
        self.menu.addSeparator()

        size_menu = self.menu.addMenu("Size")
        group = QActionGroup(size_menu)
        for name in SIZES:
            a = QAction(name, size_menu, checkable=True)
            a.setChecked(name == self.size_name)
            a.triggered.connect(lambda _=False, n=name: self.set_size(n))
            group.addAction(a)
            size_menu.addAction(a)

        bed_menu = self.menu.addMenu("Bedtime")
        self.bed_label = bed_menu.addAction("")
        self.bed_label.setEnabled(False)
        self.bed_toggle = QAction("Bedtime reminders", bed_menu, checkable=True)
        self.bed_toggle.setChecked(self.bedtime.enabled)
        self.bed_toggle.toggled.connect(self.set_bedtime_on)
        bed_menu.addAction(self.bed_toggle)
        bed_menu.addAction("Set bedtime and wake-up times…", self.edit_bedtime)

        self.on_top_action = QAction("Stay on top of apps", self.menu, checkable=True)
        self.on_top_action.setChecked(self.settings.value("on_top", "true") == "true")
        self.on_top_action.toggled.connect(self.set_on_top)
        self.menu.addAction(self.on_top_action)

        self.hide_all_action = QAction("Hide all pets", self.menu, checkable=True)
        self.hide_all_action.toggled.connect(self.set_all_hidden)
        self.menu.addAction(self.hide_all_action)
        self.menu.addSeparator()

        self.menu.addAction("What they've learned…", self.show_learning)
        self.menu.addAction("Edit app categories…", self.edit_categories)
        self.menu.addAction("Reset what they've learned…", self.reset_learning)
        self.menu.addAction("About Peep && Honey…", self.show_about)
        self.menu.addSeparator()
        self.menu.addAction("Quit", QApplication.quit)

        self.tray = QSystemTrayIcon(self.pets[0].icon)
        self.tray.setToolTip("Peep")
        self.tray.setContextMenu(self.menu)
        self.tray.show()

    def _fill_pet_menu(self, menu, pet):
        feed = menu.addAction("Feed 🍪", lambda p=pet: self.feed(p))
        pet.feed_actions = getattr(pet, "feed_actions", []) + [feed]
        menu.addAction("Nap time 💤", lambda p=pet: p.nap())
        menu.addAction("Wake up ☀️", lambda p=pet: p.wake())
        menu.addAction("Hold up a sign 🪧", lambda p=pet: p.hold_sign())
        follow = QAction("Follow my cursor", menu, checkable=True)
        follow.toggled.connect(lambda on, p=pet: self.set_follow(p, on))
        pet.follow_actions.append(follow)
        menu.addAction(follow)
        menu.addAction("Drop at my cursor", lambda p=pet: p.summon())
        menu.addSeparator()
        show = QAction(f"Show {pet.name}", menu, checkable=True)
        show.setChecked(self.settings.value(f"show_{pet.name}", "true") == "true")
        show.toggled.connect(lambda on, p=pet: self.set_visible(p, on))
        pet.show_actions.append(show)
        menu.addAction(show)

    def refresh_menu(self):
        active = self.coach.active
        if active:
            left = self.coach.minutes_left()
            self.status_action.setText(
                "⏳ Focusing: less than 1 min left" if left < 1 else
                f"⏳ Focusing: {left} min left")
        else:
            streak = self.stats["streak"]
            self.status_action.setText(
                f"🍪 {self.stats['cookies']} cookies · {self.stats['sessions']} sessions"
                + (f" · {streak}-day streak" if streak > 1 else ""))
        self.start25.setVisible(not active)
        self.start50.setVisible(not active)
        self.start2.setVisible(self.test_mode and not active)
        self.end_action.setVisible(active)
        self.bed_label.setText(f"🌙 {self.bedtime.describe()}")
        for pet in self.pets:
            for a in pet.feed_actions:
                a.setText(f"Feed 🍪 ({self.stats['cookies']} left)")

    @staticmethod
    def _sync(actions, on):
        for a in actions:
            if a.isChecked() != on:
                a.blockSignals(True)
                a.setChecked(on)
                a.blockSignals(False)

    # ---------- Actions ----------

    def try_behavior(self, action):
        pet = self.coach._pick_pet()
        if pet:
            print(f"TRY {action} on {pet.name}", flush=True)
            self.coach._execute(action, pet)

    def start_focus(self, minutes):
        self.coach.start(minutes)
        self.tray.setToolTip(f"Peep: focusing for {minutes} min")
        if not APP_DETECTION:
            self.pets[0].say("I can't see which app\nyou're in on this computer", 5)

    def end_focus(self):
        self.coach.stop(completed=False)
        self.tray.setToolTip("Peep")

    def feed(self, pet):
        if self.stats["cookies"] <= 0:
            pet.say("finish a focus session\nto earn a cookie!", 4)
            return
        if pet.busy():
            return
        self.stats["cookies"] = self.stats["cookies"] - 1
        pet.feed()

    def set_follow(self, pet, on):
        self._sync(pet.follow_actions, on)
        pet.set_follow(on)

    def set_visible(self, pet, on):
        self._sync(pet.show_actions, on)
        self.settings.setValue(f"show_{pet.name}", "true" if on else "false")
        if on and not pet.isVisible():
            pet.summon()
        elif not on:
            self.coach.on_hidden(pet)
            pet.away = 0
            pet.hide()
            pet.bubble.hide()

    def set_size(self, name):
        self.size_name = name
        self.settings.setValue("size", name)
        for pet in self.pets:
            bottom = pet.y_ + pet.height()
            center = pet.x_ + pet.width() / 2
            pet.set_scale(SIZES[name])
            pet.x_, pet.y_ = center - pet.width() / 2, bottom - pet.height()

    def set_bedtime_on(self, on):
        self.bedtime.enabled = on
        self.settings.setValue("bedtime_on", "true" if on else "false")

    def edit_bedtime(self):
        dlg = QDialog()
        dlg.setWindowTitle("Bedtime")
        form = QFormLayout(dlg)
        bed = QTimeEdit(QTime.fromString(self.settings.value("bed", "23:30"), "HH:mm"))
        wake = QTimeEdit(QTime.fromString(self.settings.value("wake", "08:00"), "HH:mm"))
        for edit in (bed, wake):
            edit.setDisplayFormat("h:mm AP")
        form.addRow("Go to sleep at:", bed)
        form.addRow("Wake up at:", wake)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        bring_to_front(dlg)
        if dlg.exec() == QDialog.Accepted:
            b, w = bed.time().toString("HH:mm"), wake.time().toString("HH:mm")
            self.settings.setValue("bed", b)
            self.settings.setValue("wake", w)
            self.bedtime.set_times(b, w)
            self.bed_toggle.setChecked(True)

    def set_on_top(self, on):
        """On: pets float above your apps. Off: they live on the desktop, behind windows."""
        self.settings.setValue("on_top", "true" if on else "false")
        windows = [w for p in self.pets for w in (p, p.bubble)]
        set_on_top(windows, on)

    def set_all_hidden(self, hidden):
        """Quick way to clear the screen, e.g. for a presentation or screen share."""
        self.all_hidden = hidden
        for pet in self.pets:
            if hidden:
                pet.away = 0
                pet.hide()
                pet.bubble.hide()
            elif self.settings.value(f"show_{pet.name}", "true") == "true":
                pet.summon()

    def show_learning(self):
        s = self.stats
        lines = [f"Focus sessions: {s['sessions']}  ·  focus minutes: {s['focus_minutes']}"
                 f"  ·  streak: {s['streak']} day(s)  ·  cookies: {s['cookies']}", ""]
        lines.append("How each behavior has worked out for you (times tried, average reward):")
        for action, (n, avg) in self.brain.summary().items():
            lines.append(f"  {action}: {n}" + (f", {avg:+.2f}" if avg is not None else ""))
        lines += ["", "Most recent decision:", self.coach.last_explanation, "",
                  f"App detection: {'on' if APP_DETECTION else 'not available on this computer'}",
                  f"Data (stays on this computer): {DATA_DIR}"]
        box = QMessageBox(QMessageBox.Information, "What Peep & Honey have learned", "\n".join(lines))
        bring_to_front(box)
        box.exec()

    def edit_categories(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.categories.path)))

    def show_about(self):
        QMessageBox.about(
            None, "About Peep & Honey",
            "Peep & Honey: desktop pets that learn to help you focus.\n\n"
            "Concept, behavior and reward design, and creative direction: Elizabeth Kao.\n"
            "Character artwork generated with OpenAI's ChatGPT.\n"
            "Code written with Anthropic's Claude.\n\n"
            "Uses only the active app's category and your clicks on the pets. "
            "No screen contents, keystrokes, or browsing data. Everything stays on this computer.")

    def reset_learning(self):
        answer = QMessageBox.question(
            None, "Reset learning?",
            "Peep and Honey will forget what they've learned about you and go back to "
            "their starting rules. Your cookies and streak stay.")
        if answer == QMessageBox.Yes:
            self.brain.reset()
            self.brain.save()


def bring_to_front(widget):
    if sys.platform == "darwin":
        try:
            import AppKit
            AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        except ImportError:
            pass
    widget.raise_()
    widget.activateWindow()


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Ctrl+C in Terminal quits
    app = QApplication(sys.argv)
    app.setApplicationName("Peep")
    app.setQuitOnLastWindowClosed(False)
    pets = PetApp(app, test_mode="--test" in sys.argv)  # noqa: F841  (keeps everything alive)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
