#!/usr/bin/env python3
"""
macro_clicker_gui.py — a Tkinter GUI for the image-search macro clicker.

It wraps the same engine the CLI uses (`macro_clicker.run_clicker`) and adds the
things a terminal can't do nicely:

    • Capture target — drag a box over a frozen screenshot to grab the button,
      no Cmd+Shift+4 / save-to-Desktop dance.
    • Pick region  — same drag gesture to limit the search area.
    • Find once    — locate the target and flash a highlight where it matched,
      so you can tune the confidence slider before running for real.
    • Live dashboard — status light, click/miss counters, elapsed time, last
      match coordinates, and a log — while the click loop runs in a worker
      thread (the UI never freezes).

Run:  python macro_clicker_gui.py
Needs the same macOS permissions as the CLI (Accessibility + Screen Recording)
and captures the main display only.
"""

from __future__ import annotations

import queue
import sys
import threading
import time

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
except ImportError:
    sys.exit(
        "Tkinter isn't available in this Python interpreter.\n"
        "On Homebrew Python, install it to match your version, e.g.:\n"
        "    brew install python-tk@3.14\n"
        "or run this GUI with a python.org / pyenv build that bundles Tk."
    )

try:
    import pyautogui
    from PIL import Image, ImageTk
except ImportError:
    sys.exit("Dependencies missing. Run:  pip install -r requirements.txt")

from macro_clicker import (
    ClickSpec,
    ClickerEvent,
    Region,
    find_target,
    run_clicker,
    screen_scale,
)

# Palette (kept small — Tk is the plain, dependable option).
COL_IDLE = "#8a8a8e"
COL_BUSY = "#3478f6"
COL_OK = "#34c759"
COL_ERR = "#ff3b30"


class OverlaySelector:
    """Full-screen drag-to-select over a frozen screenshot.

    Returns (screenshot, crop_box_px, region_logical) or None if cancelled.
    `crop_box_px` is in screenshot pixels (for cropping the needle);
    `region_logical` is a Region in logical points (for --region / find_target).
    """

    def __init__(self, root: tk.Tk, hint: str) -> None:
        self.root = root
        self.hint = hint

    def select(self):
        shot = pyautogui.screenshot()
        shot_w, shot_h = shot.size
        scale_x, scale_y = screen_scale(shot.size)
        logical_w, logical_h = pyautogui.size()

        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.geometry(f"{logical_w}x{logical_h}+0+0")

        canvas = tk.Canvas(top, highlightthickness=0, bg="black", cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        top.update_idletasks()

        canvas_w = canvas.winfo_width() or logical_w
        canvas_h = canvas.winfo_height() or logical_h
        # Fit the screenshot into the canvas preserving aspect (px -> canvas pts).
        fit = min(canvas_w / shot_w, canvas_h / shot_h)
        disp_w, disp_h = int(shot_w * fit), int(shot_h * fit)
        off_x = (canvas_w - disp_w) / 2
        off_y = (canvas_h - disp_h) / 2

        self._photo = ImageTk.PhotoImage(shot.resize((disp_w, disp_h)))
        canvas.create_image(off_x, off_y, anchor="nw", image=self._photo)
        canvas.create_text(
            canvas_w / 2, 28, fill="#ffffff", font=("Helvetica", 15, "bold"),
            text=self.hint + "   ·   drag to select   ·   Esc / right-click to cancel",
        )

        self._result = None
        self._start = None
        self._rect = None

        def on_press(event):
            self._start = (event.x, event.y)
            if self._rect is not None:
                canvas.delete(self._rect)
                self._rect = None

        def on_drag(event):
            if self._start is None:
                return
            x0, y0 = self._start
            if self._rect is None:
                self._rect = canvas.create_rectangle(
                    x0, y0, event.x, event.y,
                    outline=COL_BUSY, width=2, fill=COL_BUSY, stipple="gray12",
                )
            else:
                canvas.coords(self._rect, x0, y0, event.x, event.y)

        def on_release(event):
            if self._start is None:
                return
            x0, y0 = self._start
            x1, y1 = event.x, event.y
            (cx0, cx1), (cy0, cy1) = sorted((x0, x1)), sorted((y0, y1))
            if (cx1 - cx0) < 4 or (cy1 - cy0) < 4:
                return  # ignore an accidental click / tiny drag
            # Canvas points -> screenshot pixels.
            px0 = max(0, min(shot_w, round((cx0 - off_x) / fit)))
            py0 = max(0, min(shot_h, round((cy0 - off_y) / fit)))
            px1 = max(0, min(shot_w, round((cx1 - off_x) / fit)))
            py1 = max(0, min(shot_h, round((cy1 - off_y) / fit)))
            if px1 - px0 < 1 or py1 - py0 < 1:
                return
            crop_box = (px0, py0, px1, py1)
            # Screenshot pixels -> logical points for the region.
            region = Region(
                int(px0 / scale_x),
                int(py0 / scale_y),
                int((px1 - px0) / scale_x),
                int((py1 - py0) / scale_y),
            )
            self._result = (shot, crop_box, region)
            top.destroy()

        def on_cancel(_event=None):
            self._result = None
            top.destroy()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        for widget in (top, canvas):
            widget.bind("<Escape>", on_cancel)
            widget.bind("<Button-3>", on_cancel)

        top.lift()
        top.focus_force()
        self.root.wait_window(top)
        return self._result


class FlashHighlight:
    """Briefly flash a translucent rectangle at a screen location (logical pts)."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root

    def show(self, center: tuple[int, int], size: tuple[int, int], ms: int = 650) -> None:
        w = max(int(size[0]), 8)
        h = max(int(size[1]), 8)
        x = int(center[0] - w / 2)
        y = int(center[1] - h / 2)
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        try:
            top.attributes("-alpha", 0.35)
        except tk.TclError:
            pass
        top.configure(bg=COL_OK)
        top.geometry(f"{w}x{h}+{x}+{y}")
        top.after(ms, top.destroy)


class ClickerGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Macro Clicker")
        root.minsize(720, 460)

        # Engine plumbing.
        self.queue: "queue.Queue[ClickerEvent]" = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self._running = False
        self._terminal = False
        self._start_ts: float | None = None

        # Target state.
        self.needle: "str | Image.Image | None" = None
        self.needle_size: tuple[int, int] | None = None  # pixels
        self._thumb: ImageTk.PhotoImage | None = None
        self.region: Region | None = None
        self._scale: tuple[float, float] | None = None

        # Control variables.
        self.var_conf = tk.DoubleVar(value=0.90)
        self.var_conf_text = tk.StringVar(value="0.90")
        self.var_interval = tk.StringVar(value="1.0")
        self.var_button = tk.StringVar(value="left")
        self.var_double = tk.BooleanVar(value=False)
        self.var_grayscale = tk.BooleanVar(value=False)
        self.var_clicks = tk.StringVar(value="0")
        self.var_move = tk.StringVar(value="0.0")
        self.var_delay = tk.StringVar(value="2.0")
        self.var_region = tk.StringVar(value="Full screen")

        # Dashboard variables.
        self.var_clicks_done = tk.StringVar(value="0")
        self.var_misses = tk.StringVar(value="0")
        self.var_elapsed = tk.StringVar(value="00:00")
        self.var_last = tk.StringVar(value="—")

        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_queue)

    # ---- layout ---------------------------------------------------------

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=0)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)

        # Target row (spans both columns).
        target = ttk.LabelFrame(outer, text="Target", padding=10)
        target.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.thumb_label = tk.Label(
            target, text="no target", width=18, height=6,
            bg="#1e1e1e", fg="#8a8a8e", relief="groove",
        )
        self.thumb_label.grid(row=0, column=0, rowspan=2, padx=(0, 12))
        ttk.Button(target, text="📷  Capture target", command=self._capture_target).grid(
            row=0, column=1, sticky="ew", **pad
        )
        ttk.Button(target, text="📂  Load PNG…", command=self._load_png).grid(
            row=1, column=1, sticky="ew", **pad
        )
        self.var_target_info = tk.StringVar(value="Capture or load the button you want clicked.")
        ttk.Label(target, textvariable=self.var_target_info, foreground=COL_IDLE).grid(
            row=0, column=2, rowspan=2, sticky="w", padx=12
        )
        target.columnconfigure(2, weight=1)

        # Left: settings.
        settings = ttk.LabelFrame(outer, text="Settings", padding=10)
        settings.grid(row=1, column=0, sticky="nsew", padx=(0, 10))

        row = 0
        ttk.Label(settings, text="Confidence").grid(row=row, column=0, sticky="w", **pad)
        ttk.Scale(
            settings, from_=0.50, to=1.0, variable=self.var_conf,
            command=lambda _v: self.var_conf_text.set(f"{self.var_conf.get():.2f}"),
            length=140,
        ).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Label(settings, textvariable=self.var_conf_text, width=4).grid(row=row, column=2, **pad)

        def spin(label, var, frm, to, inc):
            nonlocal row
            row += 1
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky="w", **pad)
            ttk.Spinbox(
                settings, textvariable=var, from_=frm, to=to, increment=inc, width=8
            ).grid(row=row, column=1, columnspan=2, sticky="w", **pad)

        spin("Interval (s)", self.var_interval, 0.0, 3600, 0.1)
        spin("Click limit (0=∞)", self.var_clicks, 0, 100000, 1)
        spin("Move duration (s)", self.var_move, 0.0, 10, 0.1)
        spin("Start delay (s)", self.var_delay, 0.0, 60, 0.5)

        row += 1
        ttk.Label(settings, text="Mouse button").grid(row=row, column=0, sticky="w", **pad)
        ttk.Combobox(
            settings, textvariable=self.var_button, width=7, state="readonly",
            values=("left", "right", "middle"),
        ).grid(row=row, column=1, columnspan=2, sticky="w", **pad)

        row += 1
        ttk.Checkbutton(settings, text="Double-click", variable=self.var_double).grid(
            row=row, column=0, columnspan=3, sticky="w", **pad
        )
        row += 1
        ttk.Checkbutton(settings, text="Grayscale match", variable=self.var_grayscale).grid(
            row=row, column=0, columnspan=3, sticky="w", **pad
        )

        row += 1
        ttk.Label(settings, text="Search region").grid(row=row, column=0, sticky="w", **pad)
        ttk.Label(settings, textvariable=self.var_region, foreground=COL_BUSY).grid(
            row=row, column=1, columnspan=2, sticky="w", **pad
        )
        row += 1
        region_btns = ttk.Frame(settings)
        region_btns.grid(row=row, column=0, columnspan=3, sticky="w", **pad)
        ttk.Button(region_btns, text="🎯  Pick region", command=self._pick_region).pack(side="left")
        ttk.Button(region_btns, text="Clear", command=self._clear_region).pack(side="left", padx=6)

        # Right: dashboard + actions.
        right = ttk.Frame(outer)
        right.grid(row=1, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        dash = ttk.LabelFrame(right, text="Status", padding=10)
        dash.grid(row=0, column=0, sticky="ew")
        dash.columnconfigure(1, weight=1)
        dash.columnconfigure(3, weight=1)

        self.pill = tk.Label(
            dash, text="Idle", bg=COL_IDLE, fg="white", width=14,
            font=("Helvetica", 12, "bold"), pady=6,
        )
        self.pill.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 8))

        def stat(r, c, caption, var):
            cell = ttk.Frame(dash)
            cell.grid(row=r, column=c, sticky="w", padx=8, pady=2)
            ttk.Label(cell, text=caption, foreground=COL_IDLE).pack(anchor="w")
            ttk.Label(cell, textvariable=var, font=("Helvetica", 15, "bold")).pack(anchor="w")

        stat(1, 0, "Clicks", self.var_clicks_done)
        stat(1, 2, "Misses", self.var_misses)
        stat(2, 0, "Elapsed", self.var_elapsed)
        stat(2, 2, "Last match", self.var_last)

        act = ttk.Frame(right)
        act.grid(row=2, column=0, sticky="ew", pady=(8, 6))
        ttk.Button(act, text="🔍  Find once", command=self._find_once).pack(side="left")
        self.btn_start = ttk.Button(act, text="▶  Start", command=self._start)
        self.btn_start.pack(side="right")
        self.btn_stop = ttk.Button(act, text="■  Stop", command=self._stop, state="disabled")
        self.btn_stop.pack(side="right", padx=6)

        log_frame = ttk.LabelFrame(right, text="Log", padding=6)
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = ScrolledText(log_frame, height=8, state="disabled", wrap="word", font=("Menlo", 11))
        self.log.grid(row=0, column=0, sticky="nsew")

    # ---- helpers --------------------------------------------------------

    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_pill(self, text: str, color: str) -> None:
        self.pill.configure(text=text, bg=color)

    def _screen_scale(self) -> tuple[float, float]:
        if self._scale is None:
            self._scale = screen_scale(pyautogui.screenshot().size)
        return self._scale

    def _set_target(self, image: "str | Image.Image", info: str) -> None:
        pil = image if isinstance(image, Image.Image) else Image.open(image)
        self.needle = image
        self.needle_size = pil.size
        thumb = pil.copy()
        thumb.thumbnail((150, 130))
        self._thumb = ImageTk.PhotoImage(thumb)
        self.thumb_label.configure(image=self._thumb, text="", width=thumb.width, height=thumb.height)
        self.var_target_info.set(info)

    # ---- target / region ------------------------------------------------

    def _capture_target(self) -> None:
        self.root.withdraw()
        self.root.update()
        try:
            result = OverlaySelector(self.root, "Capture the button to click").select()
        except Exception as exc:  # e.g. Screen Recording permission missing
            self.root.deiconify()
            messagebox.showerror("Capture failed", str(exc))
            return
        self.root.deiconify()
        if result is None:
            return
        shot, crop_box, _region = result
        crop = shot.crop(crop_box)
        self._set_target(crop, f"Captured {crop.width}×{crop.height}px")
        self._log(f"Captured target ({crop.width}×{crop.height}px).")

    def _load_png(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose a reference image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self._set_target(path, path)
            self._log(f"Loaded target: {path}")
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))

    def _pick_region(self) -> None:
        self.root.withdraw()
        self.root.update()
        try:
            result = OverlaySelector(self.root, "Select the area to search").select()
        except Exception as exc:
            self.root.deiconify()
            messagebox.showerror("Region pick failed", str(exc))
            return
        self.root.deiconify()
        if result is None:
            return
        _shot, _crop, region = result
        self.region = region
        self.var_region.set(f"{region.left},{region.top}  {region.width}×{region.height}")
        self._log(f"Region set to {region.left},{region.top} {region.width}×{region.height}.")

    def _clear_region(self) -> None:
        self.region = None
        self.var_region.set("Full screen")

    # ---- spec building --------------------------------------------------

    def _build_spec(self, *, click_once: bool = False) -> ClickSpec | None:
        if self.needle is None:
            messagebox.showwarning("No target", "Capture or load a target image first.")
            return None
        try:
            interval = float(self.var_interval.get())
            clicks = int(float(self.var_clicks.get()))
            move = float(self.var_move.get())
            delay = float(self.var_delay.get())
        except ValueError:
            messagebox.showerror(
                "Invalid input",
                "Interval, click limit, move duration and start delay must be numbers.",
            )
            return None
        conf = float(self.var_conf.get())
        if interval < 0:
            messagebox.showerror("Invalid input", "Interval cannot be negative.")
            return None
        return ClickSpec(
            image=self.needle,
            interval=interval,
            confidence=conf,
            clicks=max(0, clicks),
            button=self.var_button.get(),
            double=self.var_double.get(),
            grayscale=self.var_grayscale.get(),
            region=self.region,
            move_duration=max(0.0, move),
            start_delay=max(0.0, delay),
            click_once=click_once,
        )

    # ---- find once ------------------------------------------------------

    def _find_once(self) -> None:
        spec = self._build_spec()
        if spec is None:
            return
        try:
            point = find_target(spec.image, spec.confidence, spec.grayscale, spec.region)
        except Exception as exc:
            messagebox.showerror("Find failed", str(exc))
            return
        if point is None:
            self._set_pill("Not found", COL_ERR)
            self.var_last.set("—")
            self._log("Find once: not found — try lowering confidence.")
            return
        self._set_pill("Found", COL_OK)
        self.var_last.set(f"{point[0]}, {point[1]}")
        self._log(f"Find once: matched at ({point[0]}, {point[1]}).")
        if self.needle_size:
            scale_x, scale_y = self._screen_scale()
            size = (self.needle_size[0] / scale_x, self.needle_size[1] / scale_y)
            FlashHighlight(self.root).show(point, size)

    # ---- run / stop -----------------------------------------------------

    def _start(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        spec = self._build_spec()
        if spec is None:
            return
        self.stop_event.clear()
        self._running = True
        self._terminal = False
        self._start_ts = time.monotonic()
        self.var_clicks_done.set("0")
        self.var_misses.set("0")
        self.var_elapsed.set("00:00")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._set_pill("Starting…", COL_BUSY)
        self._log("── started ──")
        self.worker = threading.Thread(target=self._worker, args=(spec,), daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        self.stop_event.set()
        self._set_pill("Stopping…", COL_IDLE)

    def _worker(self, spec: ClickSpec) -> None:
        try:
            run_clicker(spec, self.stop_event, on_event=self.queue.put)
        except FileNotFoundError:
            self.queue.put(ClickerEvent("error", message=f"Reference image not found: {spec.image}"))
        except ValueError as exc:
            self.queue.put(
                ClickerEvent("error", message=f"Matching failed ({exc}). Is OpenCV installed?")
            )
        except Exception as exc:
            self.queue.put(ClickerEvent("error", message=f"{type(exc).__name__}: {exc}"))

    # ---- event pump -----------------------------------------------------

    def _drain_queue(self) -> None:
        try:
            while True:
                self._handle_event(self.queue.get_nowait())
        except queue.Empty:
            pass
        if self._running and self._start_ts is not None:
            self.var_elapsed.set(self._fmt_elapsed(time.monotonic() - self._start_ts))
        self.root.after(100, self._drain_queue)

    def _handle_event(self, event: ClickerEvent) -> None:
        kind = event.kind
        if kind == "searching":
            self._set_pill("Searching…", COL_BUSY)
        elif kind == "miss":
            self.var_misses.set(str(event.misses))
        elif kind == "click":
            self.var_clicks_done.set(str(event.clicks))
            self.var_misses.set("0")
            if event.point:
                self.var_last.set(f"{event.point[0]}, {event.point[1]}")
            self._set_pill("Clicked", COL_OK)
            self._log(f"✓ clicked {event.point} — total {event.clicks}")
        elif kind == "limit":
            self._finish("Done", COL_OK)
            self._log(f"Reached click limit ({event.clicks}).")
        elif kind == "failsafe":
            self._finish("Fail-safe", COL_ERR)
            self._log("Fail-safe triggered (mouse hit a corner).")
        elif kind == "error":
            self._finish("Error", COL_ERR)
            self._log(f"⚠ {event.message}")
        elif kind == "stopped":
            self._running = False
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            if not self._terminal:
                self._set_pill("Idle", COL_IDLE)
                self._log("── stopped ──")

    def _finish(self, text: str, color: str) -> None:
        self._terminal = True
        self._running = False
        self._set_pill(text, color)
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    @staticmethod
    def _fmt_elapsed(seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"

    def _on_close(self) -> None:
        self.stop_event.set()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    ClickerGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
