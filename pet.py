"""One pet: a transparent window that plays sprite frames and runs a small state machine."""
import json
import math
import random
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from effects import SHADOW, Bubble, draw_heart, draw_sparkles, draw_zzz, mac_tweaks

SIZES = {"Mini": 0.11, "Tiny": 0.16, "Small": 0.24, "Medium": 0.30, "Large": 0.40}  # scale of the art
WALK_SPEED = 45        # pixels per second at Medium size
CHASE_SPEED = 120
GRAVITY = 1400
HOP_SPEED = 380
HEART_TIME = 1.2
SLEEPY_AFTER = 90
SIGN_TIME = 8
FOCUS_SLOWDOWN = 0.6   # pets move slower and calmer during focus sessions

# Frames for each state. Missing frames fall back to idle_1.
ANIMATIONS = {
    "idle":     (["idle_1"], 0),
    "resting":  (["idle_1"], 0),
    "turned":   (["idle_1"], 0),
    "walking":  (["walk_1", "walk_2", "walk_3", "walk_4"], 8),
    "retreat":  (["walk_1", "walk_2", "walk_3", "walk_4"], 8),
    "happy":    (["happy_1"], 0),
    "eating":   (["eat_1", "eat_2"], 3),
    "sleeping": (["sleep_1", "sleep_2"], 0.8),
    "held":     (["held_1"], 0),
    "falling":  (["fall_1", "held_1"], 0),
    "sign":     (["sign_1"], 0),
}
GROUNDED = ("walking", "idle", "sleeping", "eating", "sign", "retreat", "resting", "turned")


class Pet(QWidget):
    def __init__(self, name: str, folder: Path, scale: float, start_x: float):
        flags = (Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                 | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        if sys.platform != "darwin":
            flags |= Qt.Tool
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self.name = name
        self.meta = json.loads((folder / "meta.json").read_text())
        self.images = {p.stem: QPixmap(str(p)) for p in folder.glob("*.png") if p.stem != "icon"}
        self.icon = QIcon(str(folder / "icon.png"))
        self.menu = None
        self.follow_actions, self.show_actions = [], []
        self.bubble = Bubble()
        self.on_click = None       # callbacks set by the app (for the focus coach)
        self.on_drag = None
        self.in_focus = False
        self.bedtime = False       # set by Bedtime: keep sleeping until morning
        self._drawn = None         # what was last painted, to skip redundant repaints
        self.set_scale(scale)

        area = QGuiApplication.primaryScreen().availableGeometry()
        self.x_ = start_x - self.width() / 2
        self.y_ = float(area.top())
        self.vel_y = 0.0
        self.direction = random.choice((1, -1))

        self.state = "falling"
        self.state_timer = 0.0
        self.awake_time = random.uniform(0, 40)
        self.follow_cursor = False
        self.hopping = False
        self.sparkle = 0.0
        self.retreat_x = 0.0

        self.away = 0.0            # seconds left "hiding" (brain action)
        self.opacity = 1.0

        self.anim_t = 0.0
        self.heart = 0.0
        self.blink_timer = random.uniform(2, 5)

        self.drag_offset = (0.0, 0.0)
        self.press_point = QPoint()
        self.drag_distance = 0.0
        self.was_sleeping = False
        self.last = time.monotonic()

    # ---------- Size ----------

    def set_scale(self, scale):
        self.scale = scale
        w, h = self.meta["size"]
        self.setFixedSize(round(w * scale), round(h * scale))
        dpr = QGuiApplication.primaryScreen().devicePixelRatio()
        self.frames = {}
        for name, img in self.images.items():
            pm = img.scaled(round(w * scale * dpr), round(h * scale * dpr),
                            Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            self.frames[name] = pm

    def k(self):
        """Size factor relative to Medium."""
        return self.scale / SIZES["Medium"]

    def head_top(self):
        name = self.current_frame()
        return self.meta["frames"].get(name, self.meta["frames"]["idle_1"])[1] * self.scale

    # ---------- Actions ----------

    def busy(self):
        return self.state in ("held", "falling") or self.away > 0 or not self.isVisible()

    def say(self, text, seconds=4.0):
        if self.isVisible() and self.away <= 0:
            self.bubble.say(text, seconds, small=self.scale <= SIZES["Small"])

    def feed(self):
        if self.busy():
            return
        self.awake_time = 0
        self.enter("eating", 3)
        self.heart = HEART_TIME

    def nap(self, seconds=60):
        if not self.busy():
            self.enter("sleeping", seconds)

    def wake(self):
        if self.state in ("sleeping", "resting", "turned"):
            self.awake_time = 0
            self.enter("idle", 1.5)

    def hold_sign(self, seconds=SIGN_TIME):
        if self.busy():
            return False
        if "sign_1" not in self.frames:
            return False
        self.awake_time = 0
        self.enter("sign", seconds)
        return True

    def cheer(self):
        """A small, quiet hop with sparkles (no heart)."""
        if self.busy():
            return
        self.vel_y = -HOP_SPEED * 0.55
        self.hopping = True
        self.sparkle = 1.2
        self.enter("falling")

    def playful(self):
        if self.busy():
            return
        self.vel_y = -HOP_SPEED
        self.hopping = True
        self.heart = HEART_TIME
        self.enter("falling")

    def turn_away(self, seconds=25):
        if self.busy():
            return
        cursor_x = QCursor.pos().x()
        self.direction = -1 if cursor_x > self.x_ + self.width() / 2 else 1
        self.enter("turned", seconds)

    def face_cursor(self):
        cursor_x = QCursor.pos().x()
        self.direction = 1 if cursor_x > self.x_ + self.width() / 2 else -1

    def retreat(self, seconds=180):
        """Walk to the nearest corner and sit quietly."""
        if self.busy():
            return
        area = self.screen_area()
        left, right = area.left(), area.right() + 1 - self.width()
        self.retreat_x = left if (self.x_ - left) < (right - self.x_) else right
        self.state_timer_after = seconds
        self.enter("retreat")

    def go_away(self, seconds=90):
        """Say brb, fade out, and come back later."""
        if self.busy():
            return
        self.say("brb! 🙈", 1.2)
        self.away = seconds + 1.2          # the first 1.2s is the "brb" before fading

    def come_back(self):
        if self.away > 0:
            self.away = 0.001

    def set_follow(self, on):
        self.follow_cursor = on
        if on:
            self.wake()

    def summon(self):
        c = QCursor.pos()
        self.x_, self.y_ = c.x() - self.width() / 2, c.y() - self.height() / 2
        self.vel_y = 0
        self.hopping = False
        self.away = 0
        self.opacity = 1.0
        self.setWindowOpacity(1.0)
        self.enter("falling")
        if not self.isVisible():
            self.show()
            mac_tweaks(self)

    # ---------- Mouse ----------

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        g = e.globalPosition().toPoint()
        if self.state == "turned":            # ignoring you on purpose, but it still counts
            if self.on_click:
                self.on_click(self)
            return
        self.was_sleeping = self.state == "sleeping"
        self.drag_offset = (g.x() - self.x_, g.y() - self.y_)
        self.press_point = g
        self.drag_distance = 0.0
        self.hopping = False
        self.enter("held")

    def mouseMoveEvent(self, e):
        if self.state != "held":
            return
        g = e.globalPosition().toPoint()
        self.x_ = g.x() - self.drag_offset[0]
        self.y_ = g.y() - self.drag_offset[1]
        d = math.hypot(g.x() - self.press_point.x(), g.y() - self.press_point.y())
        self.drag_distance = max(self.drag_distance, d)
        self.move(round(self.x_), round(self.y_))

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self.state != "held":
            return
        if self.drag_distance < 4:              # a click, not a drag
            if self.on_click:
                self.on_click(self)
            if self.was_sleeping and self.bedtime:
                self.say("shh... it's late 🌙", 3)
                self.enter("idle", 3)            # then back to sleep
            elif self.was_sleeping:
                self.awake_time = 0
                self.enter("idle", 1.5)
            elif self.in_focus:
                self.vel_y = -HOP_SPEED * 0.4    # during focus: just a tiny hop, no fuss
                self.enter("falling")
            else:
                self.vel_y = -HOP_SPEED
                self.hopping = True
                self.heart = HEART_TIME
                self.enter("falling")
        else:
            if self.on_drag:
                self.on_drag(self)
            self.vel_y = 0
            self.enter("falling")

    def contextMenuEvent(self, e):
        if self.menu:
            self.menu.exec(e.globalPos())

    # ---------- Behavior loop ----------

    def tick(self):
        now = time.monotonic()
        dt = min(now - self.last, 0.25)
        self.last = now

        if self.away > 0:
            self._tick_away(dt)
            return
        if not self.isVisible():
            return

        area = self.screen_area()
        floor = area.bottom() + 1 - self.height()
        min_x, max_x = area.left(), area.right() + 1 - self.width()
        slow = FOCUS_SLOWDOWN if self.in_focus else 1.0
        walk = WALK_SPEED * self.k() * slow
        chase = CHASE_SPEED * self.k()

        if self.state != "sleeping":
            self.awake_time += dt

        if self.state in GROUNDED:
            if self.y_ < floor - 1:
                self.enter("falling")
            self.y_ = min(self.y_, floor)

        s = self.state
        if s == "falling":
            self.vel_y += GRAVITY * dt
            self.y_ += self.vel_y * dt
            if self.y_ >= floor:
                self.y_, self.vel_y = floor, 0.0
                self.hopping = False
                self.enter("idle", 0.8)

        elif s == "walking":
            if self.follow_cursor:
                dx = self.cursor_target_x() - self.x_
                if abs(dx) < 12:
                    self.enter("idle", 0.5)
                else:
                    self.direction = 1 if dx > 0 else -1
                    self.x_ += self.direction * chase * dt
            else:
                self.x_ += self.direction * walk * dt
                self.count_down(dt)

        elif s == "retreat":
            dx = self.retreat_x - self.x_
            if abs(dx) < 3:
                self.x_ = self.retreat_x
                self.direction = 1 if self.retreat_x <= min_x + 1 else -1   # face the screen
                self.enter("resting", getattr(self, "state_timer_after", 120))
            else:
                self.direction = 1 if dx > 0 else -1
                self.x_ += self.direction * WALK_SPEED * self.k() * dt

        elif s == "idle":
            if self.follow_cursor and abs(self.cursor_target_x() - self.x_) > 60:
                self.enter("walking")
            else:
                self.count_down(dt)

        elif s in ("eating", "sign", "resting", "turned"):
            self.state_timer -= dt
            if self.state_timer <= 0:
                if s == "eating":
                    self.heart = HEART_TIME
                self.enter("idle", 1.5)

        elif s == "sleeping":
            self.state_timer -= dt
            if self.state_timer <= 0:
                if self.bedtime:
                    self.enter("sleeping", 10_000)
                else:
                    self.awake_time = 0
                    self.enter("idle", 1.5)

        if self.state != "held":
            if self.x_ <= min_x:
                self.x_ = min_x
                if self.state == "walking":
                    self.direction = 1
            if self.x_ >= max_x:
                self.x_ = max_x
                if self.state == "walking":
                    self.direction = -1

        self._animate(dt)
        self.move(round(self.x_), round(self.y_))
        self.bubble.follow(self.x_ + self.width() / 2, self.y_ + self.head_top() + 2, dt)
        drawn = (self.current_frame(), self.direction, self.state in ("held", "falling"))
        if drawn != self._drawn or self.has_effects():
            self._drawn = drawn
            self.update()

    def has_effects(self):
        """Hearts, sparkles and z's animate every frame."""
        return self.heart > 0 or self.sparkle > 0 or self.state == "sleeping"

    def is_moving(self):
        """True if this pet needs a smooth frame rate right now."""
        if self.away > 0:
            return self.isVisible()                    # fading out
        if not self.isVisible():
            return False
        return (self.state in ("walking", "retreat", "falling", "held")
                or self.heart > 0 or self.sparkle > 0 or self.opacity < 1
                or self.bubble.remaining > 0)

    def _tick_away(self, dt):
        """Hiding: fade out, wait, fade back in."""
        self.away -= dt
        if self.isVisible():
            self.bubble.follow(self.x_ + self.width() / 2, self.y_ + self.head_top() + 2, dt)
        else:
            self.bubble.follow(0, 0, 10)
        if self.away > 0:
            if self.bubble.remaining > 0:      # finish saying "brb" first
                return
            self.opacity = max(0.0, self.opacity - dt * 2)
            if self.opacity <= 0 and self.isVisible():
                self.hide()
        else:
            self.away = 0
            self.enter("idle", 2)
            if not self.isVisible():
                self.opacity = 0.0             # fade back in from invisible
                self.show()
                mac_tweaks(self)
        self.setWindowOpacity(self.opacity)

    def _animate(self, dt):
        if self.opacity < 1:
            self.opacity = min(1.0, self.opacity + dt * 2)
            self.setWindowOpacity(self.opacity)
        self.anim_t += dt
        self.heart = max(0.0, self.heart - dt)
        self.sparkle = max(0.0, self.sparkle - dt)
        self.blink_timer -= dt
        if self.blink_timer < -0.15:
            self.blink_timer = random.uniform(2, 5)

    def count_down(self, dt):
        self.state_timer -= dt
        if self.state_timer <= 0:
            self.pick_next()

    def pick_next(self):
        if self.bedtime:
            self.enter("sleeping", 10_000)
            return
        r = random.random()
        if self.in_focus:
            # Calm "study buddy" mode: mostly sitting still, short slow strolls, naps.
            if r < 0.25 and self.awake_time > 60:
                self.enter("sleeping", random.uniform(30, 60))
            elif r < 0.7:
                self.enter("idle", random.uniform(4, 10))
            else:
                self.direction = random.choice((1, -1))
                self.enter("walking", random.uniform(2, 4))
            return
        sleep_chance = 0.35 if self.awake_time > SLEEPY_AFTER else 0.0
        if r < sleep_chance:
            self.enter("sleeping", random.uniform(20, 45))
        elif r < sleep_chance + 0.35:
            self.enter("idle", random.uniform(1.5, 4))
        else:
            self.direction = random.choice((1, -1))
            self.enter("walking", random.uniform(3, 8))

    def enter(self, state, seconds=0.0):
        if state != self.state:
            self.anim_t = 0.0
        self.state = state
        self.state_timer = seconds

    def cursor_target_x(self):
        return QCursor.pos().x() - self.width() / 2

    def screen_area(self) -> QRect:
        center = QPoint(round(self.x_ + self.width() / 2), round(self.y_ + self.height() / 2))
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    # ---------- Drawing ----------

    def current_frame(self):
        state = self.state
        if state == "idle" and self.heart > 0:
            state = "happy"
        if state == "falling" and self.hopping:
            state = "happy"
        names, fps = ANIMATIONS[state]
        names = [n for n in names if n in self.frames] or ["idle_1"]
        name = names[int(self.anim_t * fps) % len(names)] if fps else names[0]
        if name == "idle_1" and self.blink_timer < 0 and "blink" in self.frames:
            name = "blink"
        return name

    def paintEvent(self, _):
        name = self.current_frame()
        s, k = self.scale, self.k()
        w = self.width()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        l, t, r, b = self.meta["frames"].get(name, self.meta["frames"]["idle_1"])
        ground = self.meta["ground_y"] * s

        if self.state not in ("held", "falling"):
            sw = (r - l) * s * 0.8
            p.setPen(Qt.NoPen)
            p.setBrush(SHADOW)
            p.drawEllipse(QRectF(w / 2 - sw / 2, ground - 4 * k, sw, 9 * k))

        p.save()
        if self.direction < 0 and name != "sign_1":
            p.translate(w, 0)
            p.scale(-1, 1)
        p.drawPixmap(0, 0, self.frames[name])
        p.restore()

        top = t * s
        if self.state == "sleeping":
            draw_zzz(p, w * (0.25 if self.direction < 0 else 0.75), top, self.anim_t, max(k, 0.7))
        if (self.state == "idle" and self.heart > 0) or (self.hopping and self.heart > 0) or self.sparkle > 0:
            draw_sparkles(p, w, top, self.anim_t, max(k, 0.6))
        if self.heart > 0:
            progress = 1 - self.heart / HEART_TIME
            p.setOpacity(min(1.0, self.heart / HEART_TIME * 2))
            draw_heart(p, w / 2, top - 8 * k - progress * 14 * k, 8 * max(k, 0.7))
            p.setOpacity(1)
        p.end()
