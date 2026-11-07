"""Little things drawn in code on top of the sprites: hearts, sparkles, z's, speech bubbles,
plus a few macOS window tweaks."""
import math
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

HEART = QColor("#FFAFC9")
ZZZ = QColor("#A9C6F5")
SPARKLES = (QColor("#FFE28A"), QColor("#D3C1F7"))
SHADOW = QColor(180, 160, 210, 40)
BUBBLE_FILL = QColor("#FFFDF8")
BUBBLE_LINE = QColor("#EBCBD8")
BUBBLE_TEXT = QColor("#7A5C8A")


def draw_heart(p, cx, cy, s):
    path = QPainterPath()
    path.moveTo(cx, cy + s * 0.6)
    path.cubicTo(cx - s * 1.1, cy - s * 0.1, cx - s * 0.5, cy - s * 0.9, cx, cy - s * 0.35)
    path.cubicTo(cx + s * 0.5, cy - s * 0.9, cx + s * 1.1, cy - s * 0.1, cx, cy + s * 0.6)
    p.setPen(QPen(QColor("#FFFFFF"), 1.2))
    p.setBrush(HEART)
    p.drawPath(path)


def draw_sparkles(p, w, top, t, k=1.0):
    spots = ((0.12, top + 18 * k), (0.88, top + 10 * k), (0.78, top - 14 * k), (0.2, top - 10 * k))
    for i, (fx, y) in enumerate(spots):
        x, size = w * fx, (2.5 + 3.5 * abs(math.sin(t * 4 + i * 1.3))) * k
        path = QPainterPath()
        path.moveTo(x, y - size)
        path.quadTo(x, y, x + size, y)
        path.quadTo(x, y, x, y + size)
        path.quadTo(x, y, x - size, y)
        path.quadTo(x, y, x, y - size)
        p.setPen(Qt.NoPen)
        p.setBrush(SPARKLES[i % 2])
        p.drawPath(path)


def draw_zzz(p, x0, top, t, k=1.0):
    p.save()
    for i in range(3):
        phase = (t * 0.5 + i / 3) % 1.0
        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(8, int((11 + phase * 7) * k)))
        p.setFont(font)
        c = QColor(ZZZ)
        c.setAlphaF(max(0.0, 1 - phase))
        p.setPen(c)
        p.drawText(QPointF(x0 + phase * 12 * k, top + 4 - phase * 26 * k), "z")
    p.restore()


class Bubble(QWidget):
    """A small speech bubble that floats above a pet. Clicks pass straight through it."""

    def __init__(self):
        flags = (Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                 | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        if sys.platform != "darwin":
            flags |= Qt.Tool
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.text = ""
        self.remaining = 0.0
        self.text_font = QFont()
        self.text_font.setBold(True)

    def say(self, text, seconds, small=False):
        self.text = text
        self.remaining = seconds
        self.text_font.setPixelSize(11 if small else 13)
        fm = QFontMetrics(self.text_font)
        lines = text.split("\n")
        tw = max(fm.horizontalAdvance(line) for line in lines)
        th = fm.height() * len(lines)
        self.setFixedSize(tw + 22, th + 18)
        self.setWindowOpacity(1.0)
        self.show()
        mac_tweaks(self)        # also re-applies "on top" vs "on the desktop"
        self.update()

    def follow(self, x_center, y_bottom, dt):
        if self.remaining <= 0:
            if self.isVisible():
                self.hide()
            return
        self.remaining -= dt
        self.setWindowOpacity(max(0.0, min(1.0, self.remaining / 0.4)))
        self.move(round(x_center - self.width() / 2), round(y_bottom - self.height()))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        body = QRectF(1, 1, w - 2, h - 9)
        path = QPainterPath()
        path.addRoundedRect(body, 10, 10)
        tail = QPainterPath()
        tail.moveTo(w / 2 - 6, body.bottom() - 1)
        tail.lineTo(w / 2, h - 1)
        tail.lineTo(w / 2 + 6, body.bottom() - 1)
        tail.closeSubpath()
        path = path.united(tail)
        p.setPen(QPen(BUBBLE_LINE, 1.5))
        p.setBrush(BUBBLE_FILL)
        p.drawPath(path)
        p.setFont(self.text_font)
        p.setPen(BUBBLE_TEXT)
        p.drawText(body, Qt.AlignCenter, self.text)
        p.end()


_accessory_set = False
ON_TOP = True                    # False = live on the desktop, behind app windows
FLOATING_LEVEL = 3               # macOS NSFloatingWindowLevel: above normal app windows
DESKTOP_LEVEL = -2147483603      # macOS desktop-icon level: above the wallpaper, below apps


def set_on_top(widgets, on_top):
    """Switch windows between floating above apps and sitting on the desktop."""
    global ON_TOP
    ON_TOP = on_top
    for w in widgets:
        if sys.platform == "darwin":
            mac_tweaks(w)
        else:
            visible = w.isVisible()
            w.setWindowFlag(Qt.WindowStaysOnTopHint, on_top)
            if visible:
                w.show()


def mac_tweaks(widget):
    """On macOS: hide the Dock icon, show the window on every Space, and keep it visible
    when another app is active. Needs pyobjc (installed by start.sh)."""
    global _accessory_set
    if sys.platform != "darwin":
        return
    try:
        import AppKit
        import objc
    except ImportError:
        return
    if not _accessory_set:
        AppKit.NSApplication.sharedApplication().setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyAccessory)
        _accessory_set = True
    ns_view = objc.objc_object(c_void_p=int(widget.winId()))
    ns_window = ns_view.window()
    if ns_window is not None:
        # Every desktop Space, but not on top of full-screen apps (videos, presentations, games).
        can_join_all_spaces, stationary = 1 << 0, 1 << 4
        ns_window.setCollectionBehavior_(can_join_all_spaces | stationary)
        ns_window.setHidesOnDeactivate_(False)
        ns_window.setLevel_(FLOATING_LEVEL if ON_TOP else DESKTOP_LEVEL)
