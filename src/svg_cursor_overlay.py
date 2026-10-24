from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QWidget


class SvgCursorOverlay(QWidget):
    """Transparent pointer-locked overlay for a configured cursor SVG."""

    def __init__(self, svg: bytes) -> None:
        super().__init__()
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

        self.svg_renderer = QSvgRenderer(svg)
        if not self.svg_renderer.isValid():
            raise RuntimeError("The configured cursor SVG could not be parsed.")

        size = self.svg_renderer.defaultSize()
        self.setFixedSize(size)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.svg_renderer.render(
            painter,
            QRectF(0, 0, self.width(), self.height()),
        )
        painter.end()
