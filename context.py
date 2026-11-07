"""What's going on right now: which app you're in, what kind of app it is, and how
much you've been poking the pets. Everything stays on your computer.

App detection uses macOS's NSWorkspace, which needs no special permissions. It sees
the app's name only (e.g. "Google Chrome"), never window titles or websites.
"""
import json
import os
import shutil
import sys
import time
from collections import deque
from pathlib import Path

DATA_DIR = Path.home() / ".peep"
DEFAULT_APPS = Path(__file__).resolve().parent / "default_apps.json"
CATEGORIES = ("productive", "neutral", "distracting")
HISTORY_SECONDS = 20 * 60

try:
    if sys.platform != "darwin":
        raise ImportError
    from AppKit import NSWorkspace
    APP_DETECTION = True
except ImportError:
    NSWorkspace = None
    APP_DETECTION = False


def frontmost_app_name():
    """Name of the app in front, None if unknown, or "self" if it's Peep itself."""
    if not APP_DETECTION:
        return None
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    if app.processIdentifier() == os.getpid():
        return "self"
    return str(app.localizedName())


class AppCategories:
    """The user-editable mapping from app names to productive / neutral / distracting."""

    def __init__(self, path=None):
        self.path = Path(path) if path else DATA_DIR / "apps.json"
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(DEFAULT_APPS, self.path)
        self.mtime = None
        self.reload_if_changed()

    def reload_if_changed(self):
        try:
            mtime = self.path.stat().st_mtime
            if mtime == self.mtime:
                return False
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return False  # keep the last good version if the file is mid-edit or invalid
        self.mtime = mtime
        self.lists = {c: [n.lower() for n in data.get(c, [])] for c in CATEGORIES}
        self.default = data.get("default", "neutral")
        if self.default not in CATEGORIES:
            self.default = "neutral"
        return True

    def category(self, app_name):
        if not app_name:
            return self.default
        name = app_name.lower()
        for cat in CATEGORIES:                      # exact names win
            if name in self.lists[cat]:
                return cat
        for cat in CATEGORIES:                      # then partial matches
            if any(entry in name for entry in self.lists[cat] if len(entry) > 2):
                return cat
        return self.default


class ContextTracker:
    """Keeps a short history of app categories and pet interactions."""

    def __init__(self, categories: AppCategories, app_source=frontmost_app_name):
        self.categories = categories
        self.app_source = app_source
        self.app = None
        self.category = categories.default
        self.since = time.time()
        self.segments = deque()        # (start, end, category) of finished app visits
        self.clicks = deque()          # timestamps of pet clicks
        self.drags = deque()           # timestamps of pets being dragged away
        self.visit = 0                 # counts separate visits to distracting apps
        self._last_reload = 0.0

    @property
    def available(self):
        return self.app_source is not frontmost_app_name or APP_DETECTION

    def poll(self, now):
        if now - self._last_reload > 5:
            self._last_reload = now
            if self.categories.reload_if_changed() and self.app:
                self.category = self.categories.category(self.app)
        name = self.app_source()
        if name in (None, "self") or name == self.app:
            self._trim(now)
            return
        if self.app is not None:
            self.segments.append((self.since, now, self.category))
        new_cat = self.categories.category(name)
        if new_cat == "distracting" and self.category != "distracting":
            self.visit += 1
        self.app, self.category, self.since = name, new_cat, now
        self._trim(now)

    def record_click(self, now):
        self.clicks.append(now)

    def record_drag(self, now):
        self.drags.append(now)

    def dwell(self, now):
        """Seconds in the current app."""
        return now - self.since

    def count(self, which, t0, t1):
        return sum(1 for t in which if t0 <= t <= t1)

    def fractions(self, t0, t1):
        """Share of time between t0 and t1 spent in each category."""
        total = max(t1 - t0, 1e-6)
        out = dict.fromkeys(CATEGORIES, 0.0)
        for start, end, cat in list(self.segments) + [(self.since, t1, self.category)]:
            overlap = min(end, t1) - max(start, t0)
            if overlap > 0:
                out[cat] += overlap / total
        return out

    def _trim(self, now):
        cutoff = now - HISTORY_SECONDS
        while self.segments and self.segments[0][1] < cutoff:
            self.segments.popleft()
        for dq in (self.clicks, self.drags):
            while dq and dq[0] < cutoff:
                dq.popleft()
