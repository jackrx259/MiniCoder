"""
browser_tools.py — Browser automation tools for MiniCoder using Playwright.

Provides a set of browser interaction tools that the LLM agent can use
to perform web automation tasks (navigate, click, type, screenshot, etc.).

Architecture:
  - BrowserSession: singleton managing the Playwright browser lifecycle
  - Tool functions: thin wrappers that delegate to BrowserSession
  - BROWSER_TOOLS_SCHEMA: OpenAI function-calling schemas for the LLM
"""

import os
import base64
import time
from datetime import datetime


# ---------------------------------------------------------------------------
# BrowserSession — Singleton browser lifecycle manager
# ---------------------------------------------------------------------------

class BrowserSession:
    """
    Manages a single Playwright browser instance with one active page.
    Lazily initialised on first use; cleaned up via close().
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None
        self._screenshots_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".browser_screenshots"
        )

    @property
    def is_active(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    def _ensure_browser(self, headless: bool = False) -> None:
        """Start Playwright + Chromium if not already running."""
        if self.is_active:
            return

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright is not installed. Run:\n"
                "  uv add playwright\n"
                "  playwright install chromium\n"
                "Then restart MiniCoder."
            )

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=headless,
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-infobars",
            ],
        )
        self._page = self._browser.new_page(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

    def _ensure_page(self) -> None:
        """Make sure we have an active page. Re-creates if it was closed."""
        if not self.is_active:
            raise RuntimeError("Browser not started. Use browser_open first.")
        if self._page is None or self._page.is_closed():
            self._page = self._browser.new_page(
                viewport={"width": 1280, "height": 800}
            )

    def open(self, url: str, headless: bool = False) -> str:
        """Open browser and navigate to URL."""
        try:
            self._ensure_browser(headless=headless)
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = self._page.title()
            return (
                f"🌐 Browser opened: {url}\n"
                f"   Page title: {title}\n"
                f"   Viewport: 1280×800"
            )
        except RuntimeError:
            raise
        except Exception as e:
            return f"Error opening browser: {e}"

    def navigate(self, url: str) -> str:
        """Navigate to a new URL on the existing page."""
        try:
            self._ensure_page()
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = self._page.title()
            return f"🌐 Navigated to: {url}\n   Page title: {title}"
        except RuntimeError:
            raise
        except Exception as e:
            return f"Error navigating to '{url}': {e}"

    def click(self, selector: str, text: str = None) -> str:
        """Click on an element by CSS selector or text content."""
        try:
            self._ensure_page()
            if text and not selector:
                # Click by visible text
                self._page.get_by_text(text, exact=False).first.click(timeout=10000)
                return f"✅ Clicked element with text: '{text}'"
            elif selector:
                self._page.click(selector, timeout=10000)
                return f"✅ Clicked element: {selector}"
            else:
                return "Error: provide either 'selector' or 'text' to identify the element."
        except Exception as e:
            return f"Error clicking element: {e}"

    def type_text(self, selector: str, text: str, clear: bool = True) -> str:
        """Type text into an input field identified by CSS selector."""
        try:
            self._ensure_page()
            if clear:
                self._page.fill(selector, text, timeout=10000)
            else:
                self._page.type(selector, text, timeout=10000)
            # Mask sensitive content in result
            display_text = text if len(text) < 30 else text[:30] + "…"
            return f"✅ Typed '{display_text}' into {selector}"
        except Exception as e:
            return f"Error typing text: {e}"

    def screenshot(self, name: str = None) -> str:
        """Take a screenshot and save it. Returns the file path."""
        try:
            self._ensure_page()
            os.makedirs(self._screenshots_dir, exist_ok=True)
            filename = name or f"screenshot_{int(time.time())}"
            if not filename.endswith(".png"):
                filename += ".png"
            path = os.path.join(self._screenshots_dir, filename)
            self._page.screenshot(path=path, full_page=False)
            size = os.path.getsize(path)
            return (
                f"📸 Screenshot saved: {path}\n"
                f"   Size: {size:,} bytes\n"
                f"   Page title: {self._page.title()}\n"
                f"   URL: {self._page.url}"
            )
        except Exception as e:
            return f"Error taking screenshot: {e}"

    def get_text(self, selector: str = None) -> str:
        """Get visible text content from the page or a specific element."""
        try:
            self._ensure_page()
            if selector:
                el = self._page.query_selector(selector)
                if not el:
                    return f"Error: Element '{selector}' not found on page."
                text = el.inner_text()
            else:
                text = self._page.inner_text("body")
            # Truncate if too long
            if len(text) > 8000:
                text = text[:8000] + "\n...[TRUNCATED — use a more specific selector]"
            return text
        except Exception as e:
            return f"Error getting text: {e}"

    def get_elements(self, selector: str = None) -> str:
        """List interactive elements on the page (links, buttons, inputs)."""
        try:
            self._ensure_page()
            if selector:
                elements = self._page.query_selector_all(selector)
            else:
                elements = self._page.query_selector_all(
                    "a, button, input, select, textarea, [role='button'], [onclick]"
                )

            if not elements:
                return "No interactive elements found."

            lines = [f"🔍 Found {len(elements)} interactive element(s):"]
            for i, el in enumerate(elements[:50]):
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                el_type = el.get_attribute("type") or ""
                el_id = el.get_attribute("id") or ""
                el_name = el.get_attribute("name") or ""
                el_text = (el.inner_text() or "").strip()[:60]
                el_placeholder = el.get_attribute("placeholder") or ""
                el_href = el.get_attribute("href") or ""
                el_role = el.get_attribute("role") or ""

                parts = [f"  {i+1}. <{tag}"]
                if el_type:
                    parts.append(f' type="{el_type}"')
                if el_id:
                    parts.append(f' id="{el_id}"')
                if el_name:
                    parts.append(f' name="{el_name}"')
                if el_role:
                    parts.append(f' role="{el_role}"')
                parts.append(">")
                if el_text:
                    parts.append(f" text={el_text!r}")
                if el_placeholder:
                    parts.append(f" placeholder={el_placeholder!r}")
                if el_href:
                    href_display = el_href[:50] + "…" if len(el_href) > 50 else el_href
                    parts.append(f" href={href_display!r}")

                lines.append("".join(parts))

            if len(elements) > 50:
                lines.append(f"  ...(showing 50 of {len(elements)} — use a selector to narrow down)")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting elements: {e}"

    def scroll(self, direction: str = "down", amount: int = 500) -> str:
        """Scroll the page up or down."""
        try:
            self._ensure_page()
            delta = amount if direction == "down" else -amount
            self._page.mouse.wheel(0, delta)
            self._page.wait_for_timeout(300)
            scroll_y = self._page.evaluate("window.scrollY")
            scroll_height = self._page.evaluate("document.body.scrollHeight")
            return (
                f"📜 Scrolled {direction} by {amount}px\n"
                f"   Current position: {scroll_y}px / {scroll_height}px total"
            )
        except Exception as e:
            return f"Error scrolling: {e}"

    def select_option(self, selector: str, value: str = None, label: str = None) -> str:
        """Select an option from a dropdown."""
        try:
            self._ensure_page()
            if value:
                self._page.select_option(selector, value=value, timeout=10000)
                return f"✅ Selected option value='{value}' in {selector}"
            elif label:
                self._page.select_option(selector, label=label, timeout=10000)
                return f"✅ Selected option label='{label}' in {selector}"
            else:
                return "Error: provide either 'value' or 'label' for the option."
        except Exception as e:
            return f"Error selecting option: {e}"

    def wait_for(self, selector: str, timeout: int = 10000, state: str = "visible") -> str:
        """Wait for an element to appear on the page."""
        try:
            self._ensure_page()
            self._page.wait_for_selector(selector, timeout=timeout, state=state)
            return f"✅ Element '{selector}' is now {state}."
        except Exception as e:
            return f"⏰ Timeout waiting for '{selector}': {e}"

    def press_key(self, key: str) -> str:
        """Press a keyboard key (Enter, Tab, Escape, etc.)."""
        try:
            self._ensure_page()
            self._page.keyboard.press(key)
            return f"✅ Pressed key: {key}"
        except Exception as e:
            return f"Error pressing key '{key}': {e}"

    def close(self) -> str:
        """Close the browser and clean up Playwright."""
        try:
            if self._page and not self._page.is_closed():
                self._page.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
            self._page = None
            self._browser = None
            self._playwright = None
            return "🔒 Browser closed."
        except Exception as e:
            self._page = None
            self._browser = None
            self._playwright = None
            return f"Browser closed (with warning: {e})"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

BROWSER_SESSION = BrowserSession()


# ---------------------------------------------------------------------------
# Tool Functions (wrappers around BrowserSession)
# ---------------------------------------------------------------------------

def browser_open(url: str, headless: bool = False) -> str:
    """Open a browser and navigate to a URL."""
    return BROWSER_SESSION.open(url, headless=headless)


def browser_navigate(url: str) -> str:
    """Navigate the current browser tab to a new URL."""
    return BROWSER_SESSION.navigate(url)


def browser_click(selector: str = "", text: str = "") -> str:
    """Click an element on the page by CSS selector or visible text."""
    return BROWSER_SESSION.click(selector=selector, text=text)


def browser_type(selector: str, text: str, clear: bool = True) -> str:
    """Type text into an input field. Set clear=false to append instead of replace."""
    return BROWSER_SESSION.type_text(selector, text, clear=clear)


def browser_screenshot(name: str = "") -> str:
    """Take a screenshot of the current page."""
    return BROWSER_SESSION.screenshot(name=name or None)


def browser_get_text(selector: str = "") -> str:
    """Get the visible text content of the page or a specific element."""
    return BROWSER_SESSION.get_text(selector=selector or None)


def browser_get_elements(selector: str = "") -> str:
    """List interactive elements (buttons, links, inputs) on the page."""
    return BROWSER_SESSION.get_elements(selector=selector or None)


def browser_scroll(direction: str = "down", amount: int = 500) -> str:
    """Scroll the page up or down by a pixel amount."""
    return BROWSER_SESSION.scroll(direction=direction, amount=amount)


def browser_select(selector: str, value: str = "", label: str = "") -> str:
    """Select an option from a <select> dropdown by value or visible label."""
    return BROWSER_SESSION.select_option(selector, value=value or None, label=label or None)


def browser_wait(selector: str, timeout: int = 10000, state: str = "visible") -> str:
    """Wait for an element to reach a certain state (visible, hidden, attached, detached)."""
    return BROWSER_SESSION.wait_for(selector, timeout=timeout, state=state)


def browser_press_key(key: str) -> str:
    """Press a keyboard key (Enter, Tab, Escape, ArrowDown, etc.)."""
    return BROWSER_SESSION.press_key(key)


def browser_close() -> str:
    """Close the browser and free resources."""
    return BROWSER_SESSION.close()


# ---------------------------------------------------------------------------
# Browser Tools Schema (OpenAI function-calling format)
# ---------------------------------------------------------------------------

BROWSER_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "browser_open",
            "description": (
                "Open a browser and navigate to a URL. "
                "This starts a Chromium browser instance. "
                "Use headless=true for invisible background operation, "
                "or headless=false (default) so the user can watch."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to open (e.g. https://example.com)."},
                    "headless": {
                        "type": "boolean",
                        "description": "Run in headless mode (no visible window). Default: false."
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "Navigate the current browser tab to a new URL. Browser must already be open.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to navigate to."}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": (
                "Click an element on the page. Identify it by CSS selector OR by visible text. "
                "Use browser_get_elements first to see what's clickable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector (e.g. '#submit-btn', '.login-button'). Optional if text is provided."
                    },
                    "text": {
                        "type": "string",
                        "description": "Visible text on the element to click. Optional if selector is provided."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": (
                "Type text into an input field identified by CSS selector. "
                "By default clears the field first (clear=true). "
                "Set clear=false to append to existing text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the input field (e.g. '#email', 'input[name=username]')."
                    },
                    "text": {"type": "string", "description": "The text to type in."},
                    "clear": {
                        "type": "boolean",
                        "description": "Clear existing field content before typing. Default: true."
                    }
                },
                "required": ["selector", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": (
                "Take a screenshot of the current page. Returns the saved file path. "
                "Use this to verify the current state of the page."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Optional filename for the screenshot (without .png extension)."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_get_text",
            "description": (
                "Get the visible text content of the entire page or a specific element. "
                "Returns up to 8000 characters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "Optional CSS selector to get text from a specific element. Omit for full page text."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_get_elements",
            "description": (
                "List interactive elements on the current page (links, buttons, inputs, selects). "
                "Returns tag name, id, name, text, and other attributes for each element. "
                "Use this to discover what you can click or type into."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "Optional CSS selector to filter elements. Omit to list all interactive elements."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_scroll",
            "description": "Scroll the page up or down by a pixel amount.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down"],
                        "description": "Scroll direction. Default: 'down'."
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Pixels to scroll. Default: 500."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_select",
            "description": "Select an option from a <select> dropdown by its value or visible label.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the <select> element."
                    },
                    "value": {
                        "type": "string",
                        "description": "The option value to select. Provide either value or label."
                    },
                    "label": {
                        "type": "string",
                        "description": "The visible label text of the option. Provide either value or label."
                    }
                },
                "required": ["selector"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_wait",
            "description": (
                "Wait for an element to reach a certain state. "
                "Useful before clicking or typing to ensure the element is ready."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the element to wait for."
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max wait time in milliseconds. Default: 10000."
                    },
                    "state": {
                        "type": "string",
                        "enum": ["visible", "hidden", "attached", "detached"],
                        "description": "Element state to wait for. Default: 'visible'."
                    }
                },
                "required": ["selector"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_press_key",
            "description": (
                "Press a keyboard key on the current page. "
                "Supports: Enter, Tab, Escape, ArrowDown, ArrowUp, Backspace, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Key name to press (e.g. 'Enter', 'Tab', 'Escape')."
                    }
                },
                "required": ["key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_close",
            "description": "Close the browser and free resources. Call this when the browser task is done.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
]
