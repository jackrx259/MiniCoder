"""
desktop_tools.py — Cross-platform desktop automation tools for MiniCoder using pyautogui.

Provides screen-level automation tools that can control any desktop application:
open apps, click at coordinates, type text, press keys, take screenshots, etc.

Supported platforms: macOS, Linux (X11), Windows.

Notes:
  - macOS: System Preferences → Privacy & Security → Accessibility → enable terminal.
  - Linux: requires xdotool and xclip for best experience.
  - Windows: may need to run terminal as administrator for some apps.
"""

import os
import sys
import time
import subprocess
import platform
from datetime import datetime

_PLATFORM = platform.system()  # 'Darwin', 'Linux', 'Windows'


# ---------------------------------------------------------------------------
# Screenshot directory
# ---------------------------------------------------------------------------

_SCREENSHOTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".desktop_screenshots"
)


def _ensure_pyautogui():
    """Lazy import pyautogui with a friendly error message."""
    try:
        import pyautogui
        return pyautogui
    except ImportError:
        raise RuntimeError(
            "pyautogui is not installed. Run:\n"
            "  uv add pyautogui\n"
            "Then restart MiniCoder."
        )


# ---------------------------------------------------------------------------
# Tool Functions
# ---------------------------------------------------------------------------

def desktop_open_app(app_name: str) -> str:
    """Open a desktop application by name (cross-platform)."""
    try:
        if _PLATFORM == "Darwin":
            cmd = ["open", "-a", app_name]
        elif _PLATFORM == "Windows":
            # Use 'start' via cmd.exe for Windows
            cmd = ["cmd", "/c", "start", "", app_name]
        else:
            # Linux: try the app name directly (lowercase), common desktop entries
            app_lower = app_name.lower().replace(" ", "-")
            cmd = [app_lower]
            # Fallback: try xdg-open if it looks like a .desktop entry
            if not _which(app_lower):
                cmd = ["xdg-open", app_name]

        result = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            timeout=10,
        )
        if result.returncode == 0:
            time.sleep(1.5)
            return f"✅ Opened application: {app_name}"
        else:
            return f"Error opening '{app_name}': {result.stderr.strip()}"
    except FileNotFoundError:
        return (
            f"Error: '{app_name}' not found. "
            f"On {_PLATFORM}, try the exact executable name."
        )
    except Exception as e:
        return f"Error opening application: {e}"


def _which(name: str):
    """Check if an executable exists on PATH."""
    import shutil
    return shutil.which(name)


def desktop_screenshot(name: str = "", region: str = "") -> str:
    """Take a screenshot of the entire screen or a specific region.
    
    Args:
        name: Optional filename (without extension).
        region: Optional region as 'x,y,width,height' (e.g. '100,200,800,600').
    """
    try:
        pyautogui = _ensure_pyautogui()
        os.makedirs(_SCREENSHOTS_DIR, exist_ok=True)
        filename = name or f"desktop_{int(time.time())}"
        if not filename.endswith(".png"):
            filename += ".png"
        path = os.path.join(_SCREENSHOTS_DIR, filename)

        if region:
            try:
                parts = [int(x.strip()) for x in region.split(",")]
                if len(parts) == 4:
                    img = pyautogui.screenshot(region=(parts[0], parts[1], parts[2], parts[3]))
                else:
                    return "Error: region must be 'x,y,width,height' (4 comma-separated integers)."
            except ValueError:
                return "Error: region must be 'x,y,width,height' (4 comma-separated integers)."
        else:
            img = pyautogui.screenshot()

        img.save(path)
        size = os.path.getsize(path)
        screen_w, screen_h = pyautogui.size()
        return (
            f"📸 Desktop screenshot saved: {path}\n"
            f"   Size: {size:,} bytes\n"
            f"   Screen resolution: {screen_w}×{screen_h}"
        )
    except RuntimeError:
        raise
    except Exception as e:
        return f"Error taking desktop screenshot: {e}"


def desktop_click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    """Click at screen coordinates.
    
    Args:
        x: X coordinate on screen.
        y: Y coordinate on screen.
        button: 'left', 'right', or 'middle'. Default: 'left'.
        clicks: Number of clicks (1=single, 2=double). Default: 1.
    """
    try:
        pyautogui = _ensure_pyautogui()
        pyautogui.click(x=x, y=y, button=button, clicks=clicks)
        click_type = "Double-clicked" if clicks == 2 else "Clicked"
        return f"✅ {click_type} ({button}) at ({x}, {y})"
    except Exception as e:
        return f"Error clicking at ({x}, {y}): {e}"


def desktop_double_click(x: int, y: int) -> str:
    """Double-click at screen coordinates."""
    try:
        pyautogui = _ensure_pyautogui()
        pyautogui.doubleClick(x=x, y=y)
        return f"✅ Double-clicked at ({x}, {y})"
    except Exception as e:
        return f"Error double-clicking at ({x}, {y}): {e}"


def desktop_type(text: str, interval: float = 0.02) -> str:
    """Type text using the keyboard.
    
    For CJK characters (Chinese, Japanese, Korean), this uses the clipboard
    as pyautogui.typewrite only supports ASCII.
    Works on macOS (pbcopy), Linux (xclip/xsel), and Windows (clip).
    """
    try:
        pyautogui = _ensure_pyautogui()
        # Check if text contains non-ASCII characters
        if any(ord(c) > 127 for c in text):
            # Use clipboard for non-ASCII text (CJK, etc.)
            _copy_to_clipboard(text)
            # Paste from clipboard: Cmd+V on macOS, Ctrl+V elsewhere
            if _PLATFORM == "Darwin":
                pyautogui.hotkey('command', 'v')
            else:
                pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.3)
            display = text[:50] + "…" if len(text) > 50 else text
            return f"✅ Typed (via clipboard): '{display}'"
        else:
            pyautogui.typewrite(text, interval=interval)
            display = text[:50] + "…" if len(text) > 50 else text
            return f"✅ Typed: '{display}'"
    except RuntimeError:
        raise
    except Exception as e:
        return f"Error typing text: {e}"


def _copy_to_clipboard(text: str):
    """Copy text to system clipboard (cross-platform)."""
    if _PLATFORM == "Darwin":
        proc = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
        proc.communicate(text.encode('utf-8'))
    elif _PLATFORM == "Windows":
        proc = subprocess.Popen(['clip'], stdin=subprocess.PIPE)
        proc.communicate(text.encode('utf-16-le'))
    else:
        # Linux: try xclip, then xsel
        for cmd in (['xclip', '-selection', 'clipboard'],
                    ['xsel', '--clipboard', '--input']):
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                proc.communicate(text.encode('utf-8'))
                return
            except FileNotFoundError:
                continue
        raise RuntimeError(
            "Cannot copy to clipboard. Install xclip or xsel:\n"
            "  sudo apt install xclip  # Debian/Ubuntu\n"
            "  sudo pacman -S xclip    # Arch"
        )


def desktop_hotkey(*keys: str) -> str:
    """Press a keyboard shortcut.
    
    Args:
        keys: Key names to press together. Examples:
              macOS: ('command', 'c'), ('command', 'shift', '3')
              Linux/Win: ('ctrl', 'c'), ('alt', 'tab')
    """
    try:
        pyautogui = _ensure_pyautogui()
        pyautogui.hotkey(*keys)
        combo = "+".join(keys)
        return f"✅ Pressed hotkey: {combo}"
    except Exception as e:
        return f"Error pressing hotkey: {e}"


def desktop_press_key(key: str) -> str:
    """Press a single keyboard key.
    
    Supports: enter, tab, escape, space, backspace, delete,
    up, down, left, right, home, end, pageup, pagedown,
    f1-f12, and any single character.
    """
    try:
        pyautogui = _ensure_pyautogui()
        pyautogui.press(key)
        return f"✅ Pressed key: {key}"
    except Exception as e:
        return f"Error pressing key '{key}': {e}"


def desktop_move_mouse(x: int, y: int, duration: float = 0.3) -> str:
    """Move the mouse cursor to screen coordinates.
    
    Args:
        x: Target X coordinate.
        y: Target Y coordinate.
        duration: Movement duration in seconds. Default: 0.3.
    """
    try:
        pyautogui = _ensure_pyautogui()
        pyautogui.moveTo(x, y, duration=duration)
        return f"✅ Moved mouse to ({x}, {y})"
    except Exception as e:
        return f"Error moving mouse to ({x}, {y}): {e}"


def desktop_scroll(amount: int, x: int = None, y: int = None) -> str:
    """Scroll the mouse wheel.
    
    Args:
        amount: Scroll amount. Positive = up, negative = down.
        x: Optional X coordinate to scroll at.
        y: Optional Y coordinate to scroll at.
    """
    try:
        pyautogui = _ensure_pyautogui()
        if x is not None and y is not None:
            pyautogui.scroll(amount, x=x, y=y)
            return f"✅ Scrolled {'up' if amount > 0 else 'down'} by {abs(amount)} at ({x}, {y})"
        else:
            pyautogui.scroll(amount)
            return f"✅ Scrolled {'up' if amount > 0 else 'down'} by {abs(amount)}"
    except Exception as e:
        return f"Error scrolling: {e}"


def desktop_get_mouse_pos() -> str:
    """Get the current mouse cursor position."""
    try:
        pyautogui = _ensure_pyautogui()
        pos = pyautogui.position()
        return f"🖱️ Mouse position: ({pos.x}, {pos.y})"
    except Exception as e:
        return f"Error getting mouse position: {e}"


def desktop_get_screen_size() -> str:
    """Get the screen resolution."""
    try:
        pyautogui = _ensure_pyautogui()
        size = pyautogui.size()
        return f"🖥️ Screen size: {size.width}×{size.height}"
    except Exception as e:
        return f"Error getting screen size: {e}"


def desktop_find_image(image_path: str, confidence: float = 0.8) -> str:
    """Find an image on the screen and return its location.
    
    Args:
        image_path: Path to the image file to search for.
        confidence: Match confidence threshold (0.0-1.0). Default: 0.8.
    """
    try:
        pyautogui = _ensure_pyautogui()
        if not os.path.exists(image_path):
            return f"Error: Image file '{image_path}' not found."
        try:
            location = pyautogui.locateOnScreen(image_path, confidence=confidence)
        except pyautogui.ImageNotFoundException:
            return f"🔍 Image not found on screen: {image_path}"
        except Exception as e:
            # Some systems don't have opencv for confidence matching
            if "confidence" in str(e).lower():
                location = pyautogui.locateOnScreen(image_path)
            else:
                raise

        if location is None:
            return f"🔍 Image not found on screen: {image_path}"
        center = pyautogui.center(location)
        return (
            f"🔍 Image found!\n"
            f"   Location: x={location.left}, y={location.top}, "
            f"w={location.width}, h={location.height}\n"
            f"   Center: ({center.x}, {center.y})\n"
            f"   Use desktop_click({center.x}, {center.y}) to click it."
        )
    except RuntimeError:
        raise
    except Exception as e:
        return f"Error finding image: {e}"


# ---------------------------------------------------------------------------
# Desktop Tools Schema (OpenAI function-calling format)
# ---------------------------------------------------------------------------

DESKTOP_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "desktop_open_app",
            "description": (
                "Open a desktop application by name (cross-platform). "
                "macOS: 'WeChat', 'Safari', 'Calculator'. "
                "Linux: 'firefox', 'nautilus', 'calculator'. "
                "Windows: 'notepad', 'calc', 'explorer'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Application name or executable to open."
                    }
                },
                "required": ["app_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_screenshot",
            "description": (
                "Take a screenshot of the entire screen or a specific region. "
                "ALWAYS take a screenshot after opening an app or performing actions "
                "to see the current state of the screen. This is critical for knowing "
                "where to click or type next."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Optional filename for the screenshot (without .png extension)."
                    },
                    "region": {
                        "type": "string",
                        "description": "Optional capture region as 'x,y,width,height'. Omit for full screen."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_click",
            "description": (
                "Click at specific screen coordinates. Take a screenshot first to determine "
                "the correct coordinates for the element you want to click."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate on screen."},
                    "y": {"type": "integer", "description": "Y coordinate on screen."},
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "Mouse button. Default: 'left'."
                    },
                    "clicks": {
                        "type": "integer",
                        "description": "Number of clicks (1=single, 2=double). Default: 1."
                    }
                },
                "required": ["x", "y"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_double_click",
            "description": "Double-click at specific screen coordinates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate on screen."},
                    "y": {"type": "integer", "description": "Y coordinate on screen."}
                },
                "required": ["x", "y"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_type",
            "description": (
                "Type text using the keyboard. Supports Chinese/CJK characters "
                "(uses clipboard + paste for non-ASCII). Cross-platform. "
                "Make sure the correct text field is focused before typing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to type."},
                    "interval": {
                        "type": "number",
                        "description": "Delay between keystrokes in seconds. Default: 0.02."
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_hotkey",
            "description": (
                "Press a keyboard shortcut. Pass each key as a separate argument. "
                "macOS: ('command', 'c') for copy. "
                "Linux/Windows: ('ctrl', 'c') for copy. "
                "Cross-platform: ('alt', 'tab') to switch apps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of key names to press together, e.g. ['command', 'c']."
                    }
                },
                "required": ["keys"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_press_key",
            "description": (
                "Press a single keyboard key. "
                "Supports: enter, tab, escape, space, backspace, delete, "
                "up, down, left, right, f1-f12, and any single character."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key name to press (e.g. 'enter', 'tab', 'escape')."}
                },
                "required": ["key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_move_mouse",
            "description": "Move the mouse cursor to specific screen coordinates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Target X coordinate."},
                    "y": {"type": "integer", "description": "Target Y coordinate."},
                    "duration": {
                        "type": "number",
                        "description": "Movement duration in seconds. Default: 0.3."
                    }
                },
                "required": ["x", "y"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_scroll",
            "description": "Scroll the mouse wheel at the current position or at specific coordinates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "integer",
                        "description": "Scroll amount. Positive = scroll up, negative = scroll down."
                    },
                    "x": {"type": "integer", "description": "Optional X coordinate to scroll at."},
                    "y": {"type": "integer", "description": "Optional Y coordinate to scroll at."}
                },
                "required": ["amount"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_get_mouse_pos",
            "description": "Get the current mouse cursor position on screen.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_get_screen_size",
            "description": "Get the screen resolution (width × height in pixels).",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_find_image",
            "description": (
                "Search for an image on the screen and return its coordinates. "
                "Useful for finding UI elements like buttons or icons."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": "Absolute path to the image file to search for on screen."
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Match confidence (0.0-1.0). Default: 0.8."
                    }
                },
                "required": ["image_path"]
            }
        }
    },
]
