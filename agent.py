import os
import json
import sys
from llm_client import LLMClient
from tools import TOOLS_SCHEMA, execute_tool
from tui import TUI

# Tools that only read/query data — safe to auto-approve without prompting
_READ_ONLY_TOOLS = {
    "read_file", "list_dir", "get_cwd", "find_files", "search_files",
    "get_file_info",
}


class Agent:
    def __init__(self, config: dict, tui: TUI = None):
        self.client = LLMClient(
            api_key=config.get("api_key", ""),
            api_base=config.get("api_base", "https://api.openai.com/v1"),
            model=config.get("model", "gpt-4o"),
            timeout=config.get("timeout", 60),
            max_retries=config.get("max_retries", 3),
        )

        # TUI rendering layer (falls back to plain print if None)
        self.tui = tui or TUI()

        # Limits (overridable via config or CLI)
        self.max_loops = config.get("max_loops", 20)

        # Context size limit in characters
        self.max_context_chars = config.get("max_context_chars", 40000)

        # Approval mode: 'ask' (default) or 'auto' (never ask)
        self.approval_mode = config.get("approval_mode", "ask")

        # Skills injection (can be disabled via --no-skills flag)
        self.load_skills = config.get("load_skills", True)

        # Build the base system prompt (injected once; refreshed after skill creation)
        self.base_system_prompt = self._build_base_prompt(config)

        # Conversation history (starts with system message)
        self.messages = [
            {"role": "system", "content": self._build_dynamic_system_prompt()}
        ]

        # Per-session tool-call counter (reset on each new user message)
        self.loop_count = 0

    # ──────────────────────────────────────────────────────────────────────
    # System Prompt Builders
    # ──────────────────────────────────────────────────────────────────────

    def _build_base_prompt(self, config: dict) -> str:
        user_prompt = config.get(
            "system_prompt",
            "You are MiniCoder, a powerful CLI coding assistant."
        )
        return user_prompt + """
You are MiniCoder, an advanced Agentic CLI programming assistant running locally.
You have the following special capabilities and constraints:
1. EXPLORATION: Use `search_files` to find classes/functions, `find_files` to locate files by pattern, and `list_dir` to explore before rewriting code.
2. EDITING: Do NOT rewrite an entire file if you only need to change a few lines. Use `replace_in_file` for precision edits. Ensure your `target` text exactly matches the existing file content (including spaces and newlines) and is unique. If you must rewrite a small file entirely, use `write_file`.
3. APPENDING: Use `append_to_file` to add content to a file without reading it first (efficient for logs or additive changes).
4. NAVIGATION: Use `get_cwd` to know your current directory, `change_dir` to move between directories. Use `find_files` with glob patterns (e.g. '*.json') to locate files quickly.
5. SKILLS: You can use `create_skill` to save reusable markdown workflows for the future. Check your injected skills before starting a task.
6. SAFETY: You cannot run dangerous commands like `rm -rf`, `del /f /s`, `format`, etc.
7. CONTEXT LIMITS: Do not read generated binaries or huge logs. `read_file` truncates at 30K chars and refuses files > 100 KB.
8. AUTONOMY: Think step by step. If a command fails or a search returns no result, fix your parameters and try again. If you fail 3 times on the same thing, ask the user for help.

== PLANNING RULE (IMPORTANT) ==
For complex tasks that involve multiple file modifications, you MUST:
  a) First output a clear TEXT PLAN describing what you are about to do and why.
  b) The plan should list the steps clearly, e.g. "Step 1: ..., Step 2: ..."
  c) Do NOT include any tool calls in the same response as the plan.
  d) Wait for user confirmation before proceeding with tool calls.

For simple tasks (single file read, quick question, one-step operation), you may proceed directly.

Read-only operations (list_dir, read_file, find_files, search_files, get_cwd) may be executed immediately without a prior text plan.
"""

    def _get_available_skills(self) -> str:
        """Scan the skills directory and inject skill summaries into the prompt."""
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
                # If total skills content is small, inject fully; otherwise summary only
                if total_chars + len(content) < 3000:
                    skills_text.append(f"--- SKILL: {filename} ---\n{content}\n")
                    total_chars += len(content)
                else:
                    # Extract just name + description from frontmatter
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
        """Combines base instructions with dynamically loaded skills."""
        skills_section = self._get_available_skills()
        return f"{self.base_system_prompt}\n\n=== YOUR SKILLS ===\n{skills_section}"

    def _refresh_system_prompt(self):
        """Called to update the system prompt in case a new skill was created."""
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self._build_dynamic_system_prompt()

    # ──────────────────────────────────────────────────────────────────────
    # Message Management
    # ──────────────────────────────────────────────────────────────────────

    def add_user_message(self, text: str):
        """Add a user message and refresh context."""
        self._refresh_system_prompt()
        self.messages.append({"role": "user", "content": text})
        self.loop_count = 0  # Reset per-turn loop counter

    def _prune_context(self):
        """
        Prune old messages to keep the context window manageable.

        Strategy: Always keep the system prompt (index 0) and the last N
        messages. In the prunable zone, remove tool-call PAIRS atomically
        (assistant message with tool_calls + all its corresponding tool results)
        to avoid sending orphaned tool messages to the API.
        """

        def _measure() -> int:
            total = 0
            for m in self.messages:
                total += len(str(m.get("content") or ""))
                total += len(str(m.get("role") or ""))
                total += len(str(m.get("name") or ""))
                total += len(str(m.get("tool_call_id") or ""))
                if m.get("tool_calls"):
                    total += len(json.dumps(m["tool_calls"]))
            return total

        if _measure() <= self.max_context_chars:
            return

        self.tui.print_context_pruning_start(self.max_context_chars)

        # Keep system prompt + last 12 messages as an inviolable tail
        TAIL = 12
        system_msg = self.messages[0]
        tail = self.messages[-TAIL:] if len(self.messages) > TAIL else []
        tail_ids = {id(m) for m in tail}

        # Build prunable segment: everything between system and tail
        prunable = self.messages[1: len(self.messages) - len(tail)]

        # Identify tool-call pair start indices in the prunable segment
        i = 0
        new_prunable = []
        while i < len(prunable):
            msg = prunable[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                # Find all tool results for this batch
                call_ids = {tc["id"] for tc in msg["tool_calls"]}
                j = i + 1
                pair = [msg]
                while j < len(prunable) and prunable[j].get("role") == "tool":
                    if prunable[j].get("tool_call_id") in call_ids:
                        pair.append(prunable[j])
                        j += 1
                    else:
                        break
                # Skip (prune) this pair
                i = j
            else:
                new_prunable.append(msg)
                i += 1

        self.messages = [system_msg] + new_prunable + tail

        after = _measure()
        self.tui.print_context_prune(self.max_context_chars, after)

    # ──────────────────────────────────────────────────────────────────────
    # Plan Review (Human-in-the-Loop)
    # ──────────────────────────────────────────────────────────────────────

    def _review_plan(self, tool_calls: list) -> tuple:
        """
        Show ALL planned tool calls and ask the user for approval
        via an arrow-key selection menu.

        - If ALL calls are read-only they are silently auto-approved.
        - Otherwise show the plan panel and selection menu.

        Returns:
            (approved_tool_calls: list, feedback: str | None)
        """
        if self.approval_mode in ("auto", "yolo"):
            return tool_calls, None

        # Auto-approve pure read-only batches silently
        if all(tc.get("function", {}).get("name") in _READ_ONLY_TOOLS for tc in tool_calls):
            return tool_calls, None

        # Show the plan panel in the TUI (now with selection menu)
        self.tui.print_plan(tool_calls)

        # Wait for the user's response via the selection menu
        answer = self.tui.prompt_plan_input()

        # Always auto
        if answer.lower() == 'a':
            self.tui.print_success("已切换为全程自动模式，后续操作不再询问。")
            self.approval_mode = "auto"
            return tool_calls, None

        # Deny all
        elif answer.lower() == 'n':
            self.tui.print_warn("操作已拒绝。")
            return [], None

        # Approve all (empty / y)
        elif answer == '' or answer.lower() == 'y':
            return tool_calls, None

        # Select specific steps: "1" or "1,3"
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
            # Invalid numbers treated as feedback
            self.tui.print_info("已收到修改意见，重新规划中…")
            return [], answer

        # Free-text feedback → re-plan
        else:
            self.tui.print_info("已收到修改意见，重新规划中…")
            return [], answer

    # ──────────────────────────────────────────────────────────────────────
    # Plan Text Confirmation (for when LLM outputs a text plan)
    # ──────────────────────────────────────────────────────────────────────

    def _is_plan_text(self, text: str) -> bool:
        """Heuristic: check if LLM output looks like a plan that needs confirmation."""
        if not text:
            return False
        text_lower = text.lower()
        # Check for plan-like markers
        plan_markers = [
            "计划", "步骤", "plan", "step 1", "step 2",
            "首先", "然后", "接下来", "最后",
            "1.", "2.", "3.",
            "第一步", "第二步",
            "i will", "i'll",
            "方案", "修改方案",
        ]
        marker_count = sum(1 for m in plan_markers if m in text_lower)
        # Also check for action verbs suggesting file modifications
        action_markers = [
            "创建", "修改", "删除", "写入", "添加", "替换",
            "create", "modify", "delete", "write", "add", "replace",
            "update", "edit", "remove", "change",
        ]
        action_count = sum(1 for m in action_markers if m in text_lower)
        # Consider it a plan if it has enough markers
        return marker_count >= 2 or (marker_count >= 1 and action_count >= 1)

    def _handle_plan_confirmation(self, plan_text: str) -> str | None:
        """
        After LLM outputs a plan text, show it and let user choose to
        continue, reject, or modify.

        Returns:
            - None: continue executing (inject "请按计划执行")
            - "reject": user rejected
            - str: user's modification feedback
        """
        if self.approval_mode in ("auto", "yolo"):
            return None  # auto-continue

        answer = self.tui.prompt_plan_confirmation(plan_text)

        if answer == "continue":
            return None  # continue
        elif answer == "reject":
            return "reject"
        else:
            # Custom text / modification feedback
            return answer

    # ──────────────────────────────────────────────────────────────────────
    # Core Agent Loop (Iterative — no recursion)
    # ──────────────────────────────────────────────────────────────────────

    def run_step(self) -> str | None:
        """
        Execute one full agent turn: call LLM → review plan → execute tools
        → repeat until the LLM returns a text response (no tool calls).

        Uses an iterative loop instead of recursion to avoid stack overflows
        on deep tool chains.
        """
        self.loop_count = 0

        while True:
            if self.loop_count >= self.max_loops:
                msg = (
                    f"⚠️  Agent loop limit ({self.max_loops}) reached. "
                    "Please refine your request or increase max_loops in config.json."
                )
                return msg

            self._prune_context()
            self.tui.print_separator_thinking()
            self.tui.print_thinking()

            # ── LLM Call ─────────────────────────────────────────────────
            try:
                response = self.client.chat_completion(self.messages, tools=TOOLS_SCHEMA)
            except Exception as e:
                self.tui.print_error(f"Error communicating with LLM: {e}")
                return None

            choice = response.get("choices", [{}])[0].get("message", {})

            # Print per-call token usage if available
            token_info = self.client.get_last_turn_tokens(response)
            self.tui.print_token_info(token_info)

            # Build assistant message (only include non-None keys)
            assistant_msg = {
                k: v for k, v in choice.items()
                if k in ("role", "content", "tool_calls") and v is not None
            }
            assistant_msg.setdefault("role", "assistant")
            self.messages.append(assistant_msg)

            # ── No tool calls → check if it's a plan needing confirmation ──
            if not choice.get("tool_calls"):
                text = choice.get("content", "")

                # Check if this looks like a plan that needs user confirmation
                if self._is_plan_text(text):
                    # Show the plan text first via Markdown rendering
                    self.tui.print_final_response(text)

                    # Ask user what to do
                    result = self._handle_plan_confirmation(text)

                    if result is None:
                        # User chose "continue" → inject approval and re-loop
                        self.messages.append({
                            "role": "user",
                            "content": "好的，请按照上述计划执行。"
                        })
                        self.loop_count += 1
                        continue
                    elif result == "reject":
                        return "已拒绝计划。请输入新的指令。"
                    else:
                        # User gave modification feedback
                        self.messages.append({
                            "role": "user",
                            "content": f"[用户修改意见]: {result}"
                        })
                        self.loop_count += 1
                        continue

                return text

            # ── Tool calls requested ──────────────────────────────────────
            self.loop_count += 1
            all_tool_calls = choice["tool_calls"]

            approved_calls, feedback = self._review_plan(all_tool_calls)

            if feedback:
                # User redirected the plan — inject feedback and re-loop
                self.messages.append({
                    "role": "user",
                    "content": f"[User plan feedback]: {feedback}"
                })
                # Inject denial stubs for all original calls
                for tc in all_tool_calls:
                    func_name = tc.get("function", {}).get("name") or "unknown"
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": func_name,
                        "content": "[CANCELLED — user redirected the plan.]"
                    })
                continue

            # Inject denial stubs for non-approved calls
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
                # Entire plan denied — ask LLM to recover
                self.messages.append({
                    "role": "user",
                    "content": "[All planned actions were denied. Please ask the user what they want to do instead.]"
                })
                continue

            # ── Execute approved tool calls ───────────────────────────────
            self.tui.print_separator_executing(len(approved_calls))

            for tool_call in approved_calls:
                func = tool_call["function"]
                name = func["name"]
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except Exception:
                    args = {}

                # Concise args preview: hide large content values
                preview_args = self._make_args_preview(name, args)
                self.tui.print_tool_call(self.loop_count, self.max_loops, name, preview_args)

                result_str = execute_tool(name, args)

                if name == "create_skill":
                    self._refresh_system_prompt()
                    self.tui.print_skills_updated()

                # Smart result display: read-only → summary; write/exec → preview
                if name in _READ_ONLY_TOOLS:
                    self.tui.print_tool_summary(name, result_str)
                else:
                    preview_result = result_str
                    if len(preview_result) > 200:
                        preview_result = preview_result[:200] + " ..."
                    self.tui.print_tool_result(preview_result)

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": name,
                    "content": result_str
                })

            # Loop continues: send results back to LLM

    def _make_args_preview(self, name: str, args: dict) -> str:
        """Create a concise args preview, hiding large content for write tools."""
        preview_parts = {}
        for k, v in args.items():
            v_str = str(v)
            # For write tools, truncate the content arg more aggressively
            if k in ("content", "replacement", "instructions") and len(v_str) > 60:
                v_str = v_str[:60] + "…"
            elif len(v_str) > 100:
                v_str = v_str[:100] + "…"
            preview_parts[k] = v_str

        result = json.dumps(preview_parts, ensure_ascii=False)
        if len(result) > 120:
            result = result[:120] + "…}"
        return result

    # ──────────────────────────────────────────────────────────────────────
    # Session Persistence
    # ──────────────────────────────────────────────────────────────────────

    def _default_session_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "session.json")

    def save_session(self, path: str = None) -> str:
        """Serialize conversation history to a JSON file."""
        path = path or self._default_session_path()
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.messages, f, ensure_ascii=False, indent=2)
            return f"💾 Session saved to '{path}' ({len(self.messages)} messages)."
        except Exception as e:
            return f"Error saving session: {e}"

    def load_session(self, path: str = None) -> str:
        """Restore conversation history from a JSON file."""
        path = path or self._default_session_path()
        if not os.path.exists(path):
            return f"No session file found at '{path}'."
        try:
            with open(path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if not isinstance(loaded, list):
                return "Error: Session file is malformed."
            # Replace messages but keep current system prompt
            non_system = [m for m in loaded if m.get("role") != "system"]
            self.messages = [self.messages[0]] + non_system
            return f"📂 Session loaded from '{path}' ({len(self.messages)} messages restored)."
        except Exception as e:
            return f"Error loading session: {e}"

    # ──────────────────────────────────────────────────────────────────────
    # Interactive REPL
    # ──────────────────────────────────────────────────────────────────────

    def _handle_repl_command(self, cmd: str) -> bool:
        """
        Handle built-in slash commands. Returns True if the input was
        a command (so the caller should skip sending it to the LLM).
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
            # Keep system prompt, clear everything else
            self.messages = [self.messages[0]]
            self.loop_count = 0
            self.tui.print_success("Conversation cleared. System prompt preserved.")
            return True

        elif command == "/history":
            roles = [m.get("role", "?") for m in self.messages]
            role_counts = {}
            for r in roles:
                role_counts[r] = role_counts.get(r, 0) + 1
            summary = ", ".join(f"{v}× {k}" for k, v in role_counts.items())
            self.tui.print_info(f"History: {len(self.messages)} messages ({summary})")
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

        return False  # Not a built-in command

    def start_loop(self):
        """Start the interactive REPL loop."""
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

        # Offer to restore last session if one exists
        session_path = self._default_session_path()
        if os.path.exists(session_path):
            if self.tui.prompt_yes_no(f"Previous session found at '{session_path}'. Restore?"):
                self.tui.print_info(self.load_session(session_path))

        while True:
            user_input = self.tui.prompt_user_message()

            if user_input is None:
                # EOFError / KeyboardInterrupt in prompt
                self.tui.print_goodbye()
                break

            if user_input.lower() in ("exit", "quit"):
                # Offer to save on exit
                if self.tui.prompt_yes_no("Save session before exiting?"):
                    self.tui.print_info(self.save_session())
                self.tui.print_goodbye()
                break

            if not user_input:
                continue

            # Handle built-in commands
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
