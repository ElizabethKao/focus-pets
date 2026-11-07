"""Runs a whole simulated focus session headlessly: fake apps, fake clock, real pets.

Run:  QT_QPA_PLATFORM=offscreen python -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from brain import Brain  # noqa: E402
from context import AppCategories, ContextTracker  # noqa: E402
from focus import FocusCoach, Stats  # noqa: E402
from pet import SIZES, Pet  # noqa: E402

app = QApplication.instance() or QApplication([])


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class FocusSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.clock = Clock()
        self.current_app = "Visual Studio Code"
        cats = AppCategories(d / "apps.json")
        self.tracker = ContextTracker(cats, app_source=lambda: self.current_app)
        self.pets = [Pet(n.capitalize(), ROOT / "pets" / n, SIZES["Tiny"], 300 + 200 * i)
                     for i, n in enumerate(("peep", "honey"))]
        for p in self.pets:
            p.show()
        self.brain = Brain(d / "brain.json")
        self.stats = Stats(d / "stats.json")
        self.log = d / "events.jsonl"
        self.coach = FocusCoach(self.pets, self.tracker, self.brain, self.stats,
                                log_path=self.log, clock=self.clock)
        for p in self.pets:
            p.on_click = self.coach.on_click

    def tearDown(self):
        for p in self.pets:
            p.bubble.hide()
            p.hide()
        self.tmp.cleanup()

    def run_for(self, seconds):
        for _ in range(int(seconds)):
            self.clock.t += 1
            self.coach.tick()
            for p in self.pets:
                p.last -= 0.05
                p.tick()

    def test_full_session(self):
        self.coach.start(25)
        self.assertTrue(all(p.in_focus for p in self.pets))

        self.run_for(300)                       # 5 min of coding
        self.current_app = "Discord"            # get distracted
        self.run_for(20)
        self.assertIsNotNone(self.coach.pending)
        self.assertEqual(self.coach.pending["reason"], "opened a distracting app")
        self.assertEqual(self.coach.pending["category"], "distracting")

        self.current_app = "Visual Studio Code"  # come back
        self.run_for(150)

        for _ in range(4):                      # poke the pets a lot
            self.coach.on_click(self.pets[0])
        self.run_for(2)
        self.assertEqual(self.coach.pending["reason"], "lots of clicking")
        self.assertIn(self.coach.pending["action"],
                      ("idle", "nap", "turn_away", "hide", "retreat"))

        self.run_for(25 * 60)                   # finish the session
        self.assertFalse(self.coach.active)
        self.assertEqual(self.stats["sessions"], 1)
        self.assertEqual(self.stats["cookies"], 4)

        entries = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertGreaterEqual(len(entries), 4)   # fewer while pets hide (no decisions then)
        text = self.log.read_text()
        self.assertNotIn("Discord", text)        # privacy: no app names in the log
        self.assertNotIn("Visual Studio", text)
        learned = sum(n for n, _ in self.brain.summary().values())
        self.assertEqual(learned, len(entries))

    def test_spotify_is_neutral_by_default(self):
        self.current_app = "Spotify"
        self.tracker.poll(self.clock())
        self.assertEqual(self.tracker.category, "neutral")


if __name__ == "__main__":
    unittest.main()
