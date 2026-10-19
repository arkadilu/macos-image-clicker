"""Check find_target's exit messages. Run: python test_find_target.py (or pytest)."""

from PIL import Image

import macro_clicker
from macro_clicker import pyautogui


def exit_message(exc: Exception) -> str:
    def locate(*args, **kwargs):
        raise exc

    pyautogui.screenshot = lambda: Image.new("RGB", (100, 100))
    pyautogui.size = lambda: (100, 100)
    pyautogui.locate = locate
    try:
        macro_clicker.find_target("target.png", 0.9, False, None)
    except SystemExit as e:
        return str(e.code)
    raise AssertionError("find_target did not exit")


def test_missing_opencv():
    assert "OpenCV is not installed" in exit_message(NotImplementedError())


def test_needle_too_large():
    assert "larger than the search area" in exit_message(ValueError("needle dimension(s) exceed"))


if __name__ == "__main__":
    test_missing_opencv()
    test_needle_too_large()
    print("ok")
