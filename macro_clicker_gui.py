#!/usr/bin/env python3
"""
macro_clicker_gui.py — an HTML/CSS desktop GUI (pywebview) for the macro clicker.

It wraps the same engine the CLI uses (`macro_clicker.run_clicker`) in a native
window rendered with macOS's built-in WebView, and adds:

    • Capture target — drag a box over a frozen full-screen shot to grab the
      button (retina-correct), no Cmd+Shift+4 / save-to-Desktop dance.
    • Pick region   — same drag gesture to limit the search area.
    • Find once     — locate the target and flash a highlight where it matched,
      to tune the confidence slider before running.
    • Live dashboard — status pill, click/miss counters, elapsed time, last
      match, and a log — driven by a worker thread that pushes events into the
      page via evaluate_js, so the UI never freezes.

Run:  python macro_clicker_gui.py
Needs the same macOS permissions as the CLI (Accessibility + Screen Recording)
and works on the main display only.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import threading
import time

try:
    import webview
except ImportError:
    sys.exit("pywebview is not installed. Run:  pip install -r requirements.txt")

try:
    import pyautogui
    from PIL import Image
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

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "web", "index.html")

# The FileDialog constant moved namespaces in newer pywebview.
try:
    _OPEN_DIALOG = webview.FileDialog.OPEN
except AttributeError:  # pragma: no cover - older pywebview
    _OPEN_DIALOG = webview.OPEN_DIALOG


def _data_uri(img: Image.Image) -> str:
    """Encode a PIL image as a base64 PNG data URI for the webview."""
    buf = io.BytesIO()
    (img if img.mode in ("RGB", "RGBA", "L") else img.convert("RGBA")).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def rect_to_crop_and_region(shot_size: tuple[int, int], rect: dict) -> tuple | None:
    """Map a fractional selection (0..1 of the screenshot) to a crop box and Region.

    `rect` has x0,y0,x1,y1 as fractions of the displayed screenshot (DPR-free).
    Returns (crop_box_px, region_logical) or None if the selection is degenerate.
    """
    shot_w, shot_h = shot_size
    px0 = max(0, min(shot_w, round(rect["x0"] * shot_w)))
    py0 = max(0, min(shot_h, round(rect["y0"] * shot_h)))
    px1 = max(0, min(shot_w, round(rect["x1"] * shot_w)))
    py1 = max(0, min(shot_h, round(rect["y1"] * shot_h)))
    if px1 - px0 < 1 or py1 - py0 < 1:
        return None
    scale_x, scale_y = screen_scale(shot_size)
    region = Region(
        int(px0 / scale_x),
        int(py0 / scale_y),
        int((px1 - px0) / scale_x),
        int((py1 - py0) / scale_y),
    )
    return (px0, py0, px1, py1), region


OVERLAY_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
  * { margin:0; box-sizing:border-box; }
  html,body { width:100%; height:100%; overflow:hidden; background:#000;
              cursor:crosshair; user-select:none; -webkit-user-select:none; }
  #shot { position:fixed; inset:0; width:100vw; height:100vh; display:block; }
  #veil { position:fixed; inset:0; background:rgba(0,0,0,.5); }
  #sel  { position:fixed; display:none; border:2px solid #4da3ff;
          box-shadow:0 0 0 100vmax rgba(0,0,0,.5); }
  #hint { position:fixed; top:24px; left:50%; transform:translateX(-50%);
          font:600 15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
          color:#fff; background:rgba(18,20,24,.85); padding:9px 18px;
          border-radius:999px; border:1px solid rgba(255,255,255,.15);
          white-space:nowrap; }
</style></head><body>
  <img id="shot" src="__SHOT__" draggable="false">
  <div id="veil"></div>
  <div id="sel"></div>
  <div id="hint">__HINT__ &nbsp;·&nbsp; drag to select &nbsp;·&nbsp; Esc to cancel</div>
<script>
  const sel = document.getElementById('sel'), veil = document.getElementById('veil');
  let ax = 0, ay = 0, dragging = false;
  function finish(rect) { try { window.pywebview.api.finish_overlay(rect); } catch (e) {} }
  function draw(x, y) {
    const l = Math.min(ax, x), t = Math.min(ay, y);
    sel.style.left = l + 'px'; sel.style.top = t + 'px';
    sel.style.width = Math.abs(x - ax) + 'px'; sel.style.height = Math.abs(y - ay) + 'px';
  }
  addEventListener('mousedown', e => {
    dragging = true; ax = e.clientX; ay = e.clientY;
    veil.style.display = 'none'; sel.style.display = 'block'; draw(ax, ay);
  });
  addEventListener('mousemove', e => { if (dragging) draw(e.clientX, e.clientY); });
  addEventListener('mouseup', e => {
    if (!dragging) return;
    dragging = false;
    const x0 = Math.min(ax, e.clientX), y0 = Math.min(ay, e.clientY);
    const x1 = Math.max(ax, e.clientX), y1 = Math.max(ay, e.clientY);
    if (x1 - x0 < 4 || y1 - y0 < 4) { finish(null); return; }
    const w = innerWidth, h = innerHeight;   // shot fills the viewport 1:1 in aspect
    finish({ x0: x0 / w, y0: y0 / h, x1: x1 / w, y1: y1 / h });
  });
  addEventListener('keydown', e => { if (e.key === 'Escape') finish(null); });
</script></body></html>"""

FLASH_HTML = (
    "<body style='margin:0;background:transparent;overflow:hidden'>"
    "<div style='position:fixed;inset:0;border:3px solid #34c759;"
    "background:rgba(52,199,89,.28);border-radius:6px'></div></body>"
)


class Api:
    """The bridge exposed to the page as `window.pywebview.api`."""

    def __init__(self) -> None:
        self.window = None
        self.overlay = None
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.needle = None            # str path or in-memory PIL image
        self.needle_size = None       # (w, h) pixels
        self.region: Region | None = None
        self._shot = None             # frozen screenshot during an overlay
        self._mode = None             # 'capture' | 'region'
        self._scale = None

    # ---- helpers --------------------------------------------------------

    def _push(self, fn: str, payload) -> None:
        try:
            self.window.evaluate_js(f"window.{fn}({json.dumps(payload)})")
        except Exception:
            pass

    def _emit(self, event: ClickerEvent) -> None:
        self._push("onClickerEvent", {
            "kind": event.kind,
            "clicks": event.clicks,
            "misses": event.misses,
            "point": list(event.point) if event.point else None,
            "message": event.message,
        })

    def _screen_scale(self):
        if self._scale is None:
            self._scale = screen_scale(pyautogui.screenshot().size)
        return self._scale

    def _spec_from(self, d: dict, *, click_once: bool = False) -> ClickSpec:
        return ClickSpec(
            image=self.needle,
            interval=max(0.0, float(d.get("interval", 1.0))),
            confidence=float(d.get("confidence", 0.9)),
            clicks=max(0, int(d.get("clicks", 0))),
            button=d.get("button", "left"),
            double=bool(d.get("double", False)),
            grayscale=bool(d.get("grayscale", False)),
            region=self.region,
            move_duration=max(0.0, float(d.get("move_duration", 0.0))),
            start_delay=max(0.0, float(d.get("start_delay", 2.0))),
            click_once=click_once,
        )

    # ---- capture / region overlay --------------------------------------

    def _begin_overlay(self, mode: str, hint: str) -> dict:
        try:
            self.window.hide()
            time.sleep(0.18)  # let the app clear the screen before the shot
            shot = pyautogui.screenshot()
        except Exception as exc:
            self.window.show()
            return {"ok": False, "error": str(exc)}
        self._shot = shot
        self._mode = mode
        html = OVERLAY_HTML.replace("__SHOT__", _data_uri(shot)).replace("__HINT__", hint)
        # fullscreen so the shot fills the viewport 1:1 in aspect (no letterbox math)
        self.overlay = webview.create_window(
            "overlay", html=html, js_api=self,
            fullscreen=True, frameless=True, on_top=True, background_color="#000000",
        )
        return {"ok": True}

    def begin_capture(self) -> dict:
        return self._begin_overlay("capture", "Capture the button to click")

    def begin_pick_region(self) -> dict:
        return self._begin_overlay("region", "Select the area to search")

    def finish_overlay(self, rect: dict | None) -> dict:
        shot, mode = self._shot, self._mode
        if self.overlay is not None:
            self._safe_destroy(self.overlay)
            self.overlay = None
        self.window.show()
        if rect is None or shot is None:
            return {"ok": False}
        mapped = rect_to_crop_and_region(shot.size, rect)
        if mapped is None:
            return {"ok": False}
        crop_box, region = mapped
        if mode == "capture":
            crop = shot.crop(crop_box)
            self.needle = crop
            self.needle_size = crop.size
            self._push("onTargetCaptured", {
                "uri": _data_uri(crop), "info": f"Captured {crop.width}×{crop.height}px",
            })
        else:
            self.region = region
            self._push("onRegionPicked", {
                "text": f"{region.left},{region.top}  {region.width}×{region.height}",
            })
        return {"ok": True}

    def clear_region(self) -> dict:
        self.region = None
        return {"ok": True}

    def load_png(self) -> dict:
        try:
            result = self.window.create_file_dialog(
                _OPEN_DIALOG,
                file_types=("Images (*.png;*.jpg;*.jpeg;*.bmp)", "All files (*.*)"),
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not result:
            return {"ok": False}
        path = result[0]
        try:
            img = Image.open(path)
            img.load()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        self.needle = path
        self.needle_size = img.size
        return {"ok": True, "uri": _data_uri(img), "info": path}

    # ---- find once ------------------------------------------------------

    def find_once(self, d: dict) -> dict:
        if self.needle is None:
            return {"ok": False, "error": "No target set."}
        spec = self._spec_from(d)
        try:
            point = find_target(spec.image, spec.confidence, spec.grayscale, spec.region)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if point is None:
            return {"ok": True, "found": False}
        if self.needle_size:
            try:
                scale_x, scale_y = self._screen_scale()
                self._flash(point, (self.needle_size[0] / scale_x, self.needle_size[1] / scale_y))
            except Exception:
                pass
        return {"ok": True, "found": True, "point": list(point)}

    def _flash(self, center: tuple[int, int], size: tuple[float, float]) -> None:
        w = max(int(size[0]), 8)
        h = max(int(size[1]), 8)
        win = webview.create_window(
            "flash", html=FLASH_HTML,
            x=int(center[0] - w / 2), y=int(center[1] - h / 2), width=w, height=h,
            frameless=True, on_top=True, transparent=True, focus=False,
        )
        threading.Timer(0.7, lambda: self._safe_destroy(win)).start()

    @staticmethod
    def _safe_destroy(win) -> None:
        try:
            win.destroy()
        except Exception:
            pass

    # ---- run / stop -----------------------------------------------------

    def start(self, d: dict) -> dict:
        if self.needle is None:
            return {"ok": False, "error": "No target set."}
        if self.worker is not None and self.worker.is_alive():
            return {"ok": False, "error": "Already running."}
        spec = self._spec_from(d)
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._run, args=(spec,), daemon=True)
        self.worker.start()
        return {"ok": True}

    def _run(self, spec: ClickSpec) -> None:
        try:
            run_clicker(spec, self.stop_event, on_event=self._emit)
        except FileNotFoundError:
            self._emit(ClickerEvent("error", message=f"Reference image not found: {spec.image}"))
        except ValueError as exc:
            self._emit(ClickerEvent("error", message=f"Matching failed ({exc}). Is OpenCV installed?"))
        except Exception as exc:
            self._emit(ClickerEvent("error", message=f"{type(exc).__name__}: {exc}"))

    def stop(self) -> dict:
        self.stop_event.set()
        return {"ok": True}


def main() -> int:
    api = Api()
    window = webview.create_window(
        "Macro Clicker", url=INDEX_HTML, js_api=api,
        width=900, height=680, min_size=(780, 600), background_color="#0f1115",
    )
    api.window = window
    window.events.closed += api.stop_event.set
    webview.start(debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
