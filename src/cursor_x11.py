from sys import argv, stderr
import threading

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from Xlib import display, X, XK, error
from Xlib.ext import record
from Xlib.protocol import rq

from bezier_physics import BezierPhysics
from bezier_debug import BezierDebugOverlay
from cursor_overlay import CursorOverlay
from green_dot import GreenDotOverlay


class CursorX11(QObject):
    # Emitted from the XRecord thread; connected slots run on Qt's main thread.
    key_pressed = pyqtSignal(str)

    def __init__(
        self,
        SPACESHIP_SVG,
        OVERLAY_SIZE,
        GREEN_DOT_SIZE,
        DEBUG_DRAW_BEZIER,
        POLL_MS,
        CONTROL_KEYS=("Control_L", "Control_R"),
        SHIFT_KEYS=("Shift_L", "Shift_R"),
    ) -> None:
        super().__init__()

        self.running = True
        self.physics = BezierPhysics()

        self.overlay_size = OVERLAY_SIZE
        self.green_dot_size = GREEN_DOT_SIZE

        self.control_keys = tuple(CONTROL_KEYS)
        self.shift_keys = tuple(SHIFT_KEYS)

        # keycode -> keysym name
        self.monitored_keycodes: dict[int, str] = {}

        self.overlay_visible = False
        self.debug_visible = False

        self.dpy = display.Display()
        self.root = self.dpy.screen().root

        self.qt_app = QApplication(argv)

        self._configure_key_listener()

        self.key_pressed.connect(self._handle_key_pressed)

        if GREEN_DOT_SIZE > 0:
            xfixes_version = self.dpy.xfixes_query_version()

            if xfixes_version.major_version < 2:
                raise RuntimeError("XFIXES >= 2.0 is required to hide the cursor.")

            self.root.xfixes_hide_cursor()
            self.blank_cursor = self._create_transparent_xcursor()
            self.root.change_attributes(cursor=self.blank_cursor)
            self.dpy.sync()

            self.green_dot = GreenDotOverlay(GREEN_DOT_SIZE)
            self.green_dot.show()
        else:
            self.green_dot = None

        self.overlay = CursorOverlay(self.overlay_size, SPACESHIP_SVG)
        self.overlay.hide()

        self.bezier_debug: BezierDebugOverlay | None = None

        # if DEBUG_DRAW_BEZIER: Toggled now by shift
        self.bezier_debug = BezierDebugOverlay()
        self.bezier_debug.hide()

        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(POLL_MS)

    def _configure_key_listener(self) -> None:
        """
        Passively monitor Control and Shift keys with XRecord.

        No grab is created, so the desktop continues to receive all original
        keyboard events unchanged.
        """
        monitored_keys = self.control_keys + self.shift_keys

        if not monitored_keys:
            raise ValueError("At least one Control or Shift key is required.")

        for key_name in monitored_keys:
            keysym = XK.string_to_keysym(key_name)

            if keysym == 0:
                raise ValueError(f"Unknown X11 keysym: {key_name!r}")

            keycode = self.dpy.keysym_to_keycode(keysym)

            if keycode == 0:
                raise ValueError(
                    f"Could not map keysym to a keycode: {key_name!r}"
                )

            self.monitored_keycodes[keycode] = key_name

        # A separate connection keeps XRecord isolated from normal events.
        self.record_dpy = display.Display()

        self.record_context = self.record_dpy.record_create_context(
            0,
            [record.AllClients],
            [{
                "core_requests": (0, 0),
                "core_replies": (0, 0),
                "ext_requests": (0, 0, 0, 0),
                "ext_replies": (0, 0, 0, 0),
                "delivered_events": (0, 0),
                "device_events": (X.KeyPress, X.KeyRelease),
                "errors": (0, 0),
                "client_started": False,
                "client_died": False,
            }],
        )

        self.record_thread = threading.Thread(
            target=self._record_loop,
            daemon=True,
            name="X11-key-listener",
        )
        self.record_thread.start()

    def _record_loop(self) -> None:
        """
        Enable XRecord. This blocks while recording, so run it in a thread.
        """
        try:
            self.record_dpy.record_enable_context(
                self.record_context,
                self._record_callback,
            )
        except Exception as error:
            print(f"X11 RECORD listener failed: {error}", file=stderr)

    def _record_callback(self, reply) -> None:
        """
        Parse XRecord protocol data and emit only KeyPress events for
        configured keys.
        """
        if reply.category != record.FromServer:
            return

        if reply.client_swapped:
            return

        if not len(reply.data) or reply.data[0] < 2:
            return

        data = reply.data

        while len(data):
            event, data = rq.EventField(None).parse_binary_value(
                data,
                self.record_dpy.display,
                None,
                None,
            )

            if event.type != X.KeyPress:
                continue

            key_name = self.monitored_keycodes.get(event.detail)

            if key_name is None:
                continue

            self.key_pressed.emit(key_name)

    def _handle_key_pressed(self, key_name: str) -> None:
        """
        Handle a key press on Qt's main thread.

        Control toggles the spaceship overlay.
        Shift toggles the Bezier debug overlay.
        """
        if not self.running:
            return

        if key_name in self.control_keys:
            self.overlay_visible = not self.overlay_visible

            if self.overlay_visible:
                self.overlay.show()
            else:
                self.overlay.hide()

        if key_name in self.shift_keys:
            print("HERE")
            if self.bezier_debug is None:
                print("HERE2")
                return

            self.debug_visible = not self.debug_visible

            print("HERE3")
            if self.debug_visible:
                print("HERE4")
                self.bezier_debug.show()
            else:
                self.bezier_debug.hide()

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
        """
        Update physics and render the spaceship only while the overlay is
        visible.
        """
        if not self.running:
            return

        if not self.overlay_visible:
            return

        try:
            pointer_x, pointer_y, button_mask = self.pointer_position()

            mouse_down = bool(button_mask & X.Button1Mask)

            shape = self.physics.update(
                pointer_x,
                pointer_y,
                mouse_down=mouse_down,
            )

            self.overlay.set_shape(shape)

            ship_x = round(self.physics.ship_x)
            ship_y = round(self.physics.ship_y)

            self.overlay.move(
                ship_x - self.overlay_size // 2,
                ship_y - self.overlay_size // 2,
            )

            self.overlay.repaint()

            if self.green_dot:
                self.green_dot.move(
                    pointer_x - self.green_dot_size // 2,
                    pointer_y - self.green_dot_size // 2,
                )

            if self.bezier_debug is not None and self.debug_visible:
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
            self.record_dpy.record_disable_context(self.record_context)
        except Exception:
            pass

        try:
            self.record_thread.join(timeout=1)
        except Exception:
            pass

        try:
            self.record_dpy.record_free_context(self.record_context)
        except Exception:
            pass

        try:
            self.record_dpy.close()
        except Exception:
            pass

        try:
            if self.bezier_debug is not None:
                self.bezier_debug.close()
        except Exception:
            pass

        if self.green_dot:
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