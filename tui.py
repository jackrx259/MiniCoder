"""
tui.py — MiniCoder interactive terminal UI (prompt_toolkit-based).

A flicker-free, responsive terminal UI that works reliably on Windows
PowerShell, Windows Terminal, and most Unix terminals.

Architecture:
  - Main thread: synchronous REPL using PromptSession + patch_stdout
  - Output: print_formatted_text with ANSI colors rendered by Rich
  - Input: PromptSession with InMemoryHistory (native ↑/↓ history)
  - Selection menus: temporary non-fullscreen Application with arrow-key bindings
  - Status bar: bottom_toolbar on the PromptSession (updates in real time)
  - Ctrl+C: raises KeyboardInterrupt → caught by agent.start_loop()
  - Ctrl+D: raises EOFError → TUI returns None from prompt_user_message()

Requires: prompt_toolkit>=3.0, rich>=13.0
"""

from __future__ import annotations

import io
import json
from typing import Callable, List, Optional, Tuple

from prompt_toolkit import Application, PromptSession
from prompt_toolkit.formatted_text import ANSI, FormattedText, to_formatted_text
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import print_formatted_text
from prompt_toolkit.styles import Style

from rich.console import Console
from rich.markdown import Markdown as RichMarkdown


# ─────────────────────────────────────────────────────────────────────────────
# Rich → ANSI helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rich_to_ansi(markup: str, width: int = 120) -> str:
    """Run a Rich markup string through a string-buffer Console and return raw ANSI."""
    buf = io.StringIO()
    con = Console(file=buf, force_terminal=True, width=width, highlight=False, markup=True)
    con.print(markup, end="")
    return buf.getvalue()


def _rich_markdown_to_ansi(text: str, width: int = 100) -> str:
    """Same as _rich_to_ansi but renders Markdown first."""
    buf = io.StringIO()
    con = Console(file=buf, force_terminal=True, width=width, highlight=False)
    con.print(RichMarkdown(text), end="")
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Tool classification helpers
# ─────────────────────────────────────────────────────────────────────────────

_WRITE_TOOLS = {
    "write_file", "replace_in_file", "append_to_file", "create_skill",
    "delete_file", "move_file", "copy_file",
    "browser_type", "browser_select",
    "desktop_type", "desktop_scroll",
}
_EXEC_TOOLS = {"run_command", "change_dir", "browser_open", "browser_click",
               "browser_navigate", "browser_close", "browser_press_key",
               "desktop_open_app", "desktop_click", "desktop_double_click",
               "desktop_hotkey", "desktop_press_key", "desktop_move_mouse"}
_READ_ONLY_TOOLS = {
    "read_file", "list_dir", "get_cwd", "find_files", "search_files", "get_file_info",
}


def _tool_icon(name: str) -> str:
    if name in _EXEC_TOOLS:
        return "[bold red]⚡执行[/]"
    if name in _WRITE_TOOLS:
        return "[bold yellow]✏️ 写入[/]"
    return "[bold green]🔍读取[/]"


def _summarize_tool_result(name: str, result: str) -> str:
    if name == "read_file":
        lines = result.count('\n') + 1
        chars = len(result)
        return f"读取了 {lines} 行 ({chars:,} 字符)"
    elif name == "list_dir":
        entries = result.count('\n') + 1 if result.strip() else 0
        return f"目录含 {entries} 项"
    elif name == "find_files":
        count = result.count('\n')
        return f"找到 {count} 个匹配文件"
    elif name == "search_files":
        count = result.count('\n')
        return f"搜索到 {count} 条匹配结果"
    elif name == "get_cwd":
        return f"当前目录: {result.strip()}"
    elif name == "get_file_info":
        return result.strip()[:100]
    else:
        return result.strip()[:80]


# ─────────────────────────────────────────────────────────────────────────────
# Selection menu (non-fullscreen Application)
# ─────────────────────────────────────────────────────────────────────────────

# Style for the selection menu
_MENU_STYLE = Style.from_dict({
    "title": "bold cyan",
    "selected": "bold white bg:#1f6feb",
    "unselected": "#8b949e",
    "border": "#388bfd",
})


def _run_selection_menu(
        items: List[Tuple[str, str]],
        title: str = "",
) -> str:
    """
    Show a non-fullscreen arrow-key selection menu.

    Args:
        items: List of (display_label, return_value) tuples.
        title: Optional header line.

    Returns:
        The return_value of the selected item.
        Falls back to items[-1][1] on Escape/Ctrl+C.
    """
    idx = [0]
    chosen = [items[-1][1]]  # default: last item (typically "n" / cancel)

    def get_menu_text() -> List[Tuple[str, str]]:
        result: List[Tuple[str, str]] = []
        if title:
            result.append(("class:title", f"  {title}\n"))
        for i, (label, _) in enumerate(items):
            if i == idx[0]:
                result.append(("class:selected", f"  ▶  {label}\n"))
            else:
                result.append(("class:unselected", f"     {label}\n"))
        return result

    kb = KeyBindings()

    @kb.add("up")
    def _up(event):
        if idx[0] > 0:
            idx[0] -= 1
        event.app.invalidate()

    @kb.add("down")
    def _down(event):
        if idx[0] < len(items) - 1:
            idx[0] += 1
        event.app.invalidate()

    @kb.add("enter")
    def _enter(event):
        chosen[0] = items[idx[0]][1]
        event.app.exit()

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event):
        chosen[0] = items[-1][1]
        event.app.exit()

    control = FormattedTextControl(get_menu_text, focusable=False)
    menu_window = Window(content=control, dont_extend_height=True)
    layout = Layout(HSplit([menu_window]))

    app = Application(
        layout=layout,
        key_bindings=kb,
        style=_MENU_STYLE,
        full_screen=False,
        mouse_support=False,
        color_depth=ColorDepth.TRUE_COLOR,
    )
    app.run()
    return chosen[0]


# ─────────────────────────────────────────────────────────────────────────────
# TUI class
# ─────────────────────────────────────────────────────────────────────────────

class TUI:
    """
    MiniCoder TUI using prompt_toolkit.

    The agent calls our methods synchronously from the main thread.
    `patch_stdout` ensures that Rich-rendered output never garbles the
    prompt line — output appears cleanly above the input box.

    All print_*() methods log immediately.
    All prompt_*() methods block until the user responds.
    """

    def __init__(self) -> None:
        self._history = InMemoryHistory()
        self._session: Optional[PromptSession] = None
        self._patch_ctx = None

        # Status bar state (updated via print_token_info / welcome)
        self._status_model: str = ""
        self._status_mode: str = ""
        self._status_tokens: str = ""

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _out(self, ansi: str) -> None:
        """Write raw ANSI to the patch_stdout-aware output channel."""
        print_formatted_text(ANSI(ansi), end="")

    def _log(self, markup: str) -> None:
        """Render Rich markup to ANSI and print it."""
        self._out(_rich_to_ansi(markup) + "\n")

    def _get_toolbar(self) -> List[Tuple[str, str]]:
        """Bottom toolbar formatter for PromptSession."""
        parts: List[Tuple[str, str]] = []
        if self._status_model:
            parts += [("class:toolbar.model", f" {self._status_model} ")]
        if self._status_mode:
            parts += [("class:toolbar.sep", " · "), ("class:toolbar.mode", self._status_mode)]
        if self._status_tokens:
            parts += [("class:toolbar.sep", " · "), ("class:toolbar.tokens", self._status_tokens)]
        return parts if parts else [("class:toolbar", " MiniCoder ")]

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def run(self, agent_start_fn: Callable) -> None:
        """Wrap the agent in a patch_stdout context and hand control to it.
        Blocks until the agent's REPL exits."""
        session_style = Style.from_dict({
            # Prompt prefix
            "prompt": "bold cyan",
            # Bottom toolbar
            "toolbar": "bg:#161b22 #8b949e",
            "toolbar.model": "bg:#161b22 bold #58a6ff",
            "toolbar.mode": "bg:#161b22 #e6edf3",
            "toolbar.tokens": "bg:#161b22 #8b949e",
            "toolbar.sep": "bg:#161b22 #30363d",
        })

        self._session = PromptSession(
            history=self._history,
            style=session_style,
            color_depth=ColorDepth.TRUE_COLOR,
            bottom_toolbar=self._get_toolbar,
        )

        with patch_stdout(raw=True):
            try:
                agent_start_fn()
            except (EOFError, KeyboardInterrupt):
                pass

    # ── Phase separators ──────────────────────────────────────────────────────

    def print_separator_thinking(self) -> None:
        self._log("\n[bold blue]══════════════════════ 🤔 Thinking ══════════════════════[/]")

    def print_separator_executing(self, count: int) -> None:
        self._log(f"\n[bold yellow]────────────────────── ⚙️  Executing ({count} steps) ──────────────────────[/]")

    def print_separator_response(self) -> None:
        self._log("\n[bold green]══════════════════════ ✦ Response ══════════════════════[/]")

    # ── Startup / welcome ─────────────────────────────────────────────────────

    def welcome(self, model: str, mode: str, skill_count: int, max_loops: int) -> None:
        self._status_model = model
        self._status_mode = mode
        self._log(
            f"\n[bold cyan]╔══════════════════════════════════════════╗[/]\n"
            f"[bold cyan]║  🤖  MiniCoder — Agentic CLI Assistant   ║[/]\n"
            f"[bold cyan]╚══════════════════════════════════════════╝[/]\n"
            f"  [dim cyan]Model[/]  [bold white]{model}[/]\n"
            f"  [dim cyan]Mode[/]   [bold white]{mode}[/]\n"
            f"  [dim cyan]Skills[/] [white]{skill_count} skill(s) loaded[/]\n"
            f"  [dim cyan]Loops[/]  [white]max {max_loops} per turn[/]\n"
            f"  [dim]Type a task · /help for commands · Ctrl+D to quit[/]\n"
        )

    # ── LLM lifecycle ─────────────────────────────────────────────────────────

    def print_thinking(self) -> None:
        self._log("[dim]  ⋯  thinking…[/]")

    def print_token_info(self, info: str) -> None:
        if info:
            self._log(f"  [dim blue]{info}[/]")
            self._status_tokens = info

    def print_final_response(self, text: str) -> None:
        """Render the LLM response using Rich Markdown then print."""
        if not text:
            return
        self.print_separator_response()
        try:
            ansi = _rich_markdown_to_ansi(text)
            self._out(ansi + "\n")
        except Exception:
            for line in text.splitlines():
                self._log(f"  {line}")
        self._log("[bold green]══════════════════════════════════════════════════════════[/]\n")

    # ── Tool execution ────────────────────────────────────────────────────────

    def print_tool_call(self, loop: int, max_loops: int, name: str, args_preview: str) -> None:
        if len(args_preview) > 80:
            args_preview = args_preview[:80] + "…"
        self._log(
            f"  [dim][{loop}/{max_loops}][/] [bold magenta]⚙ {name}[/]  [dim white]{args_preview}[/]"
        )

    def print_tool_result(self, preview: str) -> None:
        """Show preview for write/execute tools."""
        escaped = preview.replace("[", "\\[")
        self._log(f"    [dim]↳ {escaped}[/]")

    def print_tool_summary(self, name: str, result: str) -> None:
        """Show one-line summary for read-only tool results."""
        summary = _summarize_tool_result(name, result)
        self._log(f"    [dim green]↳ {summary}[/]")

    def print_skills_updated(self) -> None:
        self._log("  [bold green]🧠 Skill saved![/]")

    # ── Plan review ───────────────────────────────────────────────────────────

    def print_plan(self, tool_calls: list) -> None:
        """Render the upcoming tool calls as a numbered plan before asking for approval."""
        self._log(f"\n[bold white on dark_blue]  📋  执行计划 — 共 {len(tool_calls)} 步操作  [/]")
        for i, tc in enumerate(tool_calls, 1):
            func = tc.get("function", {})
            name = func.get("name", "?")
            try:
                args = json.loads(func.get("arguments", "{}"))
            except Exception:
                args = {}
            key_parts = []
            for k, v in args.items():
                v_str = str(v)
                if len(v_str) > 50:
                    v_str = v_str[:50] + "…"
                key_parts.append(f"[dim cyan]{k}[/]=[dim white]{v_str}[/]")
            args_preview = "  ".join(key_parts[:3])
            icon = _tool_icon(name)
            self._log(f"  [dim]{i}.[/dim] {icon} [bold magenta]{name}[/]  {args_preview}")

    # ── Plan text confirmation ────────────────────────────────────────────────

    def prompt_plan_confirmation(self, plan_text: str) -> str:
        """
        Show a selection menu after the LLM outputs a text plan.
        Returns: 'continue', 'reject', or custom feedback text.
        """
        value = _run_selection_menu(
            items=[
                ("▶️  继续执行 (按计划进行)", "continue"),
                ("❌ 拒绝 (停止执行)", "reject"),
                ("✏️  修改计划…", "__custom__"),
            ],
            title="📋 已生成计划，请选择操作：",
        )
        if value == "__custom__":
            return self.prompt_text("输入修改意见")
        return value

    # ── Context pruning ───────────────────────────────────────────────────────

    def print_context_pruning_start(self, limit: int) -> None:
        self._log(f"  [yellow]🧹 Context exceeded {limit:,} chars — pruning…[/]")

    def print_context_prune(self, limit: int, after: int) -> None:
        self._log(f"  [dim yellow]🧹 Context pruned → {after:,} chars[/]")

    # ── Status messages ───────────────────────────────────────────────────────

    def print_info(self, msg: str) -> None:
        self._log(f"  [cyan]ℹ  {msg}[/]")

    def print_success(self, msg: str) -> None:
        self._log(f"  [bold green]✅ {msg}[/]")

    def print_warn(self, msg: str) -> None:
        self._log(f"  [bold yellow]⚠️  {msg}[/]")

    def print_error(self, msg: str) -> None:
        self._log(f"  [bold red]❌ {msg}[/]")

    def print_interrupted(self) -> None:
        self._log("\n  [dim][Interrupted][/]")

    def print_goodbye(self) -> None:
        self._log("\n  [bold cyan]👋  Goodbye![/]\n")

    def print_help(self) -> None:
        self._log(
            "\n[bold cyan]  ╔══════════════════════════════════════════╗[/]\n"
            "[bold cyan]  ║           MiniCoder — Commands           ║[/]\n"
            "[bold cyan]  ╚══════════════════════════════════════════╝[/]\n"
            "  [bold white]/help[/]          [dim]Show this help message[/]\n"
            "  [bold white]/clear[/]         [dim]Clear conversation history (keep system prompt)[/]\n"
            "  [bold white]/history[/]       [dim]Show message count and role breakdown[/]\n"
            "  [bold white]/save [path][/]   [dim]Save session to file (default: session.json)[/]\n"
            "  [bold white]/load [path][/]   [dim]Load session from file (default: session.json)[/]\n"
            "  [bold white]/usage[/]         [dim]Show token usage statistics[/]\n"
            "  [bold white]/claw <task>[/]   [dim]Claw agent mode — automate browser & desktop apps[/]\n"
            "  [bold white]exit / quit[/]    [dim]Exit MiniCoder (offers to save session)[/]\n"
            "  [bold white]Ctrl+D[/]         [dim]Quit immediately[/]\n"
            "  [bold white]Ctrl+C[/]         [dim]Interrupt current agent action[/]\n"
            "  [bold white]↑ / ↓[/]          [dim]Navigate input history[/]\n"
        )

    # ── Blocking input helpers ────────────────────────────────────────────────

    def prompt_user_message(self) -> Optional[str]:
        """
        Primary REPL prompt — blocks until user submits.
        Returns None on EOF (Ctrl+D) or persistent KeyboardInterrupt.
        """
        assert self._session is not None, "TUI.run() must be called before prompt_user_message()"
        try:
            text = self._session.prompt(
                [("class:prompt", " ❯ ")],
                bottom_toolbar=self._get_toolbar,
            )
            # Echo submitted input in the log for visual consistency
            if text.strip():
                self._log(f"\n[bold cyan]❯[/] [white]{text.strip()}[/]")
            return text.strip()
        except EOFError:
            return None
        except KeyboardInterrupt:
            # Single Ctrl+C at the empty prompt → treat as None (quit signal)
            return None

    def prompt_plan_input(self) -> str:
        """Show the approval selection menu and return the user's choice.
        Returns 'y', 'n', 'a', or free-text feedback."""
        value = _run_selection_menu(
            items=[
                ("✅ 执行全部", "y"),
                ("❌ 拒绝全部", "n"),
                ("⚡ 全程自动 (后续不再询问)", "a"),
                ("💬 修改计划 / 自定义输入…", "__custom__"),
            ],
            title="📋 执行计划 — 请选择操作：",
        )
        if value == "__custom__":
            return self.prompt_text("输入修改意见或自定义指令")
        return value

    def prompt_yes_no(self, question: str) -> bool:
        """Show a yes/no selection menu. Returns True for yes."""
        value = _run_selection_menu(
            items=[
                ("✅ 是 (Yes)", "y"),
                ("❌ 否 (No)", "n"),
            ],
            title=question,
        )
        return value.lower().startswith("y")

    def prompt_input(self, label: str = "  Your choice") -> str:
        """Convenience wrapper around prompt_text."""
        return self.prompt_text(label)

    def prompt_text(self, label: str) -> str:
        """Show a one-line text prompt and return the user's input."""
        assert self._session is not None
        try:
            text = self._session.prompt(
                [("class:prompt", f" {label}: ")],
                bottom_toolbar=self._get_toolbar,
            )
            return text.strip()
        except (EOFError, KeyboardInterrupt):
            return ""
