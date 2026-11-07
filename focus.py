"""Focus sessions: the loop that connects context, the brain, and the pets.

Privacy: the coach only works with app *categories*, timestamps, and your clicks on
the pets. The learning log (~/.peep/events.jsonl) never contains app names, window
titles, screen contents, keystrokes, or anything from the web.
"""
import datetime as dt
import json
import random
import time

from brain import INTRUSIVENESS, allowed_actions, compute_reward, featurize
from context import DATA_DIR

EVAL_WINDOW = 120        # seconds watched after each action before scoring it
CHECK_IN_EVERY = 120     # how often to make a decision when nothing special happens
COOLDOWN = 150           # minimum seconds between interrupting actions
DISTRACTING_AFTER = 15   # seconds in a distracting app before it counts
MIN_WINDOW = 15          # windows shorter than this aren't used for learning
LOG_MAX_BYTES = 1_000_000   # the learning log never grows past about 1 MB
LOG_KEEP_LINES = 3000
HIDE_SECONDS = 90        # how long the pets hide when they decide they're a distraction

ENCOURAGE = ["you got this!", "keep going!", "nice focus!", "proud of you!"]
NUDGES = ["back to work?", "one task at a time", "focus time!", "almost there!"]


class Stats:
    def __init__(self, path=None):
        self.path = path or DATA_DIR / "stats.json"
        self.data = {"sessions": 0, "focus_minutes": 0, "cookies": 3,
                     "streak": 0, "last_day": None}
        try:
            self.data.update(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save()

    def record_session(self, minutes):
        today = dt.date.today()
        last = dt.date.fromisoformat(self.data["last_day"]) if self.data["last_day"] else None
        if last != today:
            self.data["streak"] = self.data["streak"] + 1 if last == today - dt.timedelta(days=1) else 1
        self.data["last_day"] = today.isoformat()
        self.data["sessions"] += 1
        self.data["focus_minutes"] += round(minutes)
        self.data["cookies"] += 1
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1))


class FocusCoach:
    def __init__(self, pets, tracker, brain, stats, log_path=None, clock=time.time):
        self.pets = pets
        self.tracker = tracker
        self.brain = brain
        self.stats = stats
        self.log_path = log_path or DATA_DIR / "events.jsonl"
        self.clock = clock
        self.active = False
        self.start_t = self.end_t = 0.0
        self.pending = None
        self.last_decision = 0.0
        self.last_action_t = 0.0
        self.last_intrusive = -1e9
        self.nudged_visit = -1
        self.handled_visit = -1
        self.last_explanation = "No decisions yet. Start a focus session!"
        self.timing = dict(eval_window=EVAL_WINDOW, check_in=CHECK_IN_EVERY, cooldown=COOLDOWN,
                           distracting_after=DISTRACTING_AFTER, min_window=MIN_WINDOW,
                           hide=HIDE_SECONDS)
        self.verbose = False
        self.click_trigger_t = -1e9
        self.clicks_handled_t = -1e9

    def use_test_timing(self):
        """Test mode: everything happens about 6x faster so you can watch it work."""
        self.timing = dict(eval_window=20, check_in=20, cooldown=15,
                           distracting_after=5, min_window=5, hide=20)
        self.verbose = True

    def say(self, msg):
        if self.verbose:
            print(time.strftime("%H:%M:%S"), msg, flush=True)

    # ---------- Session ----------

    def start(self, minutes):
        now = self.clock()
        self.active = True
        self.minutes = minutes
        self.start_t, self.end_t = now, now + minutes * 60
        self.pending = None
        self.last_decision = now
        self.last_action_t = now
        self.handled_visit = self.nudged_visit = self.tracker.visit
        for pet in self.pets:
            pet.in_focus = True
        pet = self._pick_pet()
        if pet and not pet.hold_sign(5):
            pet.say("focus time!", 4)

    def stop(self, completed):
        now = self.clock()
        self._finish_pending(now)
        self.active = False
        for pet in self.pets:
            pet.in_focus = False
            pet.come_back()
        if completed:
            self.stats.record_session(self.minutes)
            for i, pet in enumerate(self.pets):
                if pet.isVisible():
                    pet.wake()
                    pet.playful()
                    if i == 0:
                        pet.say("you did it! +1 🍪", 5)

    def minutes_left(self):
        """Whole minutes left, rounded up (a session with 24:30 left shows 25). 0 means under a minute."""
        secs = self.end_t - self.clock() if self.active else 0
        return 0 if secs < 60 else -int(-secs // 60)

    # ---------- Events from the pets ----------

    def on_click(self, pet):
        self.tracker.record_click(self.clock())
        if self.active:
            self.say(f"click on {pet.name}")
        self.last_clicked = pet

    def on_drag(self, pet):
        if self.active:
            self.tracker.record_drag(self.clock())

    def on_hidden(self, pet):
        if self.active and self.pending:
            self.pending["hid_pet"] = True

    # ---------- Main loop ----------

    def tick(self):
        now = self.clock()
        before = self.tracker.category
        self.tracker.poll(now)
        if not self.active:
            return
        if self.tracker.category != before:
            self.say(f"switched to a {self.tracker.category} app")
        if now >= self.end_t:
            self.stop(completed=True)
            return

        trigger = None
        new_clicks = self.tracker.count(self.tracker.clicks, self.click_trigger_t + 1e-6, now)
        if (self.tracker.category == "distracting"
                and self.tracker.dwell(now) >= self.timing["distracting_after"]
                and self.handled_visit != self.tracker.visit):
            trigger = "opened a distracting app"       # most important, checked first
        elif self.tracker.count(self.tracker.clicks, now - 60, now) >= 3 and new_clicks:
            trigger = "lots of clicking"
        if trigger == "lots of clicking":
            self.click_trigger_t = now                 # these clicks are handled now

        if self.pending and (trigger or now - self.pending["t0"] >= self.timing["eval_window"]):
            self._finish_pending(now)
        if not self.pending and (trigger or now - self.last_decision >= self.timing["check_in"]):
            if self._pick_pet() is None:      # pets are hiding or hidden
                if trigger == "opened a distracting app":
                    self.say("coming back to check on you")
                    for pet in self.pets:
                        pet.come_back()          # decide once they're back on screen
                else:
                    self.last_decision = now
                return
            self._decide(now, trigger or "regular check-in")

    def _context(self, now):
        cat = self.tracker.category
        # Recent clicks the pets haven't reacted to yet (so old clicks don't keep counting)
        clicks = self.tracker.count(self.tracker.clicks, max(now - 60, self.clicks_handled_t), now)
        hour = dt.datetime.now().hour + dt.datetime.now().minute / 60
        progress = (now - self.start_t) / max(self.end_t - self.start_t, 1)
        x = featurize(cat, clicks, self.tracker.dwell(now), now - self.last_action_t, hour, progress)
        return cat, clicks, x

    def _decide(self, now, reason):
        cat, clicks, x = self._context(now)
        allowed = allowed_actions(
            cat, clicks,
            already_nudged=self.nudged_visit == self.tracker.visit,
            cooldown_ok=now - self.last_intrusive >= self.timing["cooldown"])
        action, scores = self.brain.choose(x, allowed)

        pet = getattr(self, "last_clicked", None) if reason == "lots of clicking" else None
        if pet is None or not pet.isVisible():
            pet = self._pick_pet()
        self._execute(action, pet)

        self.last_decision = now
        if action != "idle":
            self.last_action_t = now
        if INTRUSIVENESS[action] >= 0.1:
            self.last_intrusive = now
        if cat == "distracting":
            self.handled_visit = self.tracker.visit
            if action in ("nudge", "focus_sign"):
                self.nudged_visit = self.tracker.visit
        if reason == "lots of clicking":
            self.clicks_handled_t = now + 1e-6
        self.pending = {"t0": now, "action": action, "x": x, "category": cat,
                        "reason": reason, "hid_pet": False}

        self.say(f"DECIDED {action} because: {reason} (you're in a {cat} app, "
                 f"{clicks} recent clicks; options were {', '.join(allowed)})")
        ranked = sorted(scores.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))
        lines = [f"  {a}: expected {m:+.2f}, curiosity +{b:.2f}" for a, (m, b) in ranked]
        self.last_explanation = (
            f"Why: {reason}. You were in a {cat} app"
            f"{f' and clicked the pets {clicks}x recently' if clicks else ''}.\n"
            f"Chose: {action}\n" + "\n".join(lines))

    def _finish_pending(self, now):
        p, self.pending = self.pending, None
        if not p or now - p["t0"] < self.timing["min_window"]:
            return
        fr = self.tracker.fractions(p["t0"], now)
        clicks = self.tracker.count(self.tracker.clicks, p["t0"], now)
        drags = self.tracker.count(self.tracker.drags, p["t0"], now)
        reward, parts = compute_reward(p["action"], p["category"], fr, clicks, drags, p["hid_pet"])
        self.brain.update(p["action"], p["x"], reward)
        self.brain.save()
        self.say(f"  scored {p['action']}: reward {reward:+.2f} "
                 f"({', '.join(f'{k} {v:+.2f}' for k, v in parts.items() if v)})")
        self._log({"t": round(p["t0"]), "reason": p["reason"], "category": p["category"],
                   "action": p["action"], "reward": round(reward, 3),
                   "parts": {k: round(v, 3) for k, v in parts.items() if v}})

    def _log(self, entry):
        """Append to the learning log, keeping only the newest entries so it stays small."""
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
            if self.log_path.stat().st_size > LOG_MAX_BYTES:
                lines = self.log_path.read_text().splitlines()[-LOG_KEEP_LINES:]
                self.log_path.write_text("\n".join(lines) + "\n")
        except OSError:
            pass

    # ---------- Actions ----------

    def _pick_pet(self):
        visible = [p for p in self.pets if p.isVisible() and p.away <= 0]
        return random.choice(visible) if visible else None

    def _execute(self, action, pet):
        everyone = [p for p in self.pets if p.isVisible() and p.away <= 0]
        if action == "idle" or pet is None:
            return
        if action == "nap":
            for p in everyone:
                p.nap(random.uniform(60, 120))
        elif action == "retreat":
            for p in everyone:
                p.retreat(180)
        elif action == "hide":
            for p in everyone:
                p.go_away(self.timing["hide"])
        elif action == "encourage":
            pet.cheer()
            pet.say(random.choice(ENCOURAGE), 4)
        elif action == "focus_sign":
            if not pet.hold_sign(6):
                pet.say("focus time!", 4)
        elif action == "nudge":
            pet.face_cursor()
            pet.say(random.choice(NUDGES), 5)
        elif action == "turn_away":
            pet.turn_away(25)
            pet.say("later?", 3)
        elif action == "playful":
            pet.playful()
