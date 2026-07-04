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
import time
from dataclasses import dataclass

try:
    import pyautogui
except ImportError:
    sys.exit(
        "PyAutoGUI is not installed. Run:  pip install -r requirements.txt"
    )


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
    needle: str,
    confidence: float,
    grayscale: bool,
    region: Region | None,
) -> tuple[int, int] | None:
    """Return the click point in logical points, or None if not found."""
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
    except FileNotFoundError:
        sys.exit(f"Reference image not found: {needle}")
    except ValueError as exc:
        # confidence= requires OpenCV; surface a clear hint.
        sys.exit(
            f"Image matching failed ({exc}). Is OpenCV installed? "
            "Run:  pip install -r requirements.txt"
        )

    if box is None:
        return None

    center = pyautogui.center(box)
    logical_x = (center.x + offset_x) / scale_x
    logical_y = (center.y + offset_y) / scale_y
    return int(logical_x), int(logical_y)


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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not (0.0 < args.confidence <= 1.0):
        sys.exit("--confidence must be between 0 and 1.")
    if args.interval < 0:
        sys.exit("--interval cannot be negative.")

    print(f"Target image : {args.image}")
    print(f"Confidence   : {args.confidence}")
    print(f"Interval     : {args.interval}s")
    print(
        "Click limit  : "
        + ("1 (click-once)" if args.click_once else (str(args.clicks) if args.clicks else "unlimited"))
    )
    if args.region:
        r = args.region
        print(f"Search region: {r.left},{r.top} {r.width}x{r.height}")
    print("Move mouse to a screen corner or press Ctrl+C to stop.\n")

    if args.start_delay > 0:
        print(f"Starting in {args.start_delay:.0f}s — switch to the target window...")
        time.sleep(args.start_delay)

    clicks = 0
    misses = 0
    try:
        while True:
            point = find_target(
                args.image, args.confidence, args.grayscale, args.region
            )
            if point is None:
                misses += 1
                print(f"  · not found (miss #{misses})", flush=True)
            else:
                x, y = point
                if args.move_duration > 0:
                    pyautogui.moveTo(x, y, duration=args.move_duration)
                pyautogui.click(
                    x,
                    y,
                    clicks=2 if args.double else 1,
                    interval=0.05,
                    button=args.button,
                )
                clicks += 1
                misses = 0
                print(f"  ✓ clicked ({x}, {y}) — total {clicks}", flush=True)

                if args.click_once:
                    break
                if args.clicks and clicks >= args.clicks:
                    print(f"\nReached click limit ({args.clicks}). Done.")
                    break

            time.sleep(args.interval)
    except pyautogui.FailSafeException:
        print("\nFail-safe triggered (mouse hit a corner). Stopping.")
    except KeyboardInterrupt:
        print("\nInterrupted. Stopping.")

    print(f"Total clicks: {clicks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
