#!/usr/bin/env python3
"""
macro_clicker.py — Image-search macro clicker for macOS (and Linux/Windows).

Watches the screen for a reference image (a small crop of the button/icon you
want to click) and clicks it on an interval. Built on PyAutoGUI + OpenCV.

Quick start:
    1. Screenshot the exact button/icon and save the crop as `target.png`
       (Cmd+Shift+4 on macOS to grab a region).
    2. pip install -r requirements.txt
    3. python macro_clicker.py --image target.png --interval 2

Grant Terminal (or your Python launcher) permission under:
    System Settings -> Privacy & Security -> Accessibility   (to move/click)
    System Settings -> Privacy & Security -> Screen Recording (to see the screen)

Stop any time with Ctrl+C, or slam the mouse into a screen corner (fail-safe).
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

try:
    import pyautogui
except ImportError:
    sys.exit(
        "PyAutoGUI is not installed. Run:  pip install -r requirements.txt"
    )

if TYPE_CHECKING:
    from PIL import Image


# PyAutoGUI fail-safe: shoving the cursor into a screen corner aborts the run.
pyautogui.FAILSAFE = True


@dataclass
class Region:
    """A search region in logical screen points (x, y, width, height)."""

    left: int
    top: int
    width: int
    height: int


def parse_region(value: str) -> Region:
    try:
        parts = [int(p.strip()) for p in value.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(
            "region must be four integers: x,y,width,height"
        )
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "region must be four integers: x,y,width,height"
        )
    if parts[2] <= 0 or parts[3] <= 0:
        raise argparse.ArgumentTypeError("region width and height must be > 0")
    return Region(*parts)


def screen_scale(haystack_size: tuple[int, int]) -> tuple[float, float]:
    """Ratio of screenshot pixels to logical points.

    On Retina/HiDPI displays PyAutoGUI captures at 2x (or more), but the mouse
    is driven in logical points. Dividing located pixel coordinates by this
    scale keeps clicks landing where you expect.
    """
    logical_w, logical_h = pyautogui.size()
    shot_w, shot_h = haystack_size
    scale_x = shot_w / logical_w if logical_w else 1.0
    scale_y = shot_h / logical_h if logical_h else 1.0
    return scale_x, scale_y


def find_target(
    needle: str | Image.Image,
    confidence: float,
    grayscale: bool,
    region: Region | None,
) -> tuple[int, int] | None:
    """Return the click point in logical points, or None if not found.

    `needle` may be a path or an in-memory PIL image (so a GUI can match a
    freshly captured crop without saving it). A missing path raises
    FileNotFoundError and an unavailable confidence match (no OpenCV) raises
    ValueError — callers decide how to surface those.
    """
    haystack = pyautogui.screenshot()
    scale_x, scale_y = screen_scale(haystack.size)

    crop = haystack
    offset_x = offset_y = 0
    if region is not None:
        # Region is given in logical points; translate to screenshot pixels.
        px_left = int(region.left * scale_x)
        px_top = int(region.top * scale_y)
        px_right = int((region.left + region.width) * scale_x)
        px_bottom = int((region.top + region.height) * scale_y)
        crop = haystack.crop((px_left, px_top, px_right, px_bottom))
        offset_x, offset_y = px_left, px_top

    try:
        box = pyautogui.locate(
            needle, crop, confidence=confidence, grayscale=grayscale
        )
    except pyautogui.ImageNotFoundException:
        return None

    if box is None:
        return None

    center = pyautogui.center(box)
    logical_x = (center.x + offset_x) / scale_x
    logical_y = (center.y + offset_y) / scale_y
    return int(logical_x), int(logical_y)


@dataclass
class ClickSpec:
    """Everything run_clicker needs to run — mirrors the CLI flags."""

    image: str | Image.Image = "target.png"
    interval: float = 1.0
    confidence: float = 0.9
    clicks: int = 0  # 0 means run forever
    button: str = "left"
    double: bool = False
    grayscale: bool = False
    region: Region | None = None
    move_duration: float = 0.0
    start_delay: float = 2.0
    click_once: bool = False


@dataclass
class ClickerEvent:
    """A progress update emitted by run_clicker as it scans and clicks."""

    kind: str  # start|searching|miss|click|limit|failsafe|interrupted|stopped
    clicks: int = 0
    misses: int = 0
    point: tuple[int, int] | None = None
    message: str = ""


def perform_click(
    point: tuple[int, int],
    *,
    button: str = "left",
    double: bool = False,
    move_duration: float = 0.0,
) -> None:
    """Move to `point` (optionally gliding) and click it."""
    x, y = point
    if move_duration > 0:
        pyautogui.moveTo(x, y, duration=move_duration)
    pyautogui.click(
        x, y, clicks=2 if double else 1, interval=0.05, button=button
    )


def run_clicker(
    spec: ClickSpec,
    stop_event: threading.Event | None = None,
    on_event: Callable[[ClickerEvent], None] | None = None,
) -> int:
    """Scan for `spec.image` and click it until stopped, capped, or fail-safed.

    Returns the total clicks made. Waits are interruptible via `stop_event`, so
    Stop responds within one poll even at a long interval. FailSafeException and
    KeyboardInterrupt are caught and reported as events (so the CLI keeps its
    graceful-stop messages and a GUI worker never dies mid-run); other errors —
    a missing image, no OpenCV, a screenshot failure — propagate for the caller
    to handle.
    """
    emit = on_event or (lambda event: None)

    def stopping() -> bool:
        return stop_event is not None and stop_event.is_set()

    def wait(seconds: float) -> bool:
        """Sleep, returning True if a stop was requested during the wait."""
        if stop_event is not None:
            return stop_event.wait(seconds)
        time.sleep(seconds)
        return False

    emit(ClickerEvent("start"))
    if spec.start_delay > 0 and wait(spec.start_delay):
        emit(ClickerEvent("stopped"))
        return 0

    clicks = 0
    misses = 0
    try:
        while not stopping():
            emit(ClickerEvent("searching", clicks=clicks, misses=misses))
            point = find_target(
                spec.image, spec.confidence, spec.grayscale, spec.region
            )
            if point is None:
                misses += 1
                emit(ClickerEvent("miss", clicks=clicks, misses=misses))
            else:
                perform_click(
                    point,
                    button=spec.button,
                    double=spec.double,
                    move_duration=spec.move_duration,
                )
                clicks += 1
                misses = 0
                emit(
                    ClickerEvent(
                        "click", clicks=clicks, misses=misses, point=point
                    )
                )
                if spec.click_once:
                    break
                if spec.clicks and clicks >= spec.clicks:
                    emit(ClickerEvent("limit", clicks=clicks))
                    break
            if wait(spec.interval):
                break
    except pyautogui.FailSafeException:
        emit(ClickerEvent("failsafe", clicks=clicks))
    except KeyboardInterrupt:
        emit(ClickerEvent("interrupted", clicks=clicks))

    emit(ClickerEvent("stopped", clicks=clicks))
    return clicks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Click an on-screen image on a repeating interval.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--image",
        "-i",
        default="target.png",
        help="Path to the reference image to search for and click.",
    )
    parser.add_argument(
        "--interval",
        "-t",
        type=float,
        default=1.0,
        help="Seconds to wait between scan/click cycles.",
    )
    parser.add_argument(
        "--confidence",
        "-c",
        type=float,
        default=0.9,
        help="Match confidence 0-1 (needs OpenCV). Lower = more tolerant.",
    )
    parser.add_argument(
        "--clicks",
        "-n",
        type=int,
        default=0,
        help="Stop after N successful clicks. 0 means run forever.",
    )
    parser.add_argument(
        "--button",
        choices=("left", "right", "middle"),
        default="left",
        help="Which mouse button to click.",
    )
    parser.add_argument(
        "--double",
        action="store_true",
        help="Double-click instead of single-click.",
    )
    parser.add_argument(
        "--grayscale",
        action="store_true",
        help="Match in grayscale (a little faster, slightly less precise).",
    )
    parser.add_argument(
        "--region",
        type=parse_region,
        default=None,
        metavar="X,Y,W,H",
        help="Limit the search to a screen region (logical points).",
    )
    parser.add_argument(
        "--move-duration",
        type=float,
        default=0.0,
        help="Seconds to glide the cursor to the target (0 = instant).",
    )
    parser.add_argument(
        "--start-delay",
        type=float,
        default=2.0,
        help="Seconds to wait before starting (switch to the target window).",
    )
    parser.add_argument(
        "--click-once",
        action="store_true",
        help="Find and click a single time, then exit (ignores --clicks).",
    )
    return parser


def _print_event(event: ClickerEvent) -> None:
    """Render a ClickerEvent as the CLI's original console lines."""
    if event.kind == "miss":
        print(f"  · not found (miss #{event.misses})", flush=True)
    elif event.kind == "click":
        x, y = event.point
        print(f"  ✓ clicked ({x}, {y}) — total {event.clicks}", flush=True)
    elif event.kind == "limit":
        print(f"\nReached click limit ({event.clicks}). Done.")
    elif event.kind == "failsafe":
        print("\nFail-safe triggered (mouse hit a corner). Stopping.")
    elif event.kind == "interrupted":
        print("\nInterrupted. Stopping.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not (0.0 < args.confidence <= 1.0):
        sys.exit("--confidence must be between 0 and 1.")
    if args.interval < 0:
        sys.exit("--interval cannot be negative.")

    spec = ClickSpec(
        image=args.image,
        interval=args.interval,
        confidence=args.confidence,
        clicks=args.clicks,
        button=args.button,
        double=args.double,
        grayscale=args.grayscale,
        region=args.region,
        move_duration=args.move_duration,
        start_delay=args.start_delay,
        click_once=args.click_once,
    )

    print(f"Target image : {spec.image}")
    print(f"Confidence   : {spec.confidence}")
    print(f"Interval     : {spec.interval}s")
    print(
        "Click limit  : "
        + ("1 (click-once)" if spec.click_once else (str(spec.clicks) if spec.clicks else "unlimited"))
    )
    if spec.region:
        r = spec.region
        print(f"Search region: {r.left},{r.top} {r.width}x{r.height}")
    print("Move mouse to a screen corner or press Ctrl+C to stop.\n")

    if spec.start_delay > 0:
        print(f"Starting in {spec.start_delay:.0f}s — switch to the target window...")

    try:
        total = run_clicker(spec, on_event=_print_event)
    except FileNotFoundError:
        sys.exit(f"Reference image not found: {spec.image}")
    except ValueError as exc:
        # confidence= requires OpenCV; surface a clear hint.
        sys.exit(
            f"Image matching failed ({exc}). Is OpenCV installed? "
            "Run:  pip install -r requirements.txt"
        )

    print(f"Total clicks: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
