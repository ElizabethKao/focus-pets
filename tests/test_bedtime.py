"""Bedtime: crossing midnight, waking up after the computer slept, and focus sessions."""
import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from bedtime import Bedtime, is_night, parse_time  # noqa: E402
from context import AppCategories, ContextTracker  # noqa: E402
from pet import SIZES, Pet  # noqa: E402

app = QApplication.instance() or QApplication([])


class FakeCoach:
    active = False


class IsNightTest(unittest.TestCase):
    def at(self, h, m):
        return dt.datetime(2026, 11, 7, h, m)

    def test_crosses_midnight(self):
        bed, wake = parse_time("23:30"), parse_time("08:00")
        self.assertFalse(is_night(self.at(23, 29), bed, wake))
        self.assertTrue(is_night(self.at(23, 30), bed, wake))
        self.assertTrue(is_night(self.at(2, 0), bed, wake))
        self.assertTrue(is_night(self.at(7, 59), bed, wake))
        self.assertFalse(is_night(self.at(8, 0), bed, wake))
        self.assertFalse(is_night(self.at(15, 0), bed, wake))

    def test_after_midnight_bedtime(self):
        bed, wake = parse_time("01:00"), parse_time("09:00")
        self.assertFalse(is_night(self.at(23, 0), bed, wake))
        self.assertTrue(is_night(self.at(3, 0), bed, wake))


class BedtimeFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = dt.datetime(2026, 11, 7, 23, 0)
        self.mono = [1000.0]
        tracker = ContextTracker(AppCategories(Path(self.tmp.name) / "apps.json"),
                                 app_source=lambda: "Code")
        self.coach = FakeCoach()
        self.pets = [Pet(n.capitalize(), ROOT / "pets" / n, SIZES["Tiny"], 300 + 200 * i)
                     for i, n in enumerate(("peep", "honey"))]
        for p in self.pets:
            p.show()
            p.enter("idle", 5)
        self.bt = Bedtime(self.pets, tracker, self.coach, "23:30", "08:00", enabled=True,
                          clock=lambda: self.now, monotonic=lambda: self.mono[0])

    def tearDown(self):
        for p in self.pets:
            p.bubble.hide()
            p.hide()
        self.tmp.cleanup()

    def advance(self, minutes=0, seconds=0):
        self.now += dt.timedelta(minutes=minutes, seconds=seconds)
        self.mono[0] += minutes * 60 + seconds
        self.bt.check()

    def test_goes_to_bed_then_wakes_after_computer_slept(self):
        self.advance(minutes=29)
        self.assertFalse(self.bt.asleep)
        self.advance(minutes=1)                       # 11:30 PM
        self.assertTrue(self.bt.asleep)
        self.assertTrue(all(p.bedtime for p in self.pets))
        self.assertIn("bedtime", self.pets[0].bubble.text)
        self.advance(seconds=7)                       # after saying goodnight, they lie down
        self.assertTrue(all(p.state == "sleeping" for p in self.pets))

        # Computer sleeps all night; first check after waking is 9:15 AM
        self.now = dt.datetime(2026, 11, 8, 9, 15)
        self.mono[0] += 10 * 3600
        self.bt.check()
        self.assertFalse(self.bt.asleep)
        self.assertFalse(any(p.bedtime for p in self.pets))
        self.assertIn("good morning", self.pets[0].bubble.text)

    def test_stays_asleep_when_its_timer_ends(self):
        self.advance(minutes=30)
        self.advance(seconds=7)
        pet = self.pets[0]
        pet.y_ = pet.screen_area().bottom() + 1 - pet.height()   # standing on the floor
        pet.state_timer = 0
        pet.tick()
        self.assertEqual(pet.state, "sleeping")

    def test_focus_session_postpones_bedtime(self):
        self.coach.active = True
        self.advance(minutes=45)
        self.assertFalse(self.bt.asleep)
        self.coach.active = False                     # session ends at 11:45 PM
        self.advance(seconds=1)
        self.assertTrue(self.bt.asleep)

    def test_turning_it_off_wakes_them_quietly(self):
        self.advance(minutes=30)
        self.bt.enabled = False
        self.pets[0].bubble.text = ""
        self.advance(seconds=1)
        self.assertFalse(self.bt.asleep)
        self.assertNotIn("good morning", self.pets[0].bubble.text)


if __name__ == "__main__":
    unittest.main()
