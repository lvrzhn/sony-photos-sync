"""Floating thumbnail popup near the menu bar."""
import threading

from AppKit import (
    NSBorderlessWindowMask,
    NSColor,
    NSFont,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSTextField,
    NSFloatingWindowLevel,
    NSView,
)
import objc


class ThumbnailPopup:
    """Shows a floating thumbnail near the menu bar that auto-dismisses."""

    _panel = None

    @classmethod
    def show(cls, image_path, filename, status_item=None, duration=3.0):
        """Show thumbnail popup. Called from main thread via rumps timer."""
        cls._show_on_main_thread(image_path, filename, status_item, duration)

    @classmethod
    def _show_on_main_thread(cls, image_path, filename, status_item, duration):
        # Dismiss any existing popup
        if cls._panel:
            cls._panel.orderOut_(None)
            cls._panel = None

        padding = 10
        label_h = 24
        max_thumb = 200

        # Load image and get aspect ratio
        ns_image = NSImage.alloc().initByReferencingFile_(str(image_path))
        if not ns_image or not ns_image.isValid():
            return

        img_size = ns_image.size()
        img_w, img_h = img_size.width, img_size.height

        # Scale to fit within max_thumb while preserving aspect ratio
        if img_w >= img_h:
            thumb_w = max_thumb
            thumb_h = int(max_thumb * img_h / img_w)
        else:
            thumb_h = max_thumb
            thumb_w = int(max_thumb * img_w / img_h)

        panel_w = thumb_w + padding * 2
        panel_h = thumb_h + label_h + padding * 3

        # Calculate position (below menu bar)
        screen = NSScreen.mainScreen()
        screen_frame = screen.frame()
        visible = screen.visibleFrame()
        menu_bar_height = screen_frame.size.height - visible.size.height - visible.origin.y

        # Position near the status item if available
        x = screen_frame.size.width - panel_w - 10
        if status_item and status_item.button():
            btn_window = status_item.button().window()
            if btn_window:
                btn_frame = btn_window.frame()
                x = btn_frame.origin.x + btn_frame.size.width / 2 - panel_w / 2
                x = max(10, min(x, screen_frame.size.width - panel_w - 10))

        y = screen_frame.size.height - menu_bar_height - panel_h - 5

        # Create panel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, panel_w, panel_h),
            NSBorderlessWindowMask,
            2,  # NSBackingStoreBuffered
            False,
        )
        panel.setLevel_(NSFloatingWindowLevel + 1)
        panel.setOpaque_(False)
        panel.setHasShadow_(True)
        panel.setMovableByWindowBackground_(False)
        panel.setHidesOnDeactivate_(False)
        panel.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(0.15, 0.95))

        # Container view
        content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, panel_w, panel_h))

        # Image view (sized to actual aspect ratio)
        img_view = NSImageView.alloc().initWithFrame_(
            NSMakeRect(padding, label_h + padding * 2, thumb_w, thumb_h)
        )
        img_view.setImage_(ns_image)
        img_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        content.addSubview_(img_view)

        # Filename label
        label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(padding, padding, thumb_w, label_h)
        )
        label.setStringValue_(filename)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setTextColor_(NSColor.whiteColor())
        label.setFont_(NSFont.systemFontOfSize_(11))
        label.setAlignment_(1)  # NSTextAlignmentCenter
        content.addSubview_(label)

        panel.setContentView_(content)
        panel.orderFrontRegardless()
        cls._panel = panel

        # Auto-dismiss
        def dismiss():
            import time
            time.sleep(duration)
            try:
                cls._dismiss()
            except Exception:
                pass

        threading.Thread(target=dismiss, daemon=True).start()

    @classmethod
    def _dismiss(cls):
        if cls._panel:
            cls._panel.orderOut_(None)
            cls._panel = None
