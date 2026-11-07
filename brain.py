"""The pets' brain: a contextual bandit that learns which behavior helps you focus.

During a focus session, every couple of minutes (or right away when something happens,
like you opening a distracting app or clicking the pets a lot), the coach:

  1. describes the situation as a feature vector (featurize),
  2. lists the actions that make sense right now (allowed_actions, the guardrails),
  3. asks the brain to pick one (Brain.choose),
  4. watches the next two minutes and scores what happened (compute_reward),
  5. teaches the brain (Brain.update).

The goal is not to maximize interaction. Doing nothing scores well when you stay
focused, and attention-seeking actions pay an "interruption cost".

The learner is LinUCB (Li et al., 2010): one small ridge-regression model per action
that predicts reward from context, plus an exploration bonus for actions it is unsure
about. It starts from hand-written rules (warm_start) and adjusts to you over time.
Pure Python, no numpy. Nothing here depends on Qt, so it is easy to test.
"""
import json
import math
from pathlib import Path

ACTIONS = ("idle", "nap", "encourage", "focus_sign", "nudge",
           "retreat", "turn_away", "hide", "playful")

# How disruptive each action is. Subtracted from the reward, and used for the cooldown.
INTRUSIVENESS = {
    "idle": 0.0, "nap": 0.0, "retreat": 0.03, "hide": 0.05,
    "turn_away": 0.08, "encourage": 0.12, "focus_sign": 0.15, "nudge": 0.15, "playful": 0.2,
}

FEATURES = ("bias", "productive", "neutral", "distracting", "recent_clicks",
            "time_in_app", "since_last_action", "time_of_day_sin", "time_of_day_cos",
            "session_progress")
D = len(FEATURES)


def featurize(category, recent_clicks, dwell_s, since_action_s, hour, progress):
    angle = 2 * math.pi * (hour % 24) / 24
    return [
        1.0,
        1.0 if category == "productive" else 0.0,
        1.0 if category == "neutral" else 0.0,
        1.0 if category == "distracting" else 0.0,
        min(recent_clicks, 5) / 5,
        math.log1p(min(max(dwell_s, 0), 3600)) / math.log1p(3600),
        min(max(since_action_s, 0), 900) / 900,
        math.sin(angle),
        math.cos(angle),
        max(0.0, min(1.0, progress)),
    ]


def allowed_actions(category, recent_clicks, already_nudged, cooldown_ok):
    """Guardrails: the brain only chooses among actions that make sense here."""
    if category == "distracting":
        # Being in a distracting app matters more than recent petting.
        acts = ["idle", "retreat"] if already_nudged else ["idle", "nudge", "focus_sign", "retreat"]
    elif recent_clicks >= 3:
        # You're playing with the pets instead of working: only calm, back-off actions.
        acts = ["idle", "nap", "turn_away", "hide", "retreat"]
    elif category == "productive":
        acts = ["idle", "nap", "encourage", "focus_sign", "retreat", "playful"]
    else:
        acts = ["idle", "nap", "encourage", "retreat"]
    if not cooldown_ok:
        acts = [a for a in acts if INTRUSIVENESS[a] < 0.1]
    return acts


def compute_reward(action, start_category, fractions, clicks, drags, hid_pet):
    """Score what happened during the window after an action. Returns (reward, parts).

    fractions: share of the window spent in productive / neutral / distracting apps
    clicks:    pet clicks during the window (one is fine, more suggests distraction)
    drags:     times a pet was dragged away (a mild "go away")
    hid_pet:   the user hid a pet from the menu (a strong "go away")
    """
    parts = {
        "focus": 0.6 * fractions.get("productive", 0.0),
        "distraction": -0.6 * fractions.get("distracting", 0.0),
        "too_much_petting": -0.12 * min(max(clicks - 1, 0), 4),
        "dragged_away": -0.4 if drags else 0.0,
        "hidden": -0.8 if hid_pet else 0.0,
        "interruption": -INTRUSIVENESS[action],
        "nudge_worked": 0.0,
    }
    if (action in ("nudge", "focus_sign") and start_category == "distracting"
            and fractions.get("productive", 0.0) > 0.3):
        parts["nudge_worked"] = 0.5
    reward = max(-1.0, min(1.0, sum(parts.values())))
    return reward, parts


# Hand-written starting beliefs: (category, recent clicks, action, reward)
WARM_START_RULES = [
    ("productive", 0, "idle", 0.35), ("productive", 0, "nap", 0.2),
    ("productive", 0, "encourage", 0.1), ("productive", 0, "retreat", 0.1),
    ("productive", 0, "focus_sign", -0.1), ("productive", 0, "playful", -0.2),
    ("neutral", 0, "idle", 0.25), ("neutral", 0, "nap", 0.15),
    ("neutral", 0, "encourage", 0.05), ("neutral", 0, "retreat", 0.05),
    ("distracting", 0, "nudge", 0.3), ("distracting", 0, "focus_sign", 0.2),
    ("distracting", 0, "idle", -0.15), ("distracting", 0, "retreat", -0.05),
    ("productive", 4, "hide", 0.3), ("productive", 4, "turn_away", 0.25),
    ("productive", 4, "nap", 0.2), ("productive", 4, "retreat", 0.15),
    ("productive", 4, "idle", -0.1),
]


def _identity(n, scale=1.0):
    return [[scale if i == j else 0.0 for j in range(n)] for i in range(n)]


def _matvec(m, v):
    return [sum(a * b for a, b in zip(row, v)) for row in m]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


class Brain:
    def __init__(self, path=None, alpha=0.3, ridge=1.0):
        self.path = Path(path) if path else None
        self.alpha = alpha
        self.ridge = ridge
        self.reset()
        if self.path and self.path.exists():
            try:
                self.load()
            except (OSError, ValueError, KeyError):
                self.reset()

    def reset(self):
        self.models = {a: {"A_inv": _identity(D, 1 / self.ridge), "b": [0.0] * D,
                           "n": 0, "reward_sum": 0.0} for a in ACTIONS}
        self.warm_start()

    def warm_start(self):
        for cat, clicks, action, reward in WARM_START_RULES:
            x = featurize(cat, clicks, 300, 300, 14, 0.5)
            for _ in range(2):
                self.update(action, x, reward, count=False)

    def scores(self, x, actions=ACTIONS):
        """Predicted reward and exploration bonus for each action."""
        out = {}
        for a in actions:
            m = self.models[a]
            theta = _matvec(m["A_inv"], m["b"])
            mean = _dot(theta, x)
            bonus = self.alpha * math.sqrt(max(_dot(x, _matvec(m["A_inv"], x)), 0.0))
            out[a] = (mean, bonus)
        return out

    def choose(self, x, allowed):
        s = self.scores(x, allowed)
        best = max(allowed, key=lambda a: s[a][0] + s[a][1])
        return best, s

    def update(self, action, x, reward, count=True):
        m = self.models[action]
        A_inv = m["A_inv"]
        Ax = _matvec(A_inv, x)                     # Sherman-Morrison rank-one update
        denom = 1.0 + _dot(x, Ax)
        for i in range(D):
            for j in range(D):
                A_inv[i][j] -= Ax[i] * Ax[j] / denom
        m["b"] = [bi + reward * xi for bi, xi in zip(m["b"], x)]
        if count:
            m["n"] += 1
            m["reward_sum"] += reward

    def summary(self):
        return {a: (m["n"], m["reward_sum"] / m["n"] if m["n"] else None)
                for a, m in self.models.items()}

    def save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "features": FEATURES, "models": self.models}))
        tmp.replace(self.path)

    def load(self):
        data = json.loads(self.path.read_text())
        if tuple(data.get("features", ())) != FEATURES:
            raise ValueError("feature set changed")
        for a in ACTIONS:
            if a in data["models"]:
                self.models[a] = data["models"][a]
