# macOS Image-Search Clicker

A small Python script that clicks a button or icon on your screen by **matching a
picture of it**, not a fixed coordinate. It keeps working when the window moves.

You save a tight screenshot of the target as `target.png`. The script scans the screen on
an interval, finds that image, and clicks its centre.

Built with [PyAutoGUI](https://github.com/asweigart/pyautogui) and OpenCV. Developed and
tested on macOS (Python 3.14, Retina display). It has not been run on Linux or Windows.

## How it works

1. Each cycle it takes a screenshot of the screen, or of the `--region` you give it.
2. It looks for `target.png` in that screenshot with OpenCV template matching. A match
   needs a similarity of at least `--confidence` (default 0.9).
3. On a match it clicks the centre of the match. On a Retina display the screenshot has
   more pixels than the screen has points, so the script divides by that ratio before
   clicking.
4. It waits `--interval` seconds and repeats.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Grant macOS permissions

Without these the script appears to run but does nothing. Give your terminal app
(Terminal, iTerm, or the app that launches Python) access under System Settings, Privacy &
Security:

- **Accessibility**, so it can move and click the mouse.
- **Screen Recording**, so it can see the screen.

Then quit and reopen the terminal.

## Capture the target

Press **⌘⇧4**, drag a tight box around the button, and save the crop as `target.png` in
this folder. Include the button and little background: too much background makes matching
unreliable, too little makes it ambiguous. Capture it on the same display you will run on,
because a crop from a 1x monitor will not match a Retina screen.

## Run

It clicks until you stop it. The default is no click limit.

```bash
# Look for target.png once a second and click it, until stopped
python macro_clicker.py

# Another image, scanning every 2 seconds
python macro_clicker.py --image target.png --interval 2

# Click 10 times, then stop
python macro_clicker.py --clicks 10

# Find it once, click once, exit
python macro_clicker.py --click-once

# More forgiving matching (lower it if the button is not found)
python macro_clicker.py --confidence 0.8

# Search only part of the screen, in points: x,y,width,height (faster)
python macro_clicker.py --region 800,400,400,300

# Right double-click, gliding the cursor over 0.2 s
python macro_clicker.py --button right --double --move-duration 0.2
```

### Options

| Flag | Default | Meaning |
|------|---------|---------|
| `--image`, `-i` | `target.png` | Image to find and click. |
| `--interval`, `-t` | `1.0` | Seconds between scans. |
| `--confidence`, `-c` | `0.9` | Match threshold, above 0 and up to 1. Lower is more forgiving. |
| `--clicks`, `-n` | `0` | Stop after N clicks. 0 means run until stopped. |
| `--click-once` | off | Click once, then exit. Overrides `--clicks`. |
| `--button` | `left` | `left`, `right` or `middle`. |
| `--double` | off | Double-click instead of single-click. |
| `--grayscale` | off | Match in grayscale. Slightly faster, slightly less precise. |
| `--region` | whole screen | `X,Y,W,H` search box in logical points. |
| `--move-duration` | `0.0` | Seconds to glide the cursor to the target. 0 is instant. |
| `--start-delay` | `2.0` | Seconds to wait before starting, so you can switch windows. |

`python macro_clicker.py --help` prints the same list.

## Stopping it

- Press **Ctrl+C** in the terminal, or
- move the mouse into any **corner of the screen**. PyAutoGUI's fail-safe stops the script
  at once.

## Troubleshooting

- **"not found" on every cycle.** Lower `--confidence` to 0.8, re-crop the image more
  tightly, and check that the button is visible and not covered.
- **Clicks land in the wrong place.** Usually a display-scale mismatch. Capture
  `target.png` on the same display you run on.
- **Nothing happens, or a permission error.** Recheck the Accessibility and Screen
  Recording permissions above and restart the terminal.
- **An error mentioning `confidence` or OpenCV.** Run `pip install -r requirements.txt`
  again inside the virtual environment.
- **A size error.** `target.png` is larger than the area being searched. Use a smaller
  image or a larger `--region`.

## Use it responsibly

Automated clicking can break the terms of some websites, games and services. Use it on
your own machine and your own tasks.

## Alternatives

- [SikuliX](https://sikulix.github.io/) is free, built around image matching, and needs
  Java.
- [Keyboard Maestro](https://www.keyboardmaestro.com/) is a paid Mac app with a "found
  image" condition and click action.

## Licence

[MIT](LICENSE)
