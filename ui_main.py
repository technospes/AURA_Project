"""
JARVIS UI — Production-Grade PySide6 Interface  (v2)
=====================================================
Architecture:
  OrbController  — pure state/logic, no widgets, thread-safe via Qt signals
  OrbPainter     — stateless drawing functions, zero random() in paintEvent
  OrbWidget      — thin widget shell, only wires controller → painter
  JarvisBridge   — thread-safe signal bus between backend threads and Qt UI
  RipplePool     — pre-computed ripple objects, no allocation in paintEvent
  CentralStateManager — single source of truth for all app state

Run:
    python jarvis_ui.py                   # production (no demo cycling)
    python jarvis_ui.py --debug           # shows state-cycle demo
"""

from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import (
    Property, QEasingCurve, QObject, QPoint, QPointF, QRect, QRectF,
    QSize, QTimer, Qt, Signal, Slot, QPropertyAnimation, 
    QSequentialAnimationGroup, QParallelAnimationGroup
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontDatabase, QLinearGradient, QPaintEvent,
    QPainter, QPainterPath, QPalette, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)


# ══════════════════════════════════════════════════════════════════════════════
#  PALETTE  —  single source, never inline raw QColor
# ══════════════════════════════════════════════════════════════════════════════
class P:
    BG         = QColor(8,   8,   18)
    PANEL      = QColor(14,  14,  28,  200)
    BORDER     = QColor(80,  160, 255,  60)
    ACCENT     = QColor(80,  160, 255)
    VIOLET     = QColor(120,  80, 255)
    TEXT       = QColor(220, 230, 255)
    TEXT_DIM   = QColor(120, 140, 180)
    GLOW       = QColor(80,  160, 255,  40)
    SUCCESS    = QColor(80,  220, 160)
    DANGER     = QColor(255,  80, 100)
    ORB_BLUE   = QColor(80,  160, 255)
    ORB_VIOLET = QColor(120,  80, 255)

    @staticmethod
    def css(c: QColor) -> str:
        return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"

    @staticmethod
    def with_alpha(c: QColor, a: int) -> QColor:
        out = QColor(c)
        out.setAlpha(a)
        return out


# ══════════════════════════════════════════════════════════════════════════════
#  ORB STATE
# ══════════════════════════════════════════════════════════════════════════════
class OrbState:
    IDLE      = "idle"
    WAKE      = "wake"
    LISTENING = "listening"
    THINKING  = "thinking"
    SPEAKING  = "speaking"

    _ALL = (IDLE, WAKE, LISTENING, THINKING, SPEAKING)


# ══════════════════════════════════════════════════════════════════════════════
#  ORB VISUAL PARAMS  (pure dataclass — no Qt, no widgets)
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class OrbVisualParams:
    """Computed per-frame, fully deterministic. Passed to OrbPainter."""
    state:       str
    pulse_t:     float     # monotonic animation clock
    rotate_t:    float     # rotation clock (thinking arc)
    mic_energy:  float     # 0..1 normalised RMS
    # Derived — computed by OrbController.compute()
    core_color:  QColor    = field(default_factory=lambda: QColor(80, 160, 255))
    glow_color:  QColor    = field(default_factory=lambda: QColor(80, 160, 255))
    core_alpha:  int       = 200
    glow_alpha:  int       = 60
    ring_alpha:  int       = 80
    show_rings:  bool      = False
    show_arc:    bool      = False
    label:       str       = ""


# ══════════════════════════════════════════════════════════════════════════════
#  ORB CONTROLLER  —  pure logic, no widgets
#  Thread-safe: backend calls emit_state_change() from any thread.
#  The signal crosses thread boundaries via Qt's queued connection.
# ══════════════════════════════════════════════════════════════════════════════
class OrbController(QObject):
    """
    Single source of truth for orb state + animation values.
    Emits params_ready every frame so OrbWidget can repaint.
    Backend threads call emit_state_change / emit_mic_energy — both are
    posted to the Qt main-loop via invokeMethod / queued signals, so
    there is never a direct cross-thread widget call.
    """

    params_ready = Signal(OrbVisualParams)   # main-thread → OrbWidget
    fps_changed  = Signal(int)               # optional: 60 active / 15 idle

    def __init__(self, debug_mode: bool = False, parent: QObject | None = None):
        super().__init__(parent)
        self._state      : str   = OrbState.IDLE
        self._mic_energy : float = 0.0
        self._pulse_t    : float = 0.0
        self._rotate_t   : float = 0.0
        self._debug_mode         = debug_mode
        self._debug_idx          = 0
        self._last_state_change  = time.monotonic()

        # Adaptive-rate render timer — starts at 15 fps (idle) and
        # switches to 60 fps when active.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._current_fps = 0
        self._set_fps(15)

        if debug_mode:
            self._demo_timer = QTimer(self)
            self._demo_timer.timeout.connect(self._debug_cycle)
            self._demo_timer.start(3500)

    # ── Public API (safe to call from any thread via invokeMethod) ─────────

    @Slot(str)
    def set_state(self, state: str) -> None:
        """Always called on main thread via Qt signal or invokeMethod."""
        if state not in OrbState._ALL:
            return
        if state == self._state:
            return
        self._state = state
        self._last_state_change = time.monotonic()
        # Adaptive FPS: go fast when active, slow when idle
        if state == OrbState.IDLE:
            self._set_fps(15)
        else:
            self._set_fps(60)

    @Slot(float)
    def set_mic_energy(self, energy: float) -> None:
        """Always called on main thread."""
        self._mic_energy = max(0.0, min(1.0, energy))

    # ── Internal ───────────────────────────────────────────────────────────

    def _set_fps(self, fps: int) -> None:
        if fps == self._current_fps:
            return
        self._current_fps = fps
        interval = 1000 // fps
        self._timer.start(interval)
        self.fps_changed.emit(fps)

    def _tick(self) -> None:
        dt = 1.0 / self._current_fps
        self._pulse_t  += dt * 2.5
        self._rotate_t += dt * 0.6
        params = self._compute()
        self.params_ready.emit(params)

    def _compute(self) -> OrbVisualParams:
        """
        All math lives here. paintEvent receives a plain dataclass — zero
        computation, zero branching in the renderer.
        All trig values are pre-computed; no random() ever.
        """
        t  = self._pulse_t
        e  = self._mic_energy
        rt = self._rotate_t
        s  = self._state

        p = OrbVisualParams(state=s, pulse_t=t, rotate_t=rt, mic_energy=e)

        sin_slow  = math.sin(t * 0.7)
        sin_med   = math.sin(t * 2.0)
        sin_fast  = math.sin(t * 3.5)
        sin_arc   = math.sin(t * 1.1)

        if s == OrbState.IDLE:
            opacity       = 0.30 + 0.08 * sin_slow
            p.core_alpha  = int(180 * opacity)
            p.glow_alpha  = int(40  * opacity)
            p.ring_alpha  = int(60  * opacity)
            p.core_color  = P.with_alpha(P.ORB_BLUE, p.core_alpha)
            p.glow_color  = P.with_alpha(P.ORB_BLUE, p.glow_alpha)
            p.show_rings  = False
            p.show_arc    = False
            p.label       = ""

        elif s == OrbState.WAKE:
            pulse         = 0.5 + 0.5 * sin_med
            p.core_alpha  = 230
            p.glow_alpha  = int(80 + 60 * pulse)
            p.ring_alpha  = 160
            p.core_color  = P.with_alpha(P.ORB_BLUE, p.core_alpha)
            p.glow_color  = P.with_alpha(P.ORB_BLUE, p.glow_alpha)
            p.show_rings  = False
            p.show_arc    = False
            p.label       = "Wake"

        elif s == OrbState.LISTENING:
            p.core_alpha  = min(255, int(200 + 55 * e))
            p.glow_alpha  = min(255, int(60  + 100 * e))
            p.ring_alpha  = min(255, int(100 + 120 * e))
            p.core_color  = P.with_alpha(P.ORB_BLUE, p.core_alpha)
            p.glow_color  = P.with_alpha(P.ORB_BLUE, p.glow_alpha)
            p.show_rings  = e > 0.05
            p.show_arc    = False
            p.label       = "Listening"

        elif s == OrbState.THINKING:
            opacity       = 0.38 + 0.06 * sin_arc
            p.core_alpha  = int(160 * opacity)
            p.glow_alpha  = int(30  * opacity)
            p.ring_alpha  = int(50  * opacity)
            p.core_color  = P.with_alpha(P.ORB_VIOLET, p.core_alpha)
            p.glow_color  = P.with_alpha(P.ORB_VIOLET, p.glow_alpha)
            p.show_rings  = False
            p.show_arc    = True
            p.label       = "Thinking"

        else:   # SPEAKING
            p.core_alpha  = min(255, int(210 + 45 * e))
            p.glow_alpha  = min(255, int(70  + 90 * e))
            p.ring_alpha  = min(255, int(90  + 130 * e))
            p.core_color  = P.with_alpha(P.ORB_VIOLET, p.core_alpha)
            p.glow_color  = P.with_alpha(P.ORB_VIOLET, p.glow_alpha)
            p.show_rings  = e > 0.05
            p.show_arc    = False
            p.label       = "Speaking"

        return p

    def _debug_cycle(self) -> None:
        """Only active when debug_mode=True. Never ships in production."""
        states = OrbState._ALL
        self._debug_idx = (self._debug_idx + 1) % len(states)
        next_state = states[self._debug_idx]
        self.set_state(next_state)
        # Simulate mic energy for active states
        if next_state in (OrbState.LISTENING, OrbState.SPEAKING):
            self._mic_energy = 0.6
        elif next_state == OrbState.THINKING:
            self._mic_energy = 0.2
        else:
            self._mic_energy = 0.05


# ══════════════════════════════════════════════════════════════════════════════
#  ORB PAINTER  —  stateless drawing helpers, never called with random()
# ══════════════════════════════════════════════════════════════════════════════
class OrbPainter:
    """
    Pure-function namespace.  Receives OrbVisualParams and a QPainter.
    Zero Qt widgets, zero state.  All pre-computed trig from OrbController.
    """

    @staticmethod
    def draw(p: QPainter, params: OrbVisualParams, size: int) -> None:
        r  = size / 2.0 - 4
        cx = size / 2.0
        cy = size / 2.0
        t  = params.pulse_t
        rt = params.rotate_t
        e  = params.mic_energy

        OrbPainter._glow_rings(p, cx, cy, r, params)
        if params.show_arc:
            OrbPainter._thinking_arc(p, cx, cy, r, rt, params)
        if params.show_rings:
            OrbPainter._energy_rings(p, cx, cy, r, t, e, params)
        OrbPainter._core(p, cx, cy, r, params)
        OrbPainter._shimmer(p, cx, cy, r, t, params)
        if params.label:
            OrbPainter._label(p, cx, cy, r, size, params.label)

    @staticmethod
    def _glow_rings(p: QPainter, cx: float, cy: float, r: float,
                    params: OrbVisualParams) -> None:
        p.setPen(Qt.NoPen)
        for i in range(6, 0, -1):
            a = max(0, int(params.glow_alpha * i / 6.0))
            gw = P.with_alpha(params.glow_color, a)
            grad = QRadialGradient(cx, cy, r + i * 7)
            grad.setColorAt(0, gw)
            grad.setColorAt(1, P.with_alpha(gw, 0))
            p.setBrush(QBrush(grad))
            p.drawEllipse(QPointF(cx, cy), r + i * 7, r + i * 7)

    @staticmethod
    def _thinking_arc(p: QPainter, cx: float, cy: float, r: float,
                      rt: float, params: OrbVisualParams) -> None:
        arc_alpha = int(100 * (0.38 + 0.06 * math.sin(params.pulse_t * 1.1)))
        pen = QPen(P.with_alpha(P.ORB_VIOLET, arc_alpha), 2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        start = int(rt * 360 * 16) % (360 * 16)
        p.drawArc(
            QRectF(cx - r + 2, cy - r + 2, (r - 2) * 2, (r - 2) * 2),
            start, int(240 * 16),
        )

    @staticmethod
    def _energy_rings(p: QPainter, cx: float, cy: float, r: float,
                      t: float, e: float, params: OrbVisualParams) -> None:
        # Phase offsets pre-defined — no random()
        phases = (0.0, math.pi * 0.6, math.pi * 1.2)
        for i, phase in enumerate(phases):
            ring_t  = t * (1.5 + i * 0.4) + phase
            ring_r  = r + 8 + i * 12 + 6 * math.sin(ring_t) * e
            alpha   = int(params.ring_alpha * (1 - i / 3) * e)
            rpen = QPen(P.with_alpha(params.core_color, alpha), 1.5)
            p.setPen(rpen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), ring_r, ring_r)

    @staticmethod
    def _core(p: QPainter, cx: float, cy: float, r: float,
              params: OrbVisualParams) -> None:
        cc = params.core_color
        grad = QRadialGradient(cx - r * 0.3, cy - r * 0.35, r * 1.5)
        grad.setColorAt(0, QColor(
            min(255, cc.red() + 80),
            min(255, cc.green() + 40),
            255, params.core_alpha))
        grad.setColorAt(0.5, cc)
        grad.setColorAt(1, QColor(
            cc.red() // 2, cc.green() // 3, cc.blue() // 2,
            params.core_alpha))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(P.with_alpha(cc, params.ring_alpha), 1.5))
        p.drawEllipse(QPointF(cx, cy), r, r)

    @staticmethod
    def _shimmer(p: QPainter, cx: float, cy: float, r: float,
                 t: float, params: OrbVisualParams) -> None:
        base = int(50 + 30 * math.sin(t * 1.3))
        if params.state in (OrbState.IDLE, OrbState.THINKING):
            base = int(base * (0.3 + 0.07 * math.sin(t)))
        p.setBrush(QBrush(QColor(255, 255, 255, base)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx - r * 0.28, cy - r * 0.32), r * 0.32, r * 0.18)

    @staticmethod
    def _label(p: QPainter, cx: float, cy: float, r: float,
               size: int, text: str) -> None:
        p.setPen(QColor(255, 255, 255, 160))
        p.setFont(QFont("Segoe UI", 7))
        p.drawText(
            QRect(0, int(cy + r + 2), size, 14),
            Qt.AlignCenter, text,
        )


# ══════════════════════════════════════════════════════════════════════════════
#  ORB WIDGET  —  thin shell, wires controller → painter
# ══════════════════════════════════════════════════════════════════════════════
class OrbWidget(QWidget):
    """
    Only responsibilities:
      1. Receive OrbVisualParams from OrbController (queued signal)
      2. Call OrbPainter.draw() in paintEvent
      3. Handle drag + forward ripple events
      4. Apply GPU hints
    No logic, no state computation, no random().
    """

    drag_moved = Signal(float, float)   # cx, cy in screen coords

    def __init__(self, controller: OrbController,
                 ripple: "RippleOverlay", parent=None):
        super().__init__(parent)
        self._controller  = controller
        self._ripple      = ripple
        self._params      : OrbVisualParams | None = None
        self._drag_origin : QPoint | None = None
        self._orb_size    = 110

        self.setFixedSize(self._orb_size, self._orb_size)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        # GPU hints
        self.setAttribute(Qt.WA_AlwaysStackOnTop)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.setCursor(Qt.SizeAllCursor)

        # Wire controller → repaint (queued — safe across threads)
        controller.params_ready.connect(self._on_params, Qt.QueuedConnection)

    @Slot(object)
    def _on_params(self, params: OrbVisualParams) -> None:
        self._params = params
        self.update()

    # ── Paint ──────────────────────────────────────────────────────────────
    def paintEvent(self, event: QPaintEvent) -> None:
        if not self._params:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        OrbPainter.draw(p, self._params, self._orb_size)
        p.end()

    # ── Drag + Ripple ──────────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_origin = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event):
        if self._drag_origin and (event.buttons() & Qt.LeftButton):
            new_pos = event.globalPosition().toPoint() - self._drag_origin
            self.move(new_pos)
            self._ripple.add_ripple(
                new_pos.x() + self._orb_size / 2,
                new_pos.y() + self._orb_size / 2,
            )

    def mouseReleaseEvent(self, event):
        self._drag_origin = None

    def mouseDoubleClickEvent(self, event):
        # Debug shortcut: cycle state manually
        states = OrbState._ALL
        cur = self._params.state if self._params else OrbState.IDLE
        idx = (list(states).index(cur) + 1) % len(states)
        self._controller.set_state(states[idx])


# ══════════════════════════════════════════════════════════════════════════════
#  RIPPLE POOL  — pre-computed, no random() in paintEvent
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class RippleData:
    cx:      float
    cy:      float
    t:       float = 0.0
    max_r:   float = 0.0    # pre-computed
    speed:   float = 0.0    # pre-computed


# Deterministic ripple parameter table — avoids random() at paint time
_RIPPLE_TABLE = [
    (130.0, 0.022), (145.0, 0.018), (118.0, 0.025),
    (155.0, 0.016), (125.0, 0.020), (140.0, 0.019),
]


class RippleOverlay(QWidget):
    """
    Full-screen transparent overlay.  Ripple parameters are pre-computed
    when spawned — paintEvent is pure geometry, zero allocation.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnBottomHint | Qt.Tool
        )
        self._pool: list[RippleData] = []
        self._pool_cursor = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def add_ripple(self, cx: float, cy: float) -> None:
        max_r, speed = _RIPPLE_TABLE[self._pool_cursor % len(_RIPPLE_TABLE)]
        self._pool_cursor += 1
        self._pool.append(RippleData(cx=cx, cy=cy, max_r=max_r, speed=speed))
        # Cap pool size to prevent unbounded growth
        if len(self._pool) > 24:
            self._pool = self._pool[-24:]

    def _tick(self) -> None:
        self._pool = [r for r in self._pool if r.t < 1.0]
        for r in self._pool:
            r.t += r.speed
        if self._pool:
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        if not self._pool:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(Qt.NoBrush)
        for r in self._pool:
            radius = r.t * r.max_r
            # Quadratic fade: bright at start, gone at end
            alpha  = int(55 * (1.0 - r.t) ** 2)
            p.setPen(QPen(QColor(80, 160, 255, alpha), 1.5))
            p.drawEllipse(QPointF(r.cx, r.cy), radius, radius)
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  JARVIS BRIDGE  —  thread-safe signal bus
#  Backend asyncio/threads emit on this object; Qt queued connections
#  ensure all widget updates happen exclusively on the main thread.
# ══════════════════════════════════════════════════════════════════════════════
class JarvisBridge(QObject):
    """
    ┌──────────────────────────────────────────┐
    │  Backend thread / asyncio                │
    │  bridge.state_changed.emit("LISTENING")  │
    │  bridge.mic_energy_changed.emit(0.75)    │
    └──────────────────┬───────────────────────┘
                       │  Qt queued connection (crosses thread boundary)
    ┌──────────────────▼───────────────────────┐
    │  Qt main thread                          │
    │  OrbController.set_state("listening")    │
    │  OrbController.set_mic_energy(0.75)      │
    └──────────────────────────────────────────┘

    Usage from backend:
        bridge = JarvisBridge.instance()
        bridge.state_changed.emit("listening")
        bridge.mic_energy_changed.emit(rms_value)
    """

    # ── Signals (safe to emit from any thread) ─────────────────────────────
    state_changed      = Signal(str)    # OrbState constant
    mic_energy_changed = Signal(float)  # 0.0 .. 1.0 normalised RMS
    home_requested     = Signal()       # show main window
    shutdown_requested = Signal()       # clean exit

    _instance: "JarvisBridge | None" = None

    def __init__(self, parent=None):
        super().__init__(parent)

    @classmethod
    def instance(cls) -> "JarvisBridge":
        if cls._instance is None:
            cls._instance = JarvisBridge()
        return cls._instance


# ══════════════════════════════════════════════════════════════════════════════
#  CENTRAL STATE MANAGER
# ══════════════════════════════════════════════════════════════════════════════
class CentralStateManager(QObject):
    """
    Owns: JarvisBridge, OrbController, session data, preferences.
    All other widgets read/write through this object — never directly.
    """

    screen_changed = Signal(str)    # "intro" | "home" | "orb"

    def __init__(self, debug_mode: bool = False, parent=None):
        super().__init__(parent)
        self.debug_mode = debug_mode
        self.bridge     = JarvisBridge.instance()
        self.orb_ctrl   = OrbController(debug_mode=debug_mode, parent=self)

        # Session / preference stores (placeholder — connect to backend later)
        self.sessions   : list[dict] = []
        self.preferences: dict       = {}

        # Wire bridge → controller (queued = thread-safe)
        self.bridge.state_changed.connect(
            self.orb_ctrl.set_state, Qt.QueuedConnection
        )
        self.bridge.mic_energy_changed.connect(
            self.orb_ctrl.set_mic_energy, Qt.QueuedConnection
        )

    # ── Backend integration point ──────────────────────────────────────────
    def notify_state(self, state: str) -> None:
        """Call from backend thread — safe."""
        self.bridge.state_changed.emit(state)

    def notify_mic_energy(self, rms: float) -> None:
        """Call from backend thread — safe."""
        self.bridge.mic_energy_changed.emit(rms)


# ══════════════════════════════════════════════════════════════════════════════
#  INTRO SCREEN
# ══════════════════════════════════════════════════════════════════════════════
class IntroScreen(QWidget):
    finished = Signal()

    _LINE1 = "Hi, I am Jarvis"
    _LINE2 = "How can I assist you today?"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._shown1 = ""
        self._shown2 = ""
        self._phase  = 1     # 1=typing1, 2=pause, 3=typing2, 4=done
        self._blink_on = True

        self._lbl1 = QLabel("", self)
        self._lbl1.setAlignment(Qt.AlignCenter)
        self._lbl1.setFont(QFont("Segoe UI", 26, QFont.Weight.Light))
        self._lbl1.setStyleSheet(
            f"color:{P.css(P.TEXT)}; background:transparent;"
        )
        self._lbl2 = QLabel("", self)
        self._lbl2.setAlignment(Qt.AlignCenter)
        self._lbl2.setFont(QFont("Segoe UI", 15, QFont.Weight.Thin))
        self._lbl2.setStyleSheet(
            f"color:{P.css(P.TEXT_DIM)}; background:transparent;"
        )

        vl = QVBoxLayout(self)
        vl.setContentsMargins(60, 60, 60, 60)
        vl.setSpacing(16)
        vl.addStretch()
        vl.addWidget(self._lbl1)
        vl.addWidget(self._lbl2)
        vl.addStretch()

        self._type_timer  = QTimer(self)
        self._type_timer.timeout.connect(self._type_tick)
        self._type_timer.start(55)

        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._blink)
        self._blink_timer.start(500)

        # Fade in
        fx = QGraphicsOpacityEffect(self)
        fx.setOpacity(0.0)
        self.setGraphicsEffect(fx)
        self._fade_anim = QPropertyAnimation(fx, b"opacity", self)
        self._fade_anim.setDuration(800)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_anim.start()

    def _blink(self) -> None:
        self._blink_on = not self._blink_on
        self._render()

    def _type_tick(self) -> None:
        if self._phase == 1:
            if len(self._shown1) < len(self._LINE1):
                self._shown1 += self._LINE1[len(self._shown1)]
                self._render()
            else:
                self._phase = 2
                self._type_timer.stop()
                QTimer.singleShot(550, self._advance_phase)

        elif self._phase == 3:
            if len(self._shown2) < len(self._LINE2):
                self._shown2 += self._LINE2[len(self._shown2)]
                self._render()
            else:
                self._phase = 4
                self._type_timer.stop()
                self._blink_timer.stop()
                self._render()
                QTimer.singleShot(1200, self.finished.emit)

    def _advance_phase(self) -> None:
        self._phase = 3
        self._type_timer.start(40)

    def _render(self) -> None:
        cur = "█" if self._blink_on and self._phase != 4 else ""
        if self._phase in (1, 2):
            self._lbl1.setText(self._shown1 + cur)
            self._lbl2.setText("")
        else:
            self._lbl1.setText(self._shown1)
            self._lbl2.setText(self._shown2 + cur)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rc = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0, QColor(20, 22, 45, 215))
        grad.setColorAt(1, QColor(10, 12, 30, 235))
        path = QPainterPath()
        path.addRoundedRect(rc, 26, 26)
        p.fillPath(path, grad)

        p.setPen(QPen(QColor(80, 160, 255, 65), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rc, 26, 26)

        sh = QLinearGradient(rc.left(), rc.top(), rc.right(), rc.top())
        sh.setColorAt(0.0, QColor(255, 255, 255, 0))
        sh.setColorAt(0.5, QColor(255, 255, 255, 30))
        sh.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(QPen(QBrush(sh), 1))
        p.drawLine(int(rc.left() + 26), int(rc.top() + 1),
                   int(rc.right() - 26), int(rc.top() + 1))
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  SETTINGS MODAL
# ══════════════════════════════════════════════════════════════════════════════
class SettingsModal(QWidget):
    closed = Signal()

    def __init__(self, state_mgr: CentralStateManager, parent=None):
        super().__init__(parent)
        self._state_mgr = state_mgr
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._backdrop = QWidget(self)
        self._backdrop.setStyleSheet("background:rgba(0,0,5,165);")

        self._card = QFrame(self)
        self._card.setFixedSize(520, 540)
        self._card.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 rgba(18,20,45,248), stop:1 rgba(10,12,30,255));
                border: 1px solid rgba(80,160,255,85);
                border-radius: 20px;
            }
        """)

        cl = QVBoxLayout(self._card)
        cl.setContentsMargins(32, 26, 32, 26)
        cl.setSpacing(16)

        # Title + close
        tr = QHBoxLayout()
        t = QLabel("  Settings")
        t.setFont(QFont("Segoe UI", 16, QFont.Weight.DemiBold))
        t.setStyleSheet(f"color:{P.css(P.TEXT)};background:transparent;")
        x = QPushButton("")
        x.setFixedSize(32, 32)
        x.setCursor(Qt.PointingHandCursor)
        x.setStyleSheet("""
            QPushButton{background:rgba(255,80,100,30);color:rgba(255,80,100,200);
            border:1px solid rgba(255,80,100,60);border-radius:16px;font-size:13px;}
            QPushButton:hover{background:rgba(255,80,100,80);}
        """)
        x.clicked.connect(self._close)
        tr.addWidget(t); tr.addStretch(); tr.addWidget(x)
        cl.addLayout(tr)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("background:rgba(80,160,255,40);max-height:1px;border:none;")
        cl.addWidget(div)

        # Section 1 — Memory
        cl.addWidget(self._section("  Memory & History",
            "Jarvis remembers past sessions to provide context."))
        b1 = self._action_btn("Delete All Memory")
        b1.clicked.connect(lambda: self._feedback("Memory cleared."))
        cl.addWidget(b1)

        # Section 2 — Preferences
        cl.addWidget(self._section("  Preferences",
            "Edit learned preferences in the sidebar → Preferences tab."))

        # Section 3 — Reset
        cl.addWidget(self._section("  Complete Reset",
            "Wipes all data, sessions and preferences. Fresh start."))
        b3 = self._action_btn("Factory Reset", danger=True)
        b3.clicked.connect(lambda: self._feedback("Reset complete. Restart the app."))
        cl.addWidget(b3)

        cl.addStretch()
        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet(
            f"color:{P.css(P.SUCCESS)};font-size:12px;background:transparent;"
        )
        cl.addWidget(self._status)

        # Opacity FX
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0)
        self.setGraphicsEffect(self._fx)

    def _section(self, title: str, sub: str) -> QWidget:
        w = QWidget(); w.setStyleSheet("background:transparent;")
        vl = QVBoxLayout(w); vl.setContentsMargins(0,0,0,0); vl.setSpacing(2)
        t = QLabel(title); t.setFont(QFont("Segoe UI",12,QFont.Weight.DemiBold))
        t.setStyleSheet(f"color:{P.css(P.TEXT)};background:transparent;")
        s = QLabel(sub); s.setWordWrap(True)
        s.setStyleSheet(f"color:{P.css(P.TEXT_DIM)};font-size:11px;background:transparent;")
        vl.addWidget(t); vl.addWidget(s)
        return w

    def _action_btn(self, label: str, danger: bool = False) -> QPushButton:
        btn = QPushButton(label); btn.setCursor(Qt.PointingHandCursor)
        if danger:
            btn.setStyleSheet("""
                QPushButton{background:rgba(255,80,100,20);color:rgba(255,80,100,220);
                border:1px solid rgba(255,80,100,100);border-radius:8px;padding:9px 18px;font-size:13px;}
                QPushButton:hover{background:rgba(255,80,100,50);}
            """)
        else:
            btn.setStyleSheet("""
                QPushButton{background:rgba(80,160,255,15);color:rgba(80,160,255,220);
                border:1px solid rgba(80,160,255,80);border-radius:8px;padding:9px 18px;font-size:13px;}
                QPushButton:hover{background:rgba(80,160,255,40);}
            """)
        return btn

    def _feedback(self, msg: str) -> None:
        self._status.setText(f"  {msg}")
        QTimer.singleShot(3000, lambda: self._status.setText(""))

    def _close(self) -> None:
        a = QPropertyAnimation(self._fx, b"opacity", self)
        a.setDuration(200); a.setStartValue(1.0); a.setEndValue(0.0)
        a.finished.connect(self.hide); a.finished.connect(self.closed.emit)
        a.start(QPropertyAnimation.DeleteWhenStopped)

    def show_modal(self) -> None:
        if self.parent():
            self.resize(self.parent().size())
        self._backdrop.resize(self.size())
        self._card.move(
            (self.width()  - self._card.width())  // 2,
            (self.height() - self._card.height()) // 2,
        )
        self.show(); self.raise_()
        a = QPropertyAnimation(self._fx, b"opacity", self)
        a.setDuration(240); a.setStartValue(0.0); a.setEndValue(1.0)
        a.start(QPropertyAnimation.DeleteWhenStopped)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._backdrop.resize(self.size())
        self._card.move(
            (self.width()  - self._card.width())  // 2,
            (self.height() - self._card.height()) // 2,
        )


# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
class SidebarWidget(QWidget):
    settings_requested = Signal()

    def __init__(self, state_mgr: CentralStateManager, parent=None):
        super().__init__(parent)
        self._state_mgr = state_mgr
        self.setFixedWidth(280)
        self.setStyleSheet("""
            background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 rgba(12,14,32,245),stop:1 rgba(10,12,26,200));
            border-right:1px solid rgba(80,160,255,40);
        """)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # Tab row
        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(12,14,12,0); tab_row.setSpacing(4)
        self._tabs: list[QPushButton] = []
        for i, (icon, label) in enumerate([("","History"),("","Preferences")]):
            btn = QPushButton(f"  {icon}  {label}")
            btn.setCheckable(True); btn.setChecked(i == 0)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, idx=i: self._switch(idx))
            btn.setStyleSheet("""
                QPushButton{background:transparent;color:rgba(120,140,180,200);
                border:none;border-radius:8px;padding:7px 10px;font-size:12px;text-align:left;}
                QPushButton:checked{background:rgba(80,160,255,20);color:rgba(220,230,255,240);
                border-bottom:2px solid rgba(80,160,255,200);}
                QPushButton:hover:!checked{color:rgba(180,200,240,220);}
            """)
            self._tabs.append(btn); tab_row.addWidget(btn)
        root.addLayout(tab_row)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background:rgba(80,160,255,30);max-height:1px;border:none;margin:8px 12px 0 12px;")
        root.addWidget(sep)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._history_page())
        self._stack.addWidget(self._prefs_page())
        root.addWidget(self._stack, 1)

        # Bottom bar
        bot = QWidget()
        bot.setStyleSheet("background:rgba(10,12,28,185);border-top:1px solid rgba(80,160,255,30);")
        bl = QVBoxLayout(bot); bl.setContentsMargins(12,10,12,14); bl.setSpacing(6)

        sb = QPushButton("     Settings"); sb.setCursor(Qt.PointingHandCursor)
        sb.setStyleSheet("""
            QPushButton{background:rgba(80,160,255,12);color:rgba(180,200,240,200);
            border:1px solid rgba(80,160,255,50);border-radius:9px;
            padding:9px 14px;font-size:13px;text-align:left;}
            QPushButton:hover{background:rgba(80,160,255,30);color:rgba(220,230,255,255);}
        """)
        sb.clicked.connect(self.settings_requested.emit)
        bl.addWidget(sb)

        hb = QPushButton("  ？  Help & Docs"); hb.setCursor(Qt.PointingHandCursor)
        hb.setStyleSheet("""
            QPushButton{background:transparent;color:rgba(120,140,180,180);
            border:none;border-radius:9px;padding:8px 14px;font-size:12px;text-align:left;}
            QPushButton:hover{color:rgba(180,200,240,220);}
        """)
        bl.addWidget(hb)
        root.addWidget(bot)

    def _switch(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._tabs):
            btn.setChecked(i == idx)

    def _history_page(self) -> QWidget:
        w = QWidget(); w.setStyleSheet("background:transparent;")
        vl = QVBoxLayout(w); vl.setContentsMargins(10,10,10,6); vl.setSpacing(6)
        lbl = QLabel("Recent Sessions")
        lbl.setStyleSheet(f"color:{P.css(P.TEXT_DIM)};font-size:10px;letter-spacing:1.5px;background:transparent;")
        vl.addWidget(lbl)

        groups = [
            ("Today", ["Phone recommendation – Pixel vs S25",
                        "Email draft for project proposal",
                        "Research: AI trends 2025"]),
            ("Yesterday", ["Opened Spotify, played lo-fi mix",
                           "Laptop under ₹50k recommendation"]),
            ("3 days ago", ["Weather for Delhi this week",
                             "Reminder: team meeting at 3 PM"]),
        ]
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}QScrollBar{width:4px;}")
        inner = QWidget(); inner.setStyleSheet("background:transparent;")
        il = QVBoxLayout(inner); il.setContentsMargins(0,0,4,0); il.setSpacing(4)
        for group, items in groups:
            gl = QLabel(group.upper())
            gl.setStyleSheet("color:rgba(80,160,255,160);font-size:9px;letter-spacing:2px;background:transparent;margin-top:8px;")
            il.addWidget(gl)
            for item in items:
                btn = QPushButton(f"  {item}"); btn.setCursor(Qt.PointingHandCursor)
                btn.setFixedHeight(36)
                btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                btn.setStyleSheet("""
                    QPushButton{background:rgba(80,160,255,8);color:rgba(180,200,240,200);
                    border:1px solid rgba(80,160,255,20);border-radius:7px;
                    font-size:11px;text-align:left;padding:0 8px;}
                    QPushButton:hover{background:rgba(80,160,255,22);color:rgba(220,230,255,240);
                    border-color:rgba(80,160,255,60);}
                """)
                il.addWidget(btn)
        il.addStretch(); scroll.setWidget(inner); vl.addWidget(scroll)
        return w

    def _prefs_page(self) -> QWidget:
        w = QWidget(); w.setStyleSheet("background:transparent;")
        vl = QVBoxLayout(w); vl.setContentsMargins(10,10,10,6); vl.setSpacing(8)
        lbl = QLabel("Learned Preferences")
        lbl.setStyleSheet(f"color:{P.css(P.TEXT_DIM)};font-size:10px;letter-spacing:1.5px;background:transparent;")
        vl.addWidget(lbl)

        prefs = [
            ("","Smartphone","Prefers Google Pixel"),
            ("","Laptop","Prefers Dell XPS"),
            ("","Music","Loves lo-fi & jazz"),
            ("","Location","Delhi, India"),
            ("","Theme","Dark always"),
            ("","Reminders","Gentle tone"),
        ]
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}QScrollBar{width:4px;}")
        inner = QWidget(); inner.setStyleSheet("background:transparent;")
        il = QVBoxLayout(inner); il.setContentsMargins(0,0,4,0); il.setSpacing(5)
        for icon, key, val in prefs:
            card = QWidget()
            card.setStyleSheet("background:rgba(80,160,255,8);border:1px solid rgba(80,160,255,25);border-radius:8px;")
            cl = QHBoxLayout(card); cl.setContentsMargins(10,8,10,8); cl.setSpacing(8)
            ic = QLabel(icon); ic.setFixedWidth(20); ic.setStyleSheet("background:transparent;font-size:14px;")
            kl = QLabel(f"<b style='color:rgba(180,200,240,220);font-size:11px;'>{key}</b>"
                        f"<br><span style='color:rgba(120,140,180,200);font-size:10px;'>{val}</span>")
            kl.setStyleSheet("background:transparent;")
            cl.addWidget(ic); cl.addWidget(kl, 1)
            il.addWidget(card)
        il.addStretch(); scroll.setWidget(inner); vl.addWidget(scroll)
        return w


# ══════════════════════════════════════════════════════════════════════════════
#  ORB PREVIEW  (home screen decorative widget)
# ══════════════════════════════════════════════════════════════════════════════
class GlowOrbPreview(QWidget):
    def __init__(self, radius: int = 55, parent=None):
        super().__init__(parent)
        self._r   = radius
        self._t   = 0.0
        self.setFixedSize(radius * 2 + 24, radius * 2 + 24)
        t = QTimer(self)
        t.timeout.connect(self._tick)
        t.start(20)   # 50 fps is plenty for a decorative preview

    def _tick(self) -> None:
        self._t += 0.04; self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx = self.width()  / 2.0
        cy = self.height() / 2.0
        r  = float(self._r)
        pulse = 0.5 + 0.5 * math.sin(self._t)

        for i in range(5, 0, -1):
            a = int(8 * i * pulse)
            grad = QRadialGradient(cx, cy, r + i * 8)
            grad.setColorAt(0, QColor(80,160,255,a))
            grad.setColorAt(1, QColor(80,160,255,0))
            p.setBrush(QBrush(grad)); p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx,cy), r + i*8, r + i*8)

        grad2 = QRadialGradient(cx - r*0.3, cy - r*0.3, r*1.4)
        grad2.setColorAt(0, QColor(160,210,255,220))
        grad2.setColorAt(0.4, QColor(80,160,255,200))
        grad2.setColorAt(1, QColor(60,80,180,180))
        p.setBrush(QBrush(grad2))
        p.setPen(QPen(QColor(140,200,255,100), 1.5))
        p.drawEllipse(QPointF(cx,cy), r, r)

        p.setBrush(QBrush(QColor(255,255,255,int(38*pulse))))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx - r*0.25, cy - r*0.3), r*0.34, r*0.19)
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  HOME SCREEN
# ══════════════════════════════════════════════════════════════════════════════
class HomeScreen(QWidget):
    start_requested = Signal()

    def __init__(self, state_mgr: CentralStateManager, parent=None):
        super().__init__(parent)
        self._state_mgr       = state_mgr
        self._sidebar_open    = False
        self._settings_modal  : SettingsModal | None = None
        self.setStyleSheet(f"background:{P.css(P.BG)};")
        self._build()

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        self._sidebar = SidebarWidget(self._state_mgr, self)
        self._sidebar.settings_requested.connect(self._open_settings)
        self._sidebar.hide()

        main = QWidget(); main.setStyleSheet("background:transparent;")
        root.addWidget(self._sidebar)
        root.addWidget(main, 1)

        ml = QVBoxLayout(main); ml.setContentsMargins(0,0,0,0); ml.setSpacing(0)
        ml.addWidget(self._topbar())

        center = QWidget(); center.setStyleSheet("background:transparent;")
        ml.addWidget(center, 1)
        cl = QVBoxLayout(center); cl.setAlignment(Qt.AlignCenter); cl.setSpacing(26)
        cl.addWidget(GlowOrbPreview(55), 0, Qt.AlignCenter)
        greet = QLabel("Good evening.")
        greet.setFont(QFont("Segoe UI", 28, QFont.Weight.Light))
        greet.setAlignment(Qt.AlignCenter)
        greet.setStyleSheet(f"color:{P.css(P.TEXT)};background:transparent;")
        cl.addWidget(greet)
        sub = QLabel("Your AI assistant is ready.")
        sub.setFont(QFont("Segoe UI", 13))
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"color:{P.css(P.TEXT_DIM)};background:transparent;")
        cl.addWidget(sub)
        cl.addWidget(self._start_btn(), 0, Qt.AlignCenter)

        ml.addWidget(self._statusbar())

        # Settings modal
        self._settings_modal = SettingsModal(self._state_mgr, self)
        self._settings_modal.hide()

    def _topbar(self) -> QWidget:
        bar = QWidget(); bar.setFixedHeight(56)
        bar.setStyleSheet("background:rgba(10,12,28,200);border-bottom:1px solid rgba(80,160,255,30);")
        bl = QHBoxLayout(bar); bl.setContentsMargins(14,0,18,0)

        self._ham = QPushButton(""); self._ham.setFixedSize(40,40)
        self._ham.setCheckable(True); self._ham.setCursor(Qt.PointingHandCursor)
        self._ham.clicked.connect(self._toggle_sidebar)
        self._ham.setStyleSheet("""
            QPushButton{background:rgba(80,160,255,15);color:rgba(180,200,240,200);
            border:1px solid rgba(80,160,255,40);border-radius:10px;font-size:16px;}
            QPushButton:hover{background:rgba(80,160,255,35);color:rgba(220,230,255,255);}
            QPushButton:checked{background:rgba(80,160,255,50);color:rgba(220,230,255,255);}
        """)
        logo = QLabel("J A R V I S")
        logo.setFont(QFont("Segoe UI",14,QFont.Weight.Light))
        logo.setStyleSheet("color:rgba(80,160,255,220);letter-spacing:6px;background:transparent;")
        dot = QLabel("●  Online")
        dot.setStyleSheet("color:rgba(80,220,160,200);font-size:11px;background:transparent;")
        bl.addWidget(self._ham); bl.addSpacing(10); bl.addWidget(logo)
        bl.addStretch(); bl.addWidget(dot)
        return bar

    def _start_btn(self) -> QPushButton:
        btn = QPushButton("  ◉   Start Assisting"); btn.setFixedSize(222,50)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 rgba(80,160,255,210),stop:1 rgba(120,80,255,210));
                color:white;border:none;border-radius:25px;font-size:14px;
                font-weight:500;letter-spacing:1px;}
            QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 rgba(100,175,255,235),stop:1 rgba(140,100,255,235));}
            QPushButton:pressed{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 rgba(60,130,220,225),stop:1 rgba(100,60,220,225));}
        """)
        btn.clicked.connect(self.start_requested.emit)
        return btn

    def _statusbar(self) -> QWidget:
        bar = QWidget(); bar.setFixedHeight(32)
        bar.setStyleSheet("background:rgba(8,8,20,160);border-top:1px solid rgba(80,160,255,20);")
        bl = QHBoxLayout(bar); bl.setContentsMargins(18,0,18,0)
        items = [" Mic: Ready"," TTS: Ready"," Model: Connected"]
        for i, item in enumerate(items):
            lbl = QLabel(item)
            lbl.setStyleSheet("color:rgba(100,120,160,180);font-size:10px;background:transparent;")
            bl.addWidget(lbl)
            if i < len(items)-1:
                sep = QLabel("·")
                sep.setStyleSheet("color:rgba(80,160,255,80);background:transparent;")
                bl.addSpacing(8); bl.addWidget(sep); bl.addSpacing(8)
        bl.addStretch()
        ver = QLabel("v7.0")
        ver.setStyleSheet("color:rgba(80,160,255,100);font-size:10px;background:transparent;")
        bl.addWidget(ver)
        return bar

    def _toggle_sidebar(self) -> None:
        self._sidebar_open = not self._sidebar_open
        self._ham.setChecked(self._sidebar_open)
        if self._sidebar_open:
            self._sidebar.setMaximumWidth(0)
            self._sidebar.show()
            a = QPropertyAnimation(self._sidebar, b"maximumWidth", self)
            a.setDuration(260); a.setStartValue(0); a.setEndValue(280)
            a.setEasingCurve(QEasingCurve.OutCubic)
            a.start(QPropertyAnimation.DeleteWhenStopped)
        else:
            a = QPropertyAnimation(self._sidebar, b"maximumWidth", self)
            a.setDuration(220); a.setStartValue(280); a.setEndValue(0)
            a.setEasingCurve(QEasingCurve.InCubic)
            a.finished.connect(self._sidebar.hide)
            a.start(QPropertyAnimation.DeleteWhenStopped)

    def _open_settings(self) -> None:
        if self._settings_modal:
            self._settings_modal.resize(self.size())
            self._settings_modal.show_modal()
            self._settings_modal.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._settings_modal:
            self._settings_modal.resize(self.size())


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ══════════════════════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    def __init__(self, state_mgr: CentralStateManager):
        super().__init__()
        self._state_mgr  = state_mgr
        self._drag_origin: QPoint | None = None

        self.setWindowTitle("Jarvis")
        self.setMinimumSize(900, 600)
        self.resize(1080, 680)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Ripple overlay (desktop-level, behind orb)
        self._ripple = RippleOverlay()
        screen = QApplication.primaryScreen().geometry()
        self._ripple.resize(screen.width(), screen.height())
        self._ripple.show()

        # Central container
        self._container = QWidget()
        self._container.setStyleSheet("""
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 rgb(8,8,20),stop:0.5 rgb(10,10,26),stop:1 rgb(6,6,16));
            border-radius:14px;
        """)
        self._container.setAttribute(Qt.WA_StyledBackground)
        self.setCentralWidget(self._container)

        self._stack = QStackedWidget(self._container)
        ml = QVBoxLayout(self._container)
        ml.setContentsMargins(0,0,0,0)
        ml.addWidget(self._stack)

        # Screens
        self._intro = IntroScreen()
        self._intro.finished.connect(self._show_home)
        self._home = HomeScreen(state_mgr)
        self._home.start_requested.connect(self._start_assisting)
        self._stack.addWidget(self._intro)  # 0
        self._stack.addWidget(self._home)   # 1
        self._stack.setCurrentIndex(0)

        # Orb (created but hidden until Start Assisting)
        self._orb = OrbWidget(state_mgr.orb_ctrl, self._ripple)
        self._orb.hide()

        # Window fade-in
        self._win_fx = QGraphicsOpacityEffect(self)
        self._win_fx.setOpacity(0.0)
        self.setGraphicsEffect(self._win_fx)
        QTimer.singleShot(80, self._fade_in)

    def _fade_in(self) -> None:
        a = QPropertyAnimation(self._win_fx, b"opacity", self)
        a.setDuration(600); a.setStartValue(0.0); a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.OutCubic)
        a.start(QPropertyAnimation.DeleteWhenStopped)

    def _show_home(self) -> None:
        fx_out = QGraphicsOpacityEffect(self._intro)
        self._intro.setGraphicsEffect(fx_out)
        a_out = QPropertyAnimation(fx_out, b"opacity", self)
        a_out.setDuration(380); a_out.setStartValue(1.0); a_out.setEndValue(0.0)
        a_out.setEasingCurve(QEasingCurve.InCubic)

        def _swap() -> None:
            self._stack.setCurrentIndex(1)
            fx_in = QGraphicsOpacityEffect(self._home)
            self._home.setGraphicsEffect(fx_in)
            fx_in.setOpacity(0.0)
            a_in = QPropertyAnimation(fx_in, b"opacity", self)
            a_in.setDuration(480); a_in.setStartValue(0.0); a_in.setEndValue(1.0)
            a_in.setEasingCurve(QEasingCurve.OutCubic)
            a_in.start(QPropertyAnimation.DeleteWhenStopped)

        a_out.finished.connect(_swap)
        a_out.start(QPropertyAnimation.DeleteWhenStopped)

    def _start_assisting(self) -> None:
        a = QPropertyAnimation(self._win_fx, b"opacity", self)
        a.setDuration(320); a.setStartValue(1.0); a.setEndValue(0.0)
        a.setEasingCurve(QEasingCurve.InCubic)
        a.finished.connect(self._show_orb)
        a.start(QPropertyAnimation.DeleteWhenStopped)

    def _show_orb(self) -> None:
        self.hide()
        screen = QApplication.primaryScreen().geometry()
        self._orb.move(screen.width() - 145, screen.height() - 145)
        self._state_mgr.orb_ctrl.set_state(OrbState.IDLE)
        self._orb.show()
        self._orb.raise_()

    # ── Frameless drag ─────────────────────────────────────────────────────
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_origin = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_origin)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_origin = None

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rc = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(80,160,255,50), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rc, 14, 14)
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
def main() -> None:
    # Prevent multiple QApplication instances — fixes the infinite-loop bug
    # caused by main() being re-entered when the module was imported.
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Jarvis")

    debug_mode = "--debug" in sys.argv

    pal = app.palette()
    pal.setColor(QPalette.ColorRole.Window,     P.BG)
    pal.setColor(QPalette.ColorRole.WindowText, P.TEXT)
    pal.setColor(QPalette.ColorRole.Base,       QColor(14,14,28))
    pal.setColor(QPalette.ColorRole.Text,       P.TEXT)
    app.setPalette(pal)

    state_mgr = CentralStateManager(debug_mode=debug_mode)
    win = MainWindow(state_mgr)
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()