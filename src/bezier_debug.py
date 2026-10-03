from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen

class BezierDebugOverlay(QWidget):
    """
    Optional full-screen debug overlay.

    Draws:
    - Green curves: active multi-segment Bezier route.
    - Cyan dashed lines: control polygons.
    - Red/cyan/blue/magenta points: P0/P1/P2/P3 for each segment.
    - Yellow circle: current ship position.
    """

    def __init__(self) -> None:
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

        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())

        self.segments: list[BezierSegment] = []
        self.current_position: tuple[float, float] | None = None

    def set_route(
        self,
        segments: list[BezierSegment],
        current_position: tuple[float, float],
    ) -> None:
        self.segments = segments
        self.current_position = current_position
        self.update()

    def paintEvent(self, event) -> None:
        if not self.segments:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Control polygons.
        control_pen = QPen(QColor(0, 220, 255, 130))
        control_pen.setWidthF(1.0)
        control_pen.setStyle(Qt.PenStyle.DashLine)

        painter.setPen(control_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        for p0, p1, p2, p3 in self.segments:
            painter.drawLine(QPointF(*p0), QPointF(*p1))
            painter.drawLine(QPointF(*p1), QPointF(*p2))
            painter.drawLine(QPointF(*p2), QPointF(*p3))

        # Bezier curves.
        curve_pen = QPen(QColor(0, 255, 130, 230))
        curve_pen.setWidthF(2.0)

        painter.setPen(curve_pen)

        for p0, p1, p2, p3 in self.segments:
            curve = QPainterPath()
            curve.moveTo(QPointF(*p0))
            curve.cubicTo(
                QPointF(*p1),
                QPointF(*p2),
                QPointF(*p3),
            )
            painter.drawPath(curve)

        # Control points.
        colors = [
            QColor(255, 100, 100, 255),
            QColor(0, 220, 255, 255),
            QColor(0, 180, 255, 255),
            QColor(255, 80, 255, 255),
        ]
        labels = ["P0", "P1", "P2", "P3"]

        for segment in self.segments:
            for point, color, label in zip(
                segment,
                colors,
                labels,
            ):
                self._draw_point(
                    painter,
                    QPointF(*point),
                    color,
                    label,
                )

        # Current position.
        if self.current_position is not None:
            current = QPointF(*self.current_position)

            current_pen = QPen(QColor(255, 235, 0, 255))
            current_pen.setWidthF(2.0)

            painter.setPen(current_pen)
            painter.setBrush(QColor(255, 235, 0, 110))
            painter.drawEllipse(current, 6.0, 6.0)

        painter.end()

    def _draw_point(
        self,
        painter: QPainter,
        point: QPointF,
        color: QColor,
        label: str,
    ) -> None:
        pen = QPen(color)
        pen.setWidthF(1.5)

        painter.setPen(pen)
        painter.setBrush(
            QColor(color.red(), color.green(), color.blue(), 120)
        )
        painter.drawEllipse(point, 4.0, 4.0)

        painter.setPen(QColor(255, 255, 255, 220))
        painter.drawText(
            QPointF(point.x() + 7.0, point.y() - 7.0),
            label,
        )

