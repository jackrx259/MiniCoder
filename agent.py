import os
import json
import sys
import time
from llm_client import LLMClient
from tools import TOOLS_SCHEMA, execute_tool, TODO_MANAGER, BG_MANAGER
from browser_tools import BROWSER_TOOLS_SCHEMA, BROWSER_SESSION
from desktop_tools import DESKTOP_TOOLS_SCHEMA
from tui import TUI

# Tools that only read/query data — safe to auto-approve without prompting
_READ_ONLY_TOOLS = {
    "read_file", "list_dir", "get_cwd", "find_files", "search_files",
    "get_file_info", "todo_write", "list_skills", "check_background",
    "browser_screenshot", "browser_get_text", "browser_wait", "browser_get_elements",
    "desktop_screenshot", "desktop_get_mouse_pos", "desktop_get_screen_size",
    "desktop_find_image",
}

# Tools that require special post-call handling
_SKILL_WRITE_TOOLS = {"create_skill", "delete_skill"}

# Approximate chars-per-token ratio (conservative estimate)
_CHARS_PER_TOKEN = 4


def _estimate_tokens(messages: list) -> int:
    """Rough token estimate: total chars / 4."""
    total = 0
    for m in messages:
        total += len(str(m.get("content") or ""))
        total += len(str(m.get("role") or ""))
        if m.get("tool_calls"):
            total += len(json.dumps(m["tool_calls"]))
        if m.get("tool_call_id"):
            total += len(m["tool_call_id"])
    return total // _CHARS_PER_TOKEN


class Agent:
    def __init__(self, config: dict, tui: TUI = None):
        self.client = LLMClient(
            api_key=config.get("api_key", ""),
            api_base=config.get("api_base", "https://api.openai.com/v1"),
            model=config.get("model", "gpt-4o"),
            timeout=config.get("timeout", 60),
            max_retries=config.get("max_retries", 3),
        )
        self.tui = tui or TUI()
        self.max_loops = config.get("max_loops", 20)
        self.max_context_tokens = config.get("max_context_tokens", 50000)
        self.approval_mode = config.get("approval_mode", "ask")
        self.load_skills = config.get("load_skills", True)
        self.base_system_prompt = self._build_base_prompt(config)
        self.messages = [
            {"role": "system", "content": self._build_dynamic_system_prompt()}
        ]
        self.loop_count = 0
        self.rounds_since_todo = 0
        self.browser_mode = False  # True when running a /claw task
        self._transcripts_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".transcripts"
        )

    # ── System Prompt ─────────────────────────────────────────────────────

    def _build_base_prompt(self, config: dict) -> str:
        user_prompt = config.get(
            "system_prompt",
            "You are MiniCoder, a powerful CLI coding assistant."
        )
        return user_prompt + """
You are MiniCoder, an advanced Agentic CLI programming assistant running locally.
You have the following special capabilities and constraints:

1. EXPLORATION: Use `search_files` to find classes/functions, `find_files` to locate files by pattern, and `list_dir` to explore before rewriting code.
2. EDITING: Do NOT rewrite an entire file if you only need to change a few lines. Use `replace_in_file` for precision edits. Ensure your `target` text exactly matches the existing file content (including spaces and newlines) and is unique.
3. APPENDING: Use `append_to_file` to add content to a file without reading it first.
4. NAVIGATION: Use `get_cwd` to know your current directory, `change_dir` to move between directories.
5. LARGE FILES: Use `get_file_info` FIRST to check a file's line count, then `read_file` with `start_line`/`end_line` to read specific sections. Never blindly read a large file.
6. SKILLS: Use `create_skill` to save reusable workflows, `list_skills` to view them, `delete_skill` to remove outdated ones. Check injected skills before starting a task.
7. SAFETY: You cannot run dangerous commands like `rm -rf`, `del /f /s`, `format`, etc.
8. AUTONOMY: Think step by step. If a command fails or a search returns no result, fix your parameters and try again. If you fail 3 times on the same thing, ask the user for help.
9. BACKGROUND TASKS: For long-running operations (npm install, pytest, docker build), use `run_background` instead of `run_command`. You'll be automatically notified when they finish. Use `check_background(task_id)` to poll status.
10. SUBAGENT: For complex research sub-tasks that need many tool calls (e.g., "analyse the entire codebase"), use `dispatch_task` to delegate to a sub-agent. It returns only a text summary, keeping your context clean.

== TASK TRACKING RULE (CRITICAL) ==
For ANY task with 3 or more steps, you MUST use `todo_write` to:
  a) BEFORE starting: create the full task list (all steps as 'pending').
  b) WHEN starting a step: mark it 'in_progress' (only ONE at a time).
  c) AFTER completing a step: mark it 'done' before moving to the next.
  d) AT THE END: ensure all tasks are marked 'done'.

== PLANNING RULE ==
For complex tasks involving multiple file modifications:
  a) First output a clear TEXT PLAN describing what you are about to do and why.
  b) Wait for user confirmation before proceeding with tool calls.
For simple tasks (single file read, quick question, one-step operation), proceed directly.
Read-only operations may be executed immediately without a prior text plan.
"""

    def _get_available_skills(self) -> str:
        if not self.load_skills:
            return "Skills loading disabled."
        skills_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
        if not os.path.exists(skills_dir):
            return "No custom skills learned yet."
        md_files = [f for f in os.listdir(skills_dir) if f.endswith(".md")]
        if not md_files:
            return "No custom skills learned yet."
        skills_text = []
        total_chars = 0
        for filename in sorted(md_files):
            filepath = os.path.join(skills_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                if total_chars + len(content) < 3000:
                    skills_text.append(f"--- SKILL: {filename} ---\n{content}\n")
                    total_chars += len(content)
                else:
                    lines = content.splitlines()
                    summary_lines = [l for l in lines[:6] if l.strip() and not l.startswith('---')]
                    summary = " | ".join(summary_lines[:2])
                    skills_text.append(
                        f"--- SKILL: {filename} ---\n{summary}\n"
                        f"(Use read_file('{filepath}') to see full instructions)\n"
                    )
            except Exception:
                pass
        if not skills_text:
            return "No custom skills learned yet."
        return "You have the following SKILLS/WORKFLOWS memorized:\n" + "\n".join(skills_text)

    def _build_dynamic_system_prompt(self) -> str:
        skills_section = self._get_available_skills()
        return f"{self.base_system_prompt}\n\n=== YOUR SKILLS ===\n{skills_section}"

    def _refresh_system_prompt(self):
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self._build_dynamic_system_prompt()

    # ── Message Management ────────────────────────────────────────────────

    def add_user_message(self, text: str):
        self._refresh_system_prompt()
        self.messages.append({"role": "user", "content": text})
        self.loop_count = 0
        self.rounds_since_todo = 0

    # ── Three-Layer Context Compression ──────────────────────────────────

    def _micro_compact(self):
        """Layer 1: replace old tool results with placeholders (silent, every turn)."""
        KEEP_RECENT = 6
        tool_result_indices = [
            i for i, m in enumerate(self.messages)
            if m.get("role") == "tool"
        ]
        if len(tool_result_indices) <= KEEP_RECENT:
            return
        to_compact = tool_result_indices[:-KEEP_RECENT]
        for idx in to_compact:
            msg = self.messages[idx]
            content = msg.get("content", "")
            tool_name = msg.get("name", "tool")
            if isinstance(content, str) and len(content) > 80 and not content.startswith("[used "):
                msg["content"] = f"[used {tool_name}]"

    def _save_transcript(self) -> str:
        """Save full conversation JSONL to .transcripts/ for recovery."""
        os.makedirs(self._transcripts_dir, exist_ok=True)
        ts = int(time.time())
        path = os.path.join(self._transcripts_dir, f"transcript_{ts}.jsonl")
        try:
            with open(path, 'w', encoding='utf-8') as f:
                for msg in self.messages:
                    f.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")
            return path
        except Exception as e:
            return f"(transcript save failed: {e})"

    def _do_compact(self) -> str:
        """
        Perform full compaction: save transcript, generate LLM summary,
        replace messages with [system, compressed_summary, ack].
        Returns the summary text.
        """
        self.tui.print_info("🗜️  Compressing conversation context…")
        transcript_path = self._save_transcript()
        self.tui.print_info(f"   Transcript saved → {transcript_path}")

        # Use the last 60 messages as source material for the summary
        recent = self.messages[-60:]
        try:
            summary_prompt = (
                    "You are summarizing an AI coding assistant conversation to free up context space.\n"
                    "Summarize the following conversation concisely. Include:\n"
                    "- The user's overall goal\n"
                    "- Key decisions made\n"
                    "- Files created/modified (with paths)\n"
                    "- Current todo/task state if applicable\n"
                    "- What still needs to be done\n"
                    "Be factual and brief.\n\n"
                    "CONVERSATION:\n"
                    + json.dumps(recent, ensure_ascii=False, default=str)[:80000]
            )
            resp = self.client.chat_completion(
                [{"role": "user", "content": summary_prompt}]
            )
            summary_text = (
                resp.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "(summary unavailable)")
            )
        except Exception as e:
            summary_text = f"(summary generation failed: {e})"

        system_msg = self.messages[0]
        self.messages = [
            system_msg,
            {
                "role": "user",
                "content": (
                    f"<compressed_context>\n{summary_text}\n</compressed_context>\n\n"
                    "The conversation has been compressed to save space. Continue from this point."
                ),
            },
            {
                "role": "assistant",
                "content": "Understood. I have the compressed context and will continue from where we left off.",
            },
        ]
        self.tui.print_success("✅ Context compressed successfully.")
        return summary_text

    def _compress_context(self, force: bool = False) -> bool:
        """Run micro-compact every turn; trigger full auto-compact if we're over budget.
        Returns True if the heavy compaction actually fired."""
        self._micro_compact()
        token_est = _estimate_tokens(self.messages)
        if force or token_est > self.max_context_tokens:
            self._do_compact()
            return True
        return False

    # ── Background Task Notifications ─────────────────────────────────────

    def _drain_background_notifications(self):
        """Pull finished background tasks off the queue and feed them into the
        conversation as a user/assistant pair before the next LLM call."""
        notifs = BG_MANAGER.drain_notifications()
        if not notifs:
            return
        lines = []
        for n in notifs:
            status = "✅" if n["exit_code"] == 0 else "❌"
            lines.append(
                f"{status} Background task [{n['task_id']}] finished "
                f"(exit={n['exit_code']})\n"
                f"   Command: {n['command']!r}\n"
                f"   Output preview: {n['output_preview']}"
            )
        notif_text = "\n\n".join(lines)
        self.messages.append({
            "role": "user",
            "content": f"<background_results>\n{notif_text}\n</background_results>",
        })
        self.messages.append({
            "role": "assistant",
            "content": "Noted. Background task results received.",
        })
        self.tui.print_info(f"📬 {len(notifs)} background task(s) completed.")

    # ── TodoWrite Nag Reminder ────────────────────────────────────────────

    def _maybe_inject_todo_reminder(self):
        """Nudge the LLM to update its todo list if it's been ignoring it for 3+ rounds."""
        NAG_THRESHOLD = 3
        if self.rounds_since_todo < NAG_THRESHOLD:
            return
        if not TODO_MANAGER.has_items() or TODO_MANAGER.pending_count() == 0:
            return
        reminder = (
            "<reminder>You have pending todo items. "
            "Please call todo_write to update task statuses before continuing.</reminder>"
        )
        for msg in reversed(self.messages):
            if msg.get("role") == "tool":
                existing = msg.get("content", "")
                if isinstance(existing, str) and "<reminder>" not in existing:
                    msg["content"] = reminder + "\n" + existing
                return
        self.messages.append({"role": "user", "content": reminder})

    # ── Subagent ──────────────────────────────────────────────────────────

    def _run_subagent(self, prompt: str) -> str:
        """Spin up a sub-agent with its own clean message list.
        Only the final text summary comes back — keeps the main context lean."""
        subagent_tools = [
            t for t in TOOLS_SCHEMA
            if t.get("function", {}).get("name") != "dispatch_task"
        ]
        sub_messages = [{"role": "user", "content": prompt}]
        MAX_SUB_LOOPS = 30

        self.tui.print_info(f"🤖 Subagent spawned: {prompt[:80]}…")

        for _ in range(MAX_SUB_LOOPS):
            try:
                response = self.client.chat_completion(sub_messages, tools=subagent_tools)
            except Exception as e:
                return f"Subagent LLM error: {e}"

            choice = response.get("choices", [{}])[0].get("message", {})
            sub_msg = {
                k: v for k, v in choice.items()
                if k in ("role", "content", "tool_calls") and v is not None
            }
            sub_msg.setdefault("role", "assistant")
            sub_messages.append(sub_msg)

            if not choice.get("tool_calls"):
                result = choice.get("content", "(no summary)")
                self.tui.print_info(f"🤖 Subagent done. Preview: {result[:100]}…")
                return result

            # Execute sub-agent tools silently (no approval)
            for tc in choice["tool_calls"]:
                func = tc["function"]
                name = func["name"]
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except Exception:
                    args = {}
                result_str = execute_tool(name, args)
                sub_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": name,
                    "content": result_str[:50000],
                })

        return "(Subagent reached max loop limit without producing a final response.)"

    # ── Plan Review (Human-in-the-Loop) ──────────────────────────────────

    def _review_plan(self, tool_calls: list) -> tuple:
        """
        Show the planned tool calls and wait for the user's decision.
        Read-only-only batches are silently approved — no UI shown.
        Returns the approved subset and any feedback text the user typed.
        """
        if self.approval_mode in ("auto", "yolo"):
            return tool_calls, None

        # Auto-approve pure read-only batches silently
        if all(tc.get("function", {}).get("name") in _READ_ONLY_TOOLS for tc in tool_calls):
            return tool_calls, None

        self.tui.print_plan(tool_calls)
        answer = self.tui.prompt_plan_input()

        if answer.lower() == 'a':
            self.tui.print_success("已切换为全程自动模式，后续操作不再询问。")
            self.approval_mode = "auto"
            return tool_calls, None
        elif answer.lower() == 'n':
            self.tui.print_warn("操作已拒绝。")
            return [], None
        elif answer == '' or answer.lower() == 'y':
            return tool_calls, None
        elif all(c in '0123456789, ' for c in answer) and any(c.isdigit() for c in answer):
            indices = []
            for part in answer.replace(' ', '').split(','):
                try:
                    idx = int(part) - 1
                    if 0 <= idx < len(tool_calls):
                        indices.append(idx)
                except ValueError:
                    pass
            if indices:
                chosen = [tool_calls[i] for i in sorted(set(indices))]
                self.tui.print_success(f"执行步骤: {[i + 1 for i in sorted(set(indices))]}")
                return chosen, None
            # All entered numbers were out of range — warn and treat as feedback
            self.tui.print_warn(
                f"步骤编号超出范围（共 {len(tool_calls)} 步）。将作为修改意见处理。"
            )
            return [], answer
        else:
            self.tui.print_info("已收到修改意见，重新规划中…")
            return [], answer

    # ── Plan Text Confirmation ────────────────────────────────────────────

    def _is_plan_text(self, text: str) -> bool:
        """Heuristic: check if LLM output looks like a plan that needs confirmation."""
        if not text:
            return False
        text_lower = text.lower()
        plan_markers = [
            "计划", "步骤", "plan", "step 1", "step 2",
            "首先", "然后", "接下来", "最后",
            "1.", "2.", "3.",
            "第一步", "第二步",
            "i will", "i'll",
            "方案", "修改方案",
        ]
        marker_count = sum(1 for m in plan_markers if m in text_lower)
        action_markers = [
            "创建", "修改", "删除", "写入", "添加", "替换",
            "create", "modify", "delete", "write", "add", "replace",
            "update", "edit", "remove", "change",
        ]
        action_count = sum(1 for m in action_markers if m in text_lower)
        return marker_count >= 2 or (marker_count >= 1 and action_count >= 1)

    def _handle_plan_confirmation(self, plan_text: str) -> str | None:
        """
        After LLM outputs a plan text, show it and let user choose to
        continue, reject, or modify.
        Returns: None (continue) | 'reject' | str (feedback)
        """
        if self.approval_mode in ("auto", "yolo"):
            return None
        answer = self.tui.prompt_plan_confirmation(plan_text)
        if answer == "continue":
            return None
        elif answer == "reject":
            return "reject"
        else:
            return answer

    # ── Core Agent Loop ───────────────────────────────────────────────────

    def run_step(self) -> str | None:
        """
        Execute one full agent turn: call LLM → review plan → execute tools
        → repeat until the LLM returns a text response (no tool calls).
        Iterative loop — no recursion.
        """
        self.loop_count = 0

        while True:
            if self.loop_count >= self.max_loops:
                return (
                    f"⚠️  Agent loop limit ({self.max_loops}) reached. "
                    "Please refine your request or increase max_loops in config.json."
                )

            # Layer 1 micro-compact + auto-compact check
            self._compress_context()

            # Drain background task notifications
            self._drain_background_notifications()

            # Inject todo nag reminder if needed
            self._maybe_inject_todo_reminder()

            self.tui.print_separator_thinking()
            self.tui.print_thinking()

            # ── LLM Call ──────────────────────────────────────────────────

            # dispatch_task needs a closure over self, so I append it per-call
            dispatch_tool = {
                "type": "function",
                "function": {
                    "name": "dispatch_task",
                    "description": (
                        "Spawn a sub-agent with a fresh context to handle a complex research "
                        "or multi-file analysis task. The sub-agent runs independently and "
                        "returns only a text summary — keeping the main conversation lean. "
                        "Use for tasks like: 'analyse the entire codebase', "
                        "'find all usages of X across all files', etc."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": "Detailed instructions for the sub-agent."
                            }
                        },
                        "required": ["prompt"]
                    }
                }
            }
            effective_tools = TOOLS_SCHEMA + [dispatch_tool]
            if self.browser_mode:
                effective_tools = effective_tools + BROWSER_TOOLS_SCHEMA + DESKTOP_TOOLS_SCHEMA

            try:
                response = self.client.chat_completion(self.messages, tools=effective_tools)
            except Exception as e:
                self.tui.print_error(f"Error communicating with LLM: {e}")
                return None

            choice = response.get("choices", [{}])[0].get("message", {})
            token_info = self.client.get_last_turn_tokens(response)
            self.tui.print_token_info(token_info)

            assistant_msg = {
                k: v for k, v in choice.items()
                if k in ("role", "content", "tool_calls") and v is not None
            }
            assistant_msg.setdefault("role", "assistant")
            self.messages.append(assistant_msg)

            # ── No tool calls → check if it's a plan needing confirmation ──
            if not choice.get("tool_calls"):
                text = choice.get("content", "")

                if self._is_plan_text(text):
                    self.tui.print_final_response(text)
                    result = self._handle_plan_confirmation(text)

                    if result is None:
                        self.messages.append({
                            "role": "user",
                            "content": "好的，请按照上述计划执行。"
                        })
                        self.loop_count += 1
                        self.rounds_since_todo += 1
                        continue
                    elif result == "reject":
                        return "已拒绝计划。请输入新的指令。"
                    else:
                        self.messages.append({
                            "role": "user",
                            "content": f"[用户修改意见]: {result}"
                        })
                        self.loop_count += 1
                        self.rounds_since_todo += 1
                        continue

                return text

            # ── Tool calls requested ───────────────────────────────────────
            self.loop_count += 1
            self.rounds_since_todo += 1
            all_tool_calls = choice["tool_calls"]

            approved_calls, feedback = self._review_plan(all_tool_calls)

            if feedback:
                self.messages.append({
                    "role": "user",
                    "content": f"[User plan feedback]: {feedback}"
                })
                for tc in all_tool_calls:
                    func_name = tc.get("function", {}).get("name") or "unknown"
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": func_name,
                        "content": "[CANCELLED — user redirected the plan.]"
                    })
                continue

            # Add stub result messages for denied calls to keep the history valid
            approved_ids = {tc["id"] for tc in approved_calls}
            for tc in all_tool_calls:
                if tc["id"] not in approved_ids:
                    func_name = tc.get("function", {}).get("name") or "unknown"
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": func_name,
                        "content": "[DENIED by user. Do not retry this call.]"
                    })

            if not approved_calls:
                self.messages.append({
                    "role": "user",
                    "content": "[All planned actions were denied. Please ask the user what they want to do instead.]"
                })
                continue

            # ── Execute approved tool calls ────────────────────────────────
            self.tui.print_separator_executing(len(approved_calls))

            for tool_call in approved_calls:
                func = tool_call["function"]
                name = func["name"]
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except Exception:
                    args = {}

                preview_args = self._make_args_preview(name, args)
                self.tui.print_tool_call(self.loop_count, self.max_loops, name, preview_args)

                # Handle dispatch_task specially (needs reference to self)
                if name == "dispatch_task":
                    result_str = self._run_subagent(args.get("prompt", ""))
                else:
                    result_str = execute_tool(name, args)

                # Reset nag counter when todo_write is called
                if name == "todo_write":
                    self.rounds_since_todo = 0

                # Refresh system prompt after skill changes
                if name in _SKILL_WRITE_TOOLS:
                    self._refresh_system_prompt()
                    self.tui.print_skills_updated()

                # One-liner summary for reads; truncated preview for writes/exec
                if name in _READ_ONLY_TOOLS:
                    self.tui.print_tool_summary(name, result_str)
                else:
                    preview_result = result_str
                    if len(preview_result) > 200:
                        preview_result = preview_result[:200] + " …"
                    self.tui.print_tool_result(preview_result)

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": name,
                    "content": result_str
                })

    def _make_args_preview(self, name: str, args: dict) -> str:
        """Build a short args preview for the tool-call log line, truncating
        large 'content' fields so they don't flood the terminal."""
        preview_parts = {}
        for k, v in args.items():
            v_str = str(v)
            if k in ("content", "replacement", "instructions", "prompt") and len(v_str) > 60:
                v_str = v_str[:60] + "…"
            elif len(v_str) > 100:
                v_str = v_str[:100] + "…"
            preview_parts[k] = v_str

        result = json.dumps(preview_parts, ensure_ascii=False)
        if len(result) > 120:
            result = result[:120] + "…}"
        return result

    # ── Session Persistence ───────────────────────────────────────────────

    def _default_session_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "session.json")

    def save_session(self, path: str = None) -> str:
        path = path or self._default_session_path()
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.messages, f, ensure_ascii=False, indent=2, default=str)
            return f"💾 Session saved to '{path}' ({len(self.messages)} messages)."
        except Exception as e:
            return f"Error saving session: {e}"

    def load_session(self, path: str = None) -> str:
        path = path or self._default_session_path()
        if not os.path.exists(path):
            return f"No session file found at '{path}'."
        try:
            with open(path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if not isinstance(loaded, list):
                return "Error: Session file is malformed."
            non_system = [m for m in loaded if m.get("role") != "system"]
            self.messages = [self.messages[0]] + non_system
            return f"📂 Session loaded from '{path}' ({len(self.messages)} messages restored)."
        except Exception as e:
            return f"Error loading session: {e}"

    # ── Interactive REPL ──────────────────────────────────────────────────

    def _handle_repl_command(self, cmd: str) -> bool:
        """
        Handle built-in slash commands. Returns True if handled.
        Returns '__EXIT__' string if user wants to exit.
        """
        parts = cmd.strip().split(None, 1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if command in ("/exit", "/quit"):
            return "__EXIT__"

        if command in ("/help", "help"):
            self.tui.print_help()
            return True

        elif command == "/clear":
            self.messages = [self.messages[0]]
            self.loop_count = 0
            self.rounds_since_todo = 0
            self.tui.print_success("Conversation cleared. System prompt preserved.")
            return True

        elif command == "/compact":
            token_est = _estimate_tokens(self.messages)
            self.tui.print_info(
                f"Current context: ~{token_est:,} tokens. Running manual compaction…"
            )
            self._do_compact()
            return True

        elif command == "/history":
            roles = [m.get("role", "?") for m in self.messages]
            role_counts = {}
            for r in roles:
                role_counts[r] = role_counts.get(r, 0) + 1
            summary = ", ".join(f"{v}× {k}" for k, v in role_counts.items())
            token_est = _estimate_tokens(self.messages)
            self.tui.print_info(
                f"History: {len(self.messages)} messages ({summary}) | "
                f"~{token_est:,} tokens estimated"
            )
            return True

        elif command == "/save":
            path = arg.strip() or None
            self.tui.print_info(self.save_session(path))
            return True

        elif command == "/load":
            path = arg.strip() or None
            self.tui.print_info(self.load_session(path))
            return True

        elif command == "/usage":
            self.tui.print_info(self.client.get_usage_summary())
            return True

        elif command == "/todo":
            self.tui.print_info(TODO_MANAGER.render())
            return True

        elif command == "/bg":
            self.tui.print_info(BG_MANAGER.list_all())
            return True

        elif command == "/claw":
            task_desc = arg.strip()
            if not task_desc:
                self.tui.print_warn(
                    "用法: /claw <任务描述>\n"
                    "  例如: /claw 打开百度搜索hello world\n"
                    "  例如: /claw 打开微信给张三发一条消息"
                )
                return True
            self._run_claw_task(task_desc)
            return True

        return False

    # ── Claw Agent Mode (Browser + Desktop) ────────────────────────────────

    _CLAW_SYSTEM_PROMPT = """
== CLAW AGENT MODE (Browser + Desktop Automation) ==

You have TWO sets of automation tools:

### 1. Browser Tools (for web pages)
Use these when the task involves websites, URLs, or web applications:
- `browser_open` — Start browser and navigate to a URL
- `browser_get_elements` — Discover clickable elements on the page
- `browser_click` / `browser_type` / `browser_select` — Interact with web elements
- `browser_screenshot` — Capture the current web page
- `browser_get_text` — Read page content
- `browser_close` — Close the browser when done

### 2. Desktop Tools (for native desktop applications — cross-platform)
Use these when the task involves desktop apps (WeChat, Finder, Notepad, etc.):
- `desktop_open_app` — Open an application by name (cross-platform)
- `desktop_screenshot` — Capture the ENTIRE SCREEN (critical for seeing the UI)
- `desktop_click` / `desktop_double_click` — Click at screen coordinates
- `desktop_type` — Type text (supports Chinese/CJK via clipboard, cross-platform)
- `desktop_hotkey` — Press keyboard shortcuts (macOS: command+c, Linux/Win: ctrl+c)
- `desktop_press_key` — Press a single key (enter, tab, etc.)
- `desktop_move_mouse` — Move mouse to coordinates
- `desktop_scroll` — Scroll at current position
- `desktop_get_mouse_pos` — Check current mouse position
- `desktop_get_screen_size` — Get screen resolution
- `desktop_find_image` — Find an image pattern on screen

### Decision Guide
- Task mentions a URL or website → Use **browser tools**
- Task mentions a desktop app (WeChat, Finder, etc.) → Use **desktop tools**
- You can mix both tool sets if needed

### Desktop Workflow
1. Use `desktop_open_app` to launch the application
2. ALWAYS take `desktop_screenshot` after opening and after each action
3. Analyze the screenshot to find UI elements and determine coordinates
4. Use `desktop_click` at the right coordinates to interact
5. Use `desktop_type` to enter text (supports Chinese)
6. Take another screenshot to verify the result

IMPORTANT:
- For desktop apps, you MUST take screenshots to see where to click!
- Screenshot coordinates are based on screen resolution — use `desktop_get_screen_size` first.
- After each click or type action, take another screenshot to verify it worked.
- `desktop_type` automatically handles CJK text via clipboard + paste.
- Use `desktop_hotkey` with the right modifier for the OS (command on macOS, ctrl on Linux/Win).
"""

    def _run_claw_task(self, task_description: str):
        """Enter claw agent mode: inject browser+desktop prompt, run the task, auto-close."""
        self.tui.print_info("🦀 启动 Claw Agent 模式（浏览器 + 桌面自动化）…")
        self.browser_mode = True

        # Inject the task as a user message
        claw_prompt = (
            f"[Claw Agent Task]\n"
            f"{task_description}\n\n"
            f"You have both browser and desktop automation tools available. "
            f"Choose the right tools based on the task. "
            f"Close the browser when done if you opened one."
        )

        # Temporarily augment the system prompt
        original_system = self.messages[0]["content"]
        self.messages[0]["content"] = original_system + self._CLAW_SYSTEM_PROMPT

        try:
            self.add_user_message(claw_prompt)
            output = self.run_step()
            if output:
                self.tui.print_final_response(output)
        except KeyboardInterrupt:
            self.tui.print_interrupted()
        except Exception as e:
            self.tui.print_error(f"Claw agent error: {e}")
        finally:
            # Auto-close browser if still open
            if BROWSER_SESSION.is_active:
                result = BROWSER_SESSION.close()
                self.tui.print_info(result)
            # Restore original system prompt and exit claw mode
            self.messages[0]["content"] = original_system
            self.browser_mode = False
            self.tui.print_info("🦀 已退出 Claw Agent 模式")

    def start_loop(self):
        """Run the main REPL — blocks until the user exits."""
        model_name = self.client.model
        mode_label = (
            "⚡ AUTO (no approval)" if self.approval_mode in ("auto", "yolo")
            else "🔐 ASK (human review)"
        )
        skills_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
        skill_count = (
            len([f for f in os.listdir(skills_dir) if f.endswith(".md")])
            if os.path.exists(skills_dir) else 0
        )

        self.tui.welcome(model_name, mode_label, skill_count, self.max_loops)

        # Offer to restore last session
        session_path = self._default_session_path()
        if os.path.exists(session_path):
            if self.tui.prompt_yes_no(f"Previous session found at '{session_path}'. Restore?"):
                self.tui.print_info(self.load_session(session_path))

        while True:
            user_input = self.tui.prompt_user_message()

            if user_input is None:
                self.tui.print_goodbye()
                break

            if user_input.lower() in ("exit", "quit"):
                if self.tui.prompt_yes_no("Save session before exiting?"):
                    self.tui.print_info(self.save_session())
                self.tui.print_goodbye()
                break

            if not user_input:
                continue

            if user_input.startswith('/') or user_input.lower() in ("help",):
                result = self._handle_repl_command(user_input)
                if result == "__EXIT__":
                    if self.tui.prompt_yes_no("Save session before exiting?"):
                        self.tui.print_info(self.save_session())
                    self.tui.print_goodbye()
                    break
                if result:
                    continue

            try:
                self.add_user_message(user_input)
                output = self.run_step()

                if output is None:
                    self.tui.print_warn(
                        "No response received (LLM error). "
                        "Check the error above. You can try again or type 'exit'."
                    )
                elif output:
                    self.tui.print_final_response(output)

            except KeyboardInterrupt:
                self.tui.print_interrupted()
            except Exception as e:
                self.tui.print_error(f"Unexpected error: {e}")
