import os
import json
import fnmatch
import difflib


# ---------------------------------------------------------------------------
# File I/O Tools
# ---------------------------------------------------------------------------

def read_file(path: str) -> str:
    """Read contents of a file. Returns truncated content if file is too large."""
    try:
        if not os.path.exists(path):
            return f"Error: File '{path}' does not exist."

        file_size = os.path.getsize(path)
        if file_size > 100 * 1024:
            return (
                f"Error: File '{path}' is too large ({file_size:,} bytes). "
                "Max allowed is 100 KB. Consider reading specific line ranges with run_command."
            )

        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        if len(content) > 30000:
            return (
                    f"[Content truncated — full length {len(content):,} chars. "
                    f"Showing first 30,000 chars]\n"
                    + content[:30000]
                    + "\n...[TRUNCATED]"
            )
        return content
    except Exception as e:
        return f"Error reading file '{path}': {e}"


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
    existing lines so the LLM can correct its target string.
    """
    try:
        if not os.path.exists(path):
            return f"Error: File '{path}' not found."

        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        occurrences = content.count(target)
        if occurrences == 0:
            # Provide a fuzzy-match hint: show lines closest to the target
            target_lines = target.strip().splitlines()
            file_lines = content.splitlines()
            hint_lines = []
            for tl in target_lines[:3]:  # Check up to first 3 lines of target
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

        # Sort: dirs first, then files, alphabetically
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
            # Skip hidden/venv/cache directories
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
            # Sensible default: all common text file types
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

        # Results are capped at 100 matches; collect and display the same limit for consistency.
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

# Patterns that could cause irreversible damage
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

    import subprocess
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=timeout,
            # Handle Windows codepages gracefully
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
# Skills Tool
# ---------------------------------------------------------------------------

def create_skill(name: str, description: str, instructions: str) -> str:
    """Create a new reusable skill/workflow note for the assistant to remember."""
    try:
        skills_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
        os.makedirs(skills_dir, exist_ok=True)

        # Sanitize filename
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
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Reads the text content of a file. "
                "Automatically truncates if > 30,000 chars; refuses files > 100 KB."
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
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command and capture stdout/stderr. "
                "Dangerous commands (rm -rf, format, del /f /s, etc.) are blocked. "
                "Default timeout is 60 seconds."
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
                    "name": {
                        "type": "string",
                        "description": "Short descriptive name, e.g. 'Deploy Frontend'."
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief explanation of when to use this skill."
                    },
                    "instructions": {
                        "type": "string",
                        "description": "Detailed Markdown step-by-step instructions and code snippets."
                    }
                },
                "required": ["name", "description", "instructions"]
            }
        }
    }
]


# ---------------------------------------------------------------------------
# Tool Dispatcher
# ---------------------------------------------------------------------------

def execute_tool(name: str, kwargs: dict) -> str:
    """Dispatch a tool call by name with provided keyword arguments."""
    dispatch = {
        "read_file": read_file,
        "write_file": write_file,
        "append_to_file": append_to_file,
        "replace_in_file": replace_in_file,
        "list_dir": list_dir,
        "find_files": find_files,
        "search_files": search_files,
        "get_cwd": get_cwd,
        "run_command": run_command,
        "change_dir": change_dir,
        "create_skill": create_skill,
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
