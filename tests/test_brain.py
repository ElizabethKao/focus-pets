"""Checks that the brain learns from a simulated user.

Run:  python -m unittest discover tests
"""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain import (ACTIONS, Brain, allowed_actions, compute_reward,  # noqa: E402
                   featurize)


class SimulatedUser:
    """A user whose best pet action depends on context, with noisy outcomes.

    Deliberately disagrees with the warm-start rules in one place (in neutral apps
    this user likes encouragement), so the test proves the brain can learn, not just
    repeat its starting rules.
    """
    BEST = {
        ("productive", False): "idle",
        ("neutral", False): "encourage",
        ("distracting", False): "nudge",
        ("productive", True): "hide",
    }

    def reward(self, category, clicky, action, rng):
        best = self.BEST[(category, clicky)]
        mean = 0.6 if action == best else 0.0
        return max(-1, min(1, rng.gauss(mean, 0.3)))


class BrainLearnsTest(unittest.TestCase):
    def test_learns_each_context(self):
        rng = random.Random(7)
        brain = Brain()
        user = SimulatedUser()
        contexts = list(SimulatedUser.BEST)
        picks = {c: [] for c in contexts}
        for step in range(1500):
            category, clicky = rng.choice(contexts)
            clicks = 4 if clicky else 0
            x = featurize(category, clicks, rng.uniform(30, 1800), rng.uniform(0, 900),
                          rng.uniform(8, 23), rng.random())
            allowed = allowed_actions(category, clicks, already_nudged=False, cooldown_ok=True)
            action, _ = brain.choose(x, allowed)
            brain.update(action, x, user.reward(category, clicky, action, rng))
            if step >= 1200:
                picks[(category, clicky)].append(action)

        for ctx, actions in picks.items():
            best = SimulatedUser.BEST[ctx]
            share = actions.count(best) / len(actions)
            self.assertGreater(share, 0.7, f"{ctx}: picked {best} only {share:.0%} of the time")

    def test_warm_start_is_sensible(self):
        brain = Brain()
        x = featurize("distracting", 0, 60, 300, 15, 0.4)
        action, _ = brain.choose(x, allowed_actions("distracting", 0, False, True))
        self.assertIn(action, ("nudge", "focus_sign"))
        x = featurize("productive", 0, 900, 300, 15, 0.4)
        action, _ = brain.choose(x, allowed_actions("productive", 0, False, True))
        self.assertIn(action, ("idle", "nap"))

    def test_save_and_load(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "brain.json"
            a = Brain(path)
            x = featurize("neutral", 1, 100, 100, 10, 0.2)
            a.update("encourage", x, 0.9)
            a.save()
            b = Brain(path)
            self.assertEqual(a.scores(x), b.scores(x))


class GuardrailsTest(unittest.TestCase):
    def test_clicking_a_lot_only_allows_calm_actions(self):
        acts = allowed_actions("productive", 5, False, True)
        self.assertNotIn("playful", acts)
        self.assertNotIn("encourage", acts)
        self.assertIn("hide", acts)

    def test_distracting_app_beats_recent_clicks(self):
        self.assertIn("nudge", allowed_actions("distracting", 5, False, True))

    def test_nudge_only_once_per_distracting_visit(self):
        self.assertIn("nudge", allowed_actions("distracting", 0, False, True))
        self.assertNotIn("nudge", allowed_actions("distracting", 0, True, True))

    def test_cooldown_blocks_interruptions(self):
        acts = allowed_actions("productive", 0, False, cooldown_ok=False)
        self.assertTrue(all(a in ("idle", "nap", "retreat") for a in acts))

    def test_every_allowed_action_exists(self):
        for cat in ("productive", "neutral", "distracting"):
            for clicks in (0, 4):
                for nudged in (False, True):
                    for cool in (False, True):
                        for a in allowed_actions(cat, clicks, nudged, cool):
                            self.assertIn(a, ACTIONS)


class RewardTest(unittest.TestCase):
    def test_staying_focused_beats_being_distracted(self):
        good, _ = compute_reward("idle", "productive", {"productive": 1.0}, 0, 0, False)
        bad, _ = compute_reward("idle", "productive", {"distracting": 1.0}, 0, 0, False)
        self.assertGreater(good, bad)

    def test_silence_beats_interrupting_when_focused(self):
        quiet, _ = compute_reward("idle", "productive", {"productive": 1.0}, 0, 0, False)
        loud, _ = compute_reward("playful", "productive", {"productive": 1.0}, 0, 0, False)
        self.assertGreater(quiet, loud)

    def test_nudge_that_works_is_rewarded(self):
        worked, _ = compute_reward("nudge", "distracting", {"productive": 0.7, "distracting": 0.3}, 0, 0, False)
        ignored, _ = compute_reward("nudge", "distracting", {"distracting": 1.0}, 0, 0, False)
        self.assertGreater(worked, 0.3)
        self.assertLess(ignored, 0)

    def test_hiding_the_pet_is_a_strong_negative(self):
        r, _ = compute_reward("encourage", "productive", {"productive": 1.0}, 0, 0, True)
        self.assertLess(r, 0)


if __name__ == "__main__":
    unittest.main()
