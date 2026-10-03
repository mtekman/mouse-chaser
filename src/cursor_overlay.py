from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter
from PyQt6.QtCore import QRectF, Qt

from spaceship import SpacecraftState, clamp

class CursorOverlay(QWidget):
    """Transparent, click-through, always-on-top SVG spaceship overlay."""

    def __init__(self, OVERLAY_SIZE, SPACESHIP_SVG) -> None:
        super().__init__()
        self.overlay_size = OVERLAY_SIZE

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.BypassWindowManagerHint
            | Qt.WindowType.WindowTransparentForInput
        )

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )

        self.setFixedSize(self.overlay_size, self.overlay_size)

        self.shape = SpacecraftState()

        self.svg_renderer = QSvgRenderer()
        self.svg_renderer.load(SPACESHIP_SVG)

        if not self.svg_renderer.isValid():
            raise RuntimeError("The embedded spaceship SVG could not be parsed.")

    def set_shape(self, shape: SpacecraftState) -> None:
        self.shape = shape
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setOpacity(
            clamp(self.shape.distance_opacity, 0.0, 1.0)
        )
        center_x = self.overlay_size / 2.0
        center_y = self.overlay_size / 2.0

        painter.save()
        painter.translate(center_x, center_y)

        # Qt rotation is clockwise in widget/screen coordinates.
        painter.rotate(self.shape.angle_deg)

        # Scale uniformly, then stretch along the SVG's forward axis.
        painter.scale(
            self.shape.scale,
            self.shape.scale * self.shape.stretch,
        )

        # Render at the SVG's logical bounds. The painter transform scales and
        # rotates the vector image at draw time.
        self.svg_renderer.render(
            painter,
            QRectF(-10.0, -2.0, 20.0, 24.0),
        )

        painter.restore()
        painter.end()
