from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from Xlib import display, X
##from Xlib.ext import xfixes

from sys import argv, stderr

from bezier_physics import BezierPhysics
from bezier_debug import BezierDebugOverlay
from cursor_overlay import CursorOverlay
from green_dot import GreenDotOverlay


class CursorX11:
    def __init__(self, SPACESHIP_SVG, OVERLAY_SIZE, GREEN_DOT_SIZE, DEBUG_DRAW_BEZIER, POLL_MS) -> None:
        self.running = True
        self.physics = BezierPhysics()

        self.overlay_size = OVERLAY_SIZE
        self.green_dot_size = GREEN_DOT_SIZE

        self.dpy = display.Display()
        self.root = self.dpy.screen().root

        xfixes_version = self.dpy.xfixes_query_version()
        if xfixes_version.major_version < 2:
            raise RuntimeError("XFIXES >= 2.0 is required to hide the cursor.")

        self.root.xfixes_hide_cursor()

        self.blank_cursor = self._create_transparent_xcursor()
        self.root.change_attributes(cursor=self.blank_cursor)
        self.dpy.sync()

        self.qt_app = QApplication(argv)

        # Exactly one spaceship overlay.
        self.overlay = CursorOverlay(self.overlay_size, SPACESHIP_SVG)
        self.overlay.show()

        # Exactly one green-dot overlay.
        self.green_dot = GreenDotOverlay(GREEN_DOT_SIZE)
        self.green_dot.show()

        # Optional debug overlay.
        self.bezier_debug: BezierDebugOverlay | None = None

        if DEBUG_DRAW_BEZIER:
            self.bezier_debug = BezierDebugOverlay()
            self.bezier_debug.show()

        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(POLL_MS)

    def _create_transparent_xcursor(self):
        """Create an invisible X cursor from two one-bit pixmaps."""
        source = self.root.create_pixmap(1, 1, 1)
        mask = self.root.create_pixmap(1, 1, 1)

        source_gc = source.create_gc(foreground=0, background=0)
        mask_gc = mask.create_gc(foreground=0, background=0)

        source.poly_fill_rectangle(source_gc, [(0, 0, 1, 1)])
        mask.poly_fill_rectangle(mask_gc, [(0, 0, 1, 1)])

        cursor = source.create_cursor(
            mask,
            (0, 0, 0),
            (0, 0, 0),
            0,
            0,
        )

        source_gc.free()
        mask_gc.free()
        source.free()
        mask.free()

        return cursor

    def pointer_position(self) -> tuple[int, int, int]:
        """
        Return the pointer location and button/modifier mask.

        The mask contains X.Button1Mask when the left mouse button is down.
        """
        reply = self.root.query_pointer()
        return reply.root_x, reply.root_y, reply.mask

    def tick(self) -> None:
        """Update physics, repaint the ship, and lock the dot to the pointer."""
        if not self.running:
            return

        try:
            pointer_x, pointer_y, button_mask = self.pointer_position()

            mouse_down = bool(button_mask & X.Button1Mask)

            shape = self.physics.update(
                pointer_x,
                pointer_y,
                mouse_down=mouse_down,
            )
            
            # Update the ship's visual state before moving its window.
            self.overlay.set_shape(shape)

            ship_x = round(self.physics.ship_x)
            ship_y = round(self.physics.ship_y)

            self.overlay.move(
                ship_x - self.overlay_size // 2,
                ship_y - self.overlay_size // 2,
            )

            # Immediate repaint reduces the chance of a stale X11 frame.
            self.overlay.repaint()

            # Keep the green dot exactly on the real pointer.
            self.green_dot.move(
                pointer_x - self.green_dot_size // 2,
                pointer_y - self.green_dot_size // 2,
            )

            if self.bezier_debug is not None:
                self.bezier_debug.set_route(
                    self.physics.debug_segments(),
                    self.physics.debug_position(),
                )

        except Exception as error:
            print(f"Cursor update failed: {error}", file=stderr)
            self.stop()
            self.qt_app.quit()

    def stop(self, *_args) -> None:
        """Restore the normal X cursor and release resources."""
        if not self.running:
            return

        self.running = False

        try:
            self.timer.stop()
        except Exception:
            pass

        try:
            if self.bezier_debug is not None:
                self.bezier_debug.close()
        except Exception:
            pass

        try:
            self.green_dot.close()
        except Exception:
            pass

        try:
            self.overlay.close()
        except Exception:
            pass

        try:
            self.root.change_attributes(cursor=X.NONE)
            self.dpy.sync()
        except Exception:
            pass

        try:
            self.blank_cursor.free()
        except Exception:
            pass

        try:
            self.dpy.close()
        except Exception:
            pass
