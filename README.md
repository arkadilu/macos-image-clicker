# macOS Image-Search Macro Clicker

Click a button or icon on screen automatically, by **matching a picture of it**
rather than fixed coordinates — so it keeps working even when windows move.

This repo gives you two paths:

1. **No-code apps** (recommended if you don't want to touch a terminal) — see below.
2. **A ready-to-run Python script** (`macro_clicker.py`) built on PyAutoGUI + OpenCV.

---

## 1. No-code / low-code options (my recommendation first)

You said you'd prefer an existing no-code tool. On macOS, image-based clicking is
a fairly niche feature — most "auto clickers" only click fixed coordinates and
**cannot find an image**. These three actually do visual detection:

| Tool | Cost | Notes |
|------|------|-------|
| **[SikuliX](https://sikulix.github.io/)** | **Free** | Purpose-built for exactly this. You screenshot the button in its IDE and drag it into a visual script; it clicks wherever that image appears. Closest thing to no-code that's free. Needs Java. **Best free pick.** |
| **[Keyboard Maestro](https://www.keyboardmaestro.com/)** | ~$36 (paid) | The polished, native-Mac option. Has a "Found Image" condition and a "Move and Click at Found Image" action. Most reliable, best UI, great scheduling/hotkey triggers. **Best overall if you don't mind paying.** |
| **[Automator](https://support.apple.com/guide/automator/welcome/mac) / Shortcuts** | Free (built-in) | ⚠️ Cannot search for an image on screen. Only useful for fixed-position clicks. Listed so you know *not* to reach for it here. |

**Short version:** if you want free and visual, use **SikuliX**. If you want the
smoothest native experience and don't mind ~$36, use **Keyboard Maestro**. Use
the script in this repo if you'd rather have something scriptable, versionable,
and dependency-light.

---

## 2. The script (`macro_clicker.py`)

### Setup

```bash
# From this folder:
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### macOS permissions (required — the script is silent/broken without these)

Grant your terminal app (e.g. **Terminal**, **iTerm**, or your Python launcher):

- **System Settings → Privacy & Security → Accessibility** — lets it move/click the mouse.
- **System Settings → Privacy & Security → Screen Recording** — lets it see the screen to find your image.

After toggling these on, fully quit and reopen the terminal.

### Capture your target image

Take a **small, distinct** crop of the exact button/icon you want to click and
save it as `target.png` in this folder.

- On macOS: press **⌘⇧4**, drag a tight box around the button, and it saves to
  your Desktop — then move/rename it to `target.png` here.
- Keep it tight: include the button but little surrounding background. Too much
  background makes matching flaky; too little makes it ambiguous.

> **Retina tip:** the script auto-corrects for Retina/HiDPI 2x scaling, so clicks
> land in the right spot. Just make sure the crop and the live screen are at the
> same display scale (don't grab the image on an external 1x monitor and run on
> the built-in Retina one).

### Run

```bash
# Simplest: look for target.png, click it once per second, forever
python macro_clicker.py

# Explicit image, scan every 2s
python macro_clicker.py --image target.png --interval 2

# Click it exactly 10 times then stop
python macro_clicker.py --clicks 10

# Find it once and click a single time, then exit
python macro_clicker.py --click-once

# More tolerant matching (0-1); lower if it isn't finding the button
python macro_clicker.py --confidence 0.8

# Only search a region of the screen (logical points: x,y,width,height) — faster
python macro_clicker.py --region 800,400,400,300

# Right double-click, gliding the cursor over 0.2s (looks more human)
python macro_clicker.py --button right --double --move-duration 0.2
```

### All options

| Flag | Default | Meaning |
|------|---------|---------|
| `--image, -i` | `target.png` | Reference image to find and click. |
| `--interval, -t` | `1.0` | Seconds between scan/click cycles. |
| `--confidence, -c` | `0.9` | Match threshold 0–1 (needs OpenCV). Lower = more forgiving. |
| `--clicks, -n` | `0` | Stop after N clicks (0 = unlimited). |
| `--click-once` | off | Click a single time, then exit. |
| `--button` | `left` | `left`, `right`, or `middle`. |
| `--double` | off | Double-click instead of single. |
| `--grayscale` | off | Match in grayscale (slightly faster). |
| `--region` | whole screen | `X,Y,W,H` search box in logical points. |
| `--move-duration` | `0.0` | Seconds to glide cursor to target (0 = instant). |
| `--start-delay` | `2.0` | Grace period before starting, to switch windows. |

### Stopping it

- Press **Ctrl+C** in the terminal, **or**
- Slam the mouse into any **screen corner** — PyAutoGUI's fail-safe aborts instantly.

### Troubleshooting

- **"not found (miss)" every cycle** — lower `--confidence` (try `0.8`), re-crop
  the image more tightly, or confirm the button is actually visible on screen.
- **Clicks land in the wrong place** — usually a Retina scale mismatch; make sure
  the crop was taken on the same display you're running against.
- **Nothing happens at all / permission errors** — re-check the Accessibility and
  Screen Recording permissions above and restart the terminal.
- **`confidence` error about OpenCV** — run `pip install -r requirements.txt`
  again; `opencv-python` is what enables fuzzy matching.

---

## Which should I use?

- **Just want it to work, no terminal:** SikuliX (free) or Keyboard Maestro (paid).
- **Want it scriptable, versioned, and lightweight:** the script here.

Both approaches do the same core thing — screenshot the button, let the tool find
it on screen, and click it.
