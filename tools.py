import os
import json
import fnmatch
import difflib
import threading
import subprocess
import uuid
import time
from datetime import datetime


# ---------------------------------------------------------------------------
# TodoManager — Structured Task Tracking
# ---------------------------------------------------------------------------

class TodoManager:
    """
    Tracks a structured task list with statuses: pending | in_progress | done.
    Only one task can be `in_progress` at a time (enforces sequential focus).
    """

    def __init__(self):
        self.items: list[dict] = []

    def update(self, items: list) -> str:
        """
        Replace the full task list. Validates that only one item is in_progress.
        Each item: {id: str, text: str, status: 'pending'|'in_progress'|'done'}
        """
        validated = []
        in_progress_count = 0
        for item in items:
            item_id = str(item.get("id", "")).strip()
            text = str(item.get("text", "")).strip()
            status = str(item.get("status", "pending")).strip()
            if status not in ("pending", "in_progress", "done"):
                status = "pending"
            if status == "in_progress":
                in_progress_count += 1
            if in_progress_count > 1:
                return "Error: Only one task can be 'in_progress' at a time."
            validated.append({"id": item_id, "text": text, "status": status})
        self.items = validated
        return self.render()

    def render(self) -> str:
        """Returns a formatted string of the current task list."""
        if not self.items:
            return "📋 Todo list is empty."
        icons = {"pending": "⬜", "in_progress": "🔄", "done": "✅"}
        lines = ["📋 Current Tasks:"]
        for item in self.items:
            icon = icons.get(item["status"], "⬜")
            lines.append(f"  {icon} [{item['id']}] {item['text']}")
        return "\n".join(lines)

    def has_items(self) -> bool:
        return bool(self.items)

    def pending_count(self) -> int:
        return sum(1 for i in self.items if i["status"] != "done")


# Module-level singleton shared by the tool functions and Agent
TODO_MANAGER = TodoManager()


def todo_write(items: list) -> str:
    """
    Update the structured task list. Use this to plan, track and reflect
    progress across multi-step tasks.
    
    Each item must have: id (str), text (str), status ('pending'|'in_progress'|'done').
    Only one item can be 'in_progress' at a time.
    """
    return TODO_MANAGER.update(items)


# ---------------------------------------------------------------------------
# BackgroundManager — Background Task Execution
# ---------------------------------------------------------------------------

class BackgroundManager:
    """
    Long-running commands go here instead of run_command so they don't block
    the main thread. Results land in a notification queue that the agent drains
    before each LLM call.
    """

    def __init__(self):
        self._tasks: dict[str, dict] = {}
        self._notification_queue: list[dict] = []
        self._lock = threading.Lock()

    def run(self, command: str, timeout: int = 300) -> str:
        """Launch a command in a background daemon thread. Returns immediately."""
        task_id = str(uuid.uuid4())[:8]
        with self._lock:
            self._tasks[task_id] = {
                "status": "running",
                "command": command,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "output": None,
                "exit_code": None,
            }
        thread = threading.Thread(
            target=self._execute,
            args=(task_id, command, timeout),
            daemon=True,
        )
        thread.start()
        return f"🚀 Background task [{task_id}] started: {command!r}"

    def _execute(self, task_id: str, command: str, timeout: int):
        """Worker that runs in a daemon thread and stores the result when done."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            output = (result.stdout + result.stderr).strip()
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            output = f"⏰ Timeout after {timeout}s"
            exit_code = -1
        except Exception as e:
            output = f"Error: {e}"
            exit_code = -2

        # Cap output so a noisy command doesn't bloat the notification payload
        if len(output) > 4000:
            output = output[:4000] + "\n...[truncated]"

        with self._lock:
            self._tasks[task_id].update({
                "status": "done",
                "output": output,
                "exit_code": exit_code,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            })
            self._notification_queue.append({
                "task_id": task_id,
                "command": command,
                "exit_code": exit_code,
                "output_preview": output[:300],
            })

    def check(self, task_id: str) -> str:
        """Look up a background task's current status and output by ID."""
        with self._lock:
            task = self._tasks.get(task_id)
        if not task:
            ids = list(self._tasks.keys())
            return f"Unknown task ID '{task_id}'. Known IDs: {ids}"
        if task["status"] == "running":
            return (
                f"⏳ Task [{task_id}] is still running.\n"
                f"   Command: {task['command']!r}\n"
                f"   Started: {task['started_at']}"
            )
        return (
            f"✅ Task [{task_id}] finished (exit={task['exit_code']})\n"
            f"   Command: {task['command']!r}\n"
            f"   Started:  {task['started_at']}\n"
            f"   Finished: {task.get('finished_at', '?')}\n"
            f"--- Output ---\n{task['output'] or '(no output)'}"
        )

    def drain_notifications(self) -> list[dict]:
        """Take all pending completion notifications off the queue and return them."""
        with self._lock:
            notifs = list(self._notification_queue)
            self._notification_queue.clear()
        return notifs

    def list_all(self) -> str:
        """List all background tasks and their statuses."""
        with self._lock:
            tasks = dict(self._tasks)
        if not tasks:
            return "No background tasks have been started."
        lines = ["Background tasks:"]
        for tid, info in tasks.items():
            status_icon = "⏳" if info["status"] == "running" else "✅"
            lines.append(
                f"  {status_icon} [{tid}] {info['command']!r} — {info['status']}"
            )
        return "\n".join(lines)


# Global singleton
BG_MANAGER = BackgroundManager()


def run_background(command: str, timeout: int = 300) -> str:
    """
    Run a shell command asynchronously in the background.
    Returns immediately with a task_id. Use check_background(task_id) to poll.
    The agent will be automatically notified when the task completes.
    """
    return BG_MANAGER.run(command, timeout)


def check_background(task_id: str) -> str:
    """
    Check the status and output of a previously started background task.
    Use 'all' as task_id to list every background task.
    """
    if task_id.lower() == "all":
        return BG_MANAGER.list_all()
    return BG_MANAGER.check(task_id)


# ---------------------------------------------------------------------------
# File I/O Tools
# ---------------------------------------------------------------------------

def read_file(path: str, start_line: int = None, end_line: int = None) -> str:
    """Read contents of a file. Optionally specify a line range (1-indexed, inclusive).
    
    When reading large files, use get_file_info first to check line count,
    then read specific sections with start_line/end_line to avoid truncation.
    """
    try:
        if not os.path.exists(path):
            return f"Error: File '{path}' does not exist."

        # If line range requested, bypass the 100KB size limit
        if start_line is not None or end_line is not None:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                all_lines = f.readlines()

            total = len(all_lines)
            s = max(1, start_line or 1)
            e = min(total, end_line or total)

            if s > total:
                return f"Error: start_line {s} exceeds file length ({total} lines)."

            selected = all_lines[s - 1:e]
            content = "".join(selected)
            header = f"[Lines {s}–{e} of {total} total]\n"

            if len(content) > 30000:
                content = content[:30000] + "\n...[TRUNCATED — range too large, narrow the line range]"
            return header + content

        # Full-file read: apply size and content limits
        file_size = os.path.getsize(path)
        if file_size > 100 * 1024:
            return (
                f"Error: File '{path}' is too large ({file_size:,} bytes). "
                "Max allowed for full read is 100 KB. "
                "Use get_file_info to check line count, then read with start_line/end_line."
            )

        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        if len(content) > 30000:
            return (
                    f"[Content truncated — full length {len(content):,} chars. "
                    f"Showing first 30,000 chars]\n"
                    + content[:30000]
                    + "\n...[TRUNCATED — use start_line/end_line for the rest]"
            )
        return content
    except Exception as e:
        return f"Error reading file '{path}': {e}"


def get_file_info(path: str) -> str:
    """Return metadata about a file: size, line count, encoding hint, last modified.
    
    Use this before read_file on large files to plan which line ranges to read.
    """
    try:
        if not os.path.exists(path):
            return f"Error: File '{path}' does not exist."
        if os.path.isdir(path):
            return f"Error: '{path}' is a directory, not a file."

        stat = os.stat(path)
        size_bytes = stat.st_size
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

        # Count lines without loading everything into memory
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                line_count = sum(1 for _ in f)
            encoding = "utf-8"
        except Exception:
            line_count = None
            encoding = "binary/unknown"

        lines_str = f"{line_count:,}" if line_count is not None else "N/A (binary)"
        return (
            f"📄 File: {path}\n"
            f"   Size:          {_fmt_size(size_bytes)} ({size_bytes:,} bytes)\n"
            f"   Lines:         {lines_str}\n"
            f"   Encoding:      {encoding}\n"
            f"   Last modified: {mtime}"
        )
    except Exception as e:
        return f"Error getting file info for '{path}': {e}"


def write_file(path: str, content: str) -> str:
    """Write entire contents to a file (overwrite). Creates parent dirs as needed."""
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"✅ Successfully wrote {len(content):,} characters to '{path}'."
    except Exception as e:
        return f"Error writing file '{path}': {e}"


def append_to_file(path: str, content: str) -> str:
    """Append text to an existing file (or create it if it doesn't exist)."""
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(content)
        return f"✅ Appended {len(content):,} characters to '{path}'."
    except Exception as e:
        return f"Error appending to file '{path}': {e}"


def replace_in_file(path: str, target: str, replacement: str) -> str:
    """Precision editing: replaces an EXACT string match in a file.

    If the target is not found, returns a fuzzy-match hint showing the closest
    existing lines to help identify the correct target string.
    """
    try:
        if not os.path.exists(path):
            return f"Error: File '{path}' not found."

        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        occurrences = content.count(target)
        if occurrences == 0:
            target_lines = target.strip().splitlines()
            file_lines = content.splitlines()
            hint_lines = []
            for tl in target_lines[:3]:
                matches = difflib.get_close_matches(tl, file_lines, n=2, cutoff=0.5)
                if matches:
                    hint_lines.extend(matches)
            hint = ""
            if hint_lines:
                hint = (
                        "\n\nFuzzy-match hint — closest existing lines:\n"
                        + "\n".join(f"  | {ln}" for ln in hint_lines)
                        + "\n\nCheck exact whitespace, indentation, and newlines."
                )
            return (
                    f"Error: Target text not found in '{path}'. "
                    f"Please ensure the 'target' matches the file exactly (spaces, tabs, newlines)."
                    + hint
            )
        elif occurrences > 1:
            return (
                f"Error: Target text found {occurrences} times in '{path}'. "
                "Must be a unique match. Add more surrounding context to make it unique."
            )

        new_content = content.replace(target, replacement, 1)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        return f"✅ Successfully replaced target block in '{path}'."

    except Exception as e:
        return f"Error applying replace in '{path}': {e}"


# ---------------------------------------------------------------------------
# Directory / Search Tools
# ---------------------------------------------------------------------------

def list_dir(path: str) -> str:
    """List directory contents with file sizes and type labels. Caps at 200 entries."""
    try:
        if not os.path.exists(path):
            return f"Error: Path '{path}' does not exist."
        if not os.path.isdir(path):
            return f"Error: '{path}' is a file, not a directory."

        entries = os.listdir(path)
        if not entries:
            return "Directory is empty."

        dirs = sorted([e for e in entries if os.path.isdir(os.path.join(path, e))])
        files = sorted([e for e in entries if os.path.isfile(os.path.join(path, e))])
        ordered = dirs + files

        lines = []
        for name in ordered[:200]:
            full = os.path.join(path, name)
            if os.path.isdir(full):
                lines.append(f"[DIR]  {name}/")
            else:
                try:
                    size = os.path.getsize(full)
                    size_str = _fmt_size(size)
                except OSError:
                    size_str = "?"
                lines.append(f"[FILE] {name}  ({size_str})")

        result = "\n".join(lines)
        if len(ordered) > 200:
            result += f"\n... and {len(ordered) - 200} more entries (truncated)."
        return result
    except Exception as e:
        return f"Error listing directory '{path}': {e}"


def find_files(directory: str, pattern: str) -> str:
    """Find files matching a glob pattern (e.g. '*.json', '**/*.py') under a directory.

    Returns up to 100 matches with relative paths.
    """
    try:
        if not os.path.exists(directory):
            return f"Error: Directory '{directory}' does not exist."

        matches = []
        for root, dirs, files in os.walk(directory):
            dirs[:] = [
                d for d in dirs
                if not d.startswith('.') and d not in ('__pycache__', 'node_modules', '.git', 'venv', '.venv')
            ]
            for filename in files:
                if fnmatch.fnmatch(filename, pattern):
                    rel = os.path.relpath(os.path.join(root, filename), directory)
                    matches.append(rel)

        if not matches:
            return f"No files matching '{pattern}' found in '{directory}'."

        matches.sort()
        result = f"Found {len(matches)} file(s) matching '{pattern}':\n"
        shown = matches[:100]
        result += "\n".join(f"  {m}" for m in shown)
        if len(matches) > 100:
            result += f"\n... and {len(matches) - 100} more (truncated)."
        return result
    except Exception as e:
        return f"Error finding files: {e}"


def search_files(directory: str, query: str, extensions: list = None) -> str:
    """Grep-like search across files in a directory.

    Args:
        directory: Root directory to search.
        query: Text to search for (case-sensitive).
        extensions: List of file extensions to include, e.g. ['py', 'md'].
                    Defaults to common text file extensions if not specified.
    """
    try:
        if extensions is None:
            extensions = [
                'py', 'js', 'ts', 'jsx', 'tsx', 'html', 'css', 'json',
                'md', 'txt', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'sh',
                'bat', 'ps1', 'java', 'cpp', 'c', 'h', 'go', 'rs', 'rb',
                'php', 'swift', 'kt', 'sql', 'xml', 'env'
            ]
        ext_set = {e.lstrip('.').lower() for e in extensions}

        results = []
        for root, dirs, files in os.walk(directory):
            dirs[:] = [
                d for d in dirs
                if not d.startswith('.') and d not in ('__pycache__', 'node_modules', '.git', 'venv', '.venv')
            ]
            for filename in files:
                file_ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
                if file_ext not in ext_set:
                    continue
                filepath = os.path.join(root, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                        for i, line in enumerate(f, 1):
                            if query in line:
                                results.append(f"{filepath}:{i}: {line.rstrip()}")
                                if len(results) >= 100:
                                    break
                    if len(results) >= 100:
                        break
                except (PermissionError, OSError):
                    pass

        if not results:
            return f"No matches found for '{query}' in '{directory}'."

        output = "\n".join(results)
        if len(results) == 100:
            output += "\n...(output capped at 100 matches; refine your query to see more)."
        return output
    except Exception as e:
        return f"Error searching files: {e}"


def get_cwd() -> str:
    """Return the current working directory."""
    try:
        return os.getcwd()
    except Exception as e:
        return f"Error getting current directory: {e}"


# ---------------------------------------------------------------------------
# Shell Tool
# ---------------------------------------------------------------------------

# Commands I refuse to run — they could cause irreversible damage
_DANGEROUS_PATTERNS = [
    'rm -rf', 'rm -r /', 'format ',
    'mkfs', 'dd if=', 'shred',
    'del /f /s', 'del /f/s', 'rmdir /s',
    'shutdown', 'reboot', 'halt', 'poweroff',
    'DROP TABLE', 'DROP DATABASE',
    ':(){ :|:& };:',  # fork bomb
    'chmod -R 777 /', 'chown -R',
    'truncate -s 0',
    '> /dev/sda',
]


def run_command(command: str, timeout: int = 60) -> str:
    """Run a shell command with safety checks and output limits.

    Args:
        command: The shell command string.
        timeout: Max seconds before killing the process (default 60).
    """
    cmd_lower = command.lower()
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.lower() in cmd_lower:
            return (
                f"🚫 Safety Error: Command contains forbidden pattern '{pattern}'. "
                "Execution blocked to prevent irreversible damage."
            )

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=timeout,
            encoding='utf-8',
            errors='replace',
        )

        output = result.stdout or ""
        if result.stderr:
            output += "\n[STDERR]:\n" + result.stderr

        if result.returncode != 0 and not output.strip():
            output = f"[Process exited with code {result.returncode} and no output]"

        if not output.strip():
            return f"Command executed successfully (exit code {result.returncode}) with no output."

        if len(output) > 10000:
            return (
                    f"[Output truncated — full length {len(output):,} chars. Showing last 10,000 chars]\n"
                    + output[-10000:]
            )

        return output.strip()
    except subprocess.TimeoutExpired:
        return f"⏰ Error: Command timed out after {timeout} seconds."
    except Exception as e:
        return f"Error executing command: {e}"


def change_dir(path: str) -> str:
    """Change the current working directory."""
    try:
        os.chdir(path)
        return f"Changed directory to: {os.getcwd()}"
    except FileNotFoundError:
        return f"Error: Directory '{path}' does not exist."
    except PermissionError:
        return f"Error: Permission denied to access '{path}'."
    except Exception as e:
        return f"Error changing directory to '{path}': {e}"


# ---------------------------------------------------------------------------
# Skills Tools
# ---------------------------------------------------------------------------

def _get_skills_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")


def create_skill(name: str, description: str, instructions: str) -> str:
    """Create a new reusable skill/workflow note and save it to the skills directory."""
    try:
        skills_dir = _get_skills_dir()
        os.makedirs(skills_dir, exist_ok=True)

        safe_name = "".join(c if c.isalnum() or c in ' _-' else '_' for c in name)
        filename = f"{safe_name.lower().replace(' ', '_')}.md"
        path = os.path.join(skills_dir, filename)

        skill_content = (
            f"---\nname: {name}\ndescription: {description}\n---\n\n"
            f"## Instructions\n{instructions}"
        )

        with open(path, 'w', encoding='utf-8') as f:
            f.write(skill_content)

        return f"✅ Skill '{name}' saved to '{path}'. It will be loaded in future sessions."
    except Exception as e:
        return f"Error creating skill: {e}"


def list_skills() -> str:
    """List all saved skills with their names and descriptions."""
    try:
        skills_dir = _get_skills_dir()
        if not os.path.exists(skills_dir):
            return "No skills directory found. No skills have been created yet."

        md_files = sorted([f for f in os.listdir(skills_dir) if f.endswith(".md")])
        if not md_files:
            return "No skills saved yet. Use create_skill to add one."

        lines = [f"📚 Saved skills ({len(md_files)} total):"]
        for filename in md_files:
            filepath = os.path.join(skills_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                # Extract name and description from YAML frontmatter
                name = filename
                description = ""
                for line in content.splitlines():
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip()
                    elif line.startswith("---") and description:
                        break
                lines.append(f"  📄 {filename}")
                lines.append(f"     Name: {name}")
                if description:
                    lines.append(f"     Desc: {description}")
            except Exception:
                lines.append(f"  📄 {filename} (could not read)")

        return "\n".join(lines)
    except Exception as e:
        return f"Error listing skills: {e}"


def delete_skill(name: str) -> str:
    """Delete a saved skill by its filename (without .md) or display name.
    
    Args:
        name: The skill filename (e.g. 'deploy_frontend') or a substring to match.
    """
    try:
        skills_dir = _get_skills_dir()
        if not os.path.exists(skills_dir):
            return "No skills directory found."

        md_files = [f for f in os.listdir(skills_dir) if f.endswith(".md")]

        # Try exact match first (with or without .md)
        target = name if name.endswith(".md") else name + ".md"
        if target in md_files:
            os.remove(os.path.join(skills_dir, target))
            return f"✅ Skill '{target}' deleted."

        # Try substring match
        matches = [f for f in md_files if name.lower() in f.lower()]
        if len(matches) == 1:
            os.remove(os.path.join(skills_dir, matches[0]))
            return f"✅ Skill '{matches[0]}' deleted."
        elif len(matches) > 1:
            return (
                f"Ambiguous: '{name}' matches multiple skills: {matches}. "
                "Please provide a more specific name."
            )
        else:
            return (
                f"Skill '{name}' not found. "
                f"Available skills: {md_files}"
            )
    except Exception as e:
        return f"Error deleting skill: {e}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_size(n: int) -> str:
    """Format byte size as human-readable string."""
    if n < 1024:
        return f"{n} B"
    elif n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    elif n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    else:
        return f"{n / 1024 ** 3:.1f} GB"


# ---------------------------------------------------------------------------
# Tool Schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOLS_SCHEMA = [
    # ── Todo / Task Tracking ────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": (
                "Update the structured task list. Use at the start of multi-step tasks to plan, "
                "and after each step to mark progress. "
                "Statuses: 'pending' | 'in_progress' | 'done'. "
                "Only ONE task can be 'in_progress' at a time. "
                "Always keep the list current so you and the user stay in sync."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "Complete replacement task list.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Unique short identifier, e.g. '1', '2a'."},
                                "text": {"type": "string", "description": "Task description."},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "done"]}
                            },
                            "required": ["id", "text", "status"]
                        }
                    }
                },
                "required": ["items"]
            }
        }
    },

    # ── File I/O ─────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_file_info",
            "description": (
                "Get metadata about a file: size, total line count, encoding, and last modified time. "
                "Use this BEFORE read_file on large files to know how many lines there are, "
                "then use start_line/end_line to read only what you need."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative path to the file."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Reads the text content of a file. "
                "For large files (>100 KB), use get_file_info first, then read with start_line/end_line. "
                "Line numbers are 1-indexed and inclusive."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative path to the file."},
                    "start_line": {"type": "integer", "description": "First line to read (1-indexed). Optional."},
                    "end_line": {"type": "integer",
                                 "description": "Last line to read (1-indexed, inclusive). Optional."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Writes COMPLETE file content (overwrites the entire file). "
                "Use for new files or complete rewrites. "
                "For surgical edits, prefer replace_in_file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write to."},
                    "content": {"type": "string", "description": "Full file content to write."}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "append_to_file",
            "description": (
                "Appends text to the end of a file (creates the file if it doesn't exist). "
                "Efficient for adding log entries or new content without reading the whole file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to append to."},
                    "content": {"type": "string", "description": "Text to append."}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": (
                "Precision edit: replaces an EXACT, UNIQUE block of text in a file. "
                "The 'target' must match exactly (including whitespace/indentation). "
                "Returns a fuzzy-match hint if the target is not found. "
                "Prefer this over write_file for surgical edits."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "target": {"type": "string", "description": "Exact text to find and replace."},
                    "replacement": {"type": "string", "description": "New text to substitute in."}
                },
                "required": ["path", "target", "replacement"]
            }
        }
    },

    # ── Directory / Search ───────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": (
                "Lists directory contents with file sizes and [DIR]/[FILE] labels. "
                "Directories are shown first. Caps at 200 entries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path to list."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": (
                "Find files by glob pattern under a directory (e.g. '*.json', '*.py'). "
                "Skips hidden folders, __pycache__, node_modules, .git. Returns up to 100 matches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Root directory to search in."},
                    "pattern": {"type": "string", "description": "Glob filename pattern, e.g. '*.json'."}
                },
                "required": ["directory", "pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "Grep-like search for a text query across files in a directory. "
                "By default searches all common text file types. "
                "Optionally filter by extensions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Root directory to search."},
                    "query": {"type": "string", "description": "Text string to search for."},
                    "extensions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional list of file extensions to include, e.g. ['py', 'md']. "
                            "Defaults to all common text types."
                        )
                    }
                },
                "required": ["directory", "query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_cwd",
            "description": "Returns the current working directory of the agent process.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },

    # ── Shell ────────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command synchronously and capture stdout/stderr. "
                "Dangerous commands (rm -rf, format, del /f /s, etc.) are blocked. "
                "Default timeout is 60 seconds. "
                "For long-running commands (installs, builds, tests), use run_background instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command string to execute."},
                    "timeout": {
                        "type": "integer",
                        "description": "Max seconds before killing the process. Default 60."
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_background",
            "description": (
                "Run a shell command ASYNCHRONOUSLY in the background. Returns immediately with a task_id. "
                "Ideal for long-running operations (npm install, pytest, docker build, etc.) "
                "so the agent can continue working. "
                "You will be automatically notified when the task completes. "
                "Use check_background(task_id) to poll status manually."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run in the background."},
                    "timeout": {
                        "type": "integer",
                        "description": "Max seconds before killing the process. Default 300."
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_background",
            "description": (
                "Check the status and output of a background task by its task_id. "
                "Use 'all' as task_id to list all background tasks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Task ID returned by run_background, or 'all' to list all tasks."
                    }
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "change_dir",
            "description": "Change the current working directory for subsequent commands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path to switch to."}
                },
                "required": ["path"]
            }
        }
    },

    # ── Skills ───────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "create_skill",
            "description": (
                "Saves a permanent, reusable skill/workflow note to the skills/ directory. "
                "Use when the user says 'remember how to X' or 'create a workflow for Y'. "
                "Skills are automatically loaded at the start of every session."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short descriptive name, e.g. 'Deploy Frontend'."},
                    "description": {"type": "string", "description": "Brief explanation of when to use this skill."},
                    "instructions": {"type": "string", "description": "Detailed Markdown step-by-step instructions."}
                },
                "required": ["name", "description", "instructions"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "List all saved skills with their filenames, names, and descriptions.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_skill",
            "description": (
                "Delete a saved skill by filename (without .md) or display name. "
                "Use list_skills first to see available skill names."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill filename (e.g. 'deploy_frontend') or substring to match."
                    }
                },
                "required": ["name"]
            }
        }
    },
]


# ---------------------------------------------------------------------------
# Tool Dispatcher
# ---------------------------------------------------------------------------

def execute_tool(name: str, kwargs: dict) -> str:
    """Route a tool call by name. Unknown names and bad args both return error strings."""
    dispatch = {
        # Todo
        "todo_write": todo_write,
        # File I/O
        "get_file_info": get_file_info,
        "read_file": read_file,
        "write_file": write_file,
        "append_to_file": append_to_file,
        "replace_in_file": replace_in_file,
        # Directory / Search
        "list_dir": list_dir,
        "find_files": find_files,
        "search_files": search_files,
        "get_cwd": get_cwd,
        # Shell
        "run_command": run_command,
        "run_background": run_background,
        "check_background": check_background,
        "change_dir": change_dir,
        # Skills
        "create_skill": create_skill,
        "list_skills": list_skills,
        "delete_skill": delete_skill,
    }

    if name not in dispatch:
        return (
            f"Unknown tool: '{name}'. "
            f"Available tools: {', '.join(sorted(dispatch.keys()))}"
        )

    try:
        return dispatch[name](**kwargs)
    except TypeError as e:
        return f"Tool '{name}' called with invalid arguments: {e}"
    except Exception as e:
        return f"Unexpected error in tool '{name}': {e}"
