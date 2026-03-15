# MiniCoder

> A lightweight agentic CLI coding assistant — powered by any OpenAI-compatible LLM.

MiniCoder runs in your terminal as an interactive REPL. It can read and write files, search
codebases, and run shell commands, while always keeping you in control with a human-in-the-loop
approval step before any destructive action.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## Features

- **Agentic loop** — the assistant plans, uses tools, inspects results, and iterates until the task is done.
- **Human-in-the-loop** — every batch of tool calls is shown as a plan; you approve, deny, or redirect before execution.
- **Surgical file editing** — `replace_in_file` makes targeted edits with fuzzy-match hints when the target isn't found.
- **Codebase search** — grep across all common text file types; find files by glob pattern.
- **Shell access** — run commands with a safety blocklist (no `rm -rf`, `format`, etc.) and a 60-second timeout.
- **Skills (persistent memory)** — tell the agent to "remember how to X" and it saves a Markdown skill file loaded on every future session.
- **Session persistence** — save and restore conversation history with `/save` and `/load`.
- **Smart context pruning** — removes old tool-call pairs atomically to stay within the context window.
- **Works with any OpenAI-compatible API** — OpenAI, Azure OpenAI, Google Gemini, local Ollama, etc.

---

## Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/MiniCoder.git
cd MiniCoder

# (Recommended) Create a virtual environment
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # macOS/Linux

# Install dependencies
pip install openai httpx prompt_toolkit rich
# or, once pyproject.toml is in sync:
# pip install -e .
```

---

## Configuration

1. Copy the example config and fill in your credentials:

   ```bash
   cp config.example.json config.json
   ```

2. Edit `config.json`:

   ```json
   {
       "api_key": "sk-...",
       "api_base": "https://api.openai.com/v1",
       "model": "gpt-4o",
       "max_loops": 20,
       "timeout": 60,
       "max_retries": 3
   }
   ```

   | Field | Description |
   |-------|-------------|
   | `api_key` | Your API key (never commit this file — it is in `.gitignore`) |
   | `api_base` | API endpoint; change for Azure, Gemini, Ollama, etc. |
   | `model` | Model name, e.g. `gpt-4o`, `gemini-1.5-pro`, `llama3` |
   | `max_loops` | Max tool-call iterations per turn (default: 20) |
   | `timeout` | Request timeout in seconds (default: 60) |
   | `max_retries` | Retry attempts on 429 / 5xx errors (default: 3) |
   | `system_prompt` | *(Optional)* Override the assistant's system prompt |

3. Run:

   ```bash
   python main.py
   ```

---

## CLI Flags

| Flag | Description |
|------|-------------|
| `--auto` / `--yolo` | Auto-approve all tool calls without prompting |
| `--model MODEL` | Override the model from `config.json` |
| `--max-loops N` | Override max tool-call loops per turn |
| `--no-skills` | Disable skills injection into the system prompt |
| `--config PATH` | Use a custom config file path |

---

## REPL Commands

Type these at the `❯` prompt at any time:

| Command | Description |
|---------|-------------|
| `/help` | Show the help message |
| `/clear` | Clear conversation history (keeps system prompt) |
| `/history` | Show message count and role breakdown |
| `/save [file]` | Save session to JSON (default: `session.json`) |
| `/load [file]` | Load session from JSON (default: `session.json`) |
| `/usage` | Show cumulative token usage statistics |
| `exit` / `quit` | End the session (offers to save) |
| `Ctrl+C` | Interrupt the current agent action |
| `Ctrl+D` | Quit immediately |

---

## Plan Approval

When the agent proposes tool calls, you see a plan panel and an arrow-key selection menu:

| Selection | Action |
|-----------|--------|
| ✅ Execute all | Approve and run all planned steps |
| ❌ Deny all | Reject the entire plan |
| ⚡ Full auto | Auto-approve all future steps this session |
| 💬 Modify / custom input | Send feedback; agent will revise the plan |

You can also type step numbers (e.g. `1` or `1,3`) to run only specific steps.

---

## Available Tools

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents (truncates at 30K chars, refuses >100 KB) |
| `write_file` | Write complete file content (overwrite) |
| `append_to_file` | Append text to a file without reading it first |
| `replace_in_file` | Surgical text replacement with fuzzy-match error hints |
| `list_dir` | List directory with file sizes and `[DIR]`/`[FILE]` labels |
| `find_files` | Find files by glob pattern (e.g. `*.json`, `*.py`) |
| `search_files` | Grep across all common text file types (configurable extensions) |
| `get_cwd` | Get the current working directory |
| `run_command` | Run shell commands (dangerous-command blocklist, 60s timeout) |
| `change_dir` | Change the working directory |
| `create_skill` | Save a reusable workflow for future sessions |

---

## Skills (Persistent Memory)

Tell the assistant to *"remember how to deploy this project"* and it saves a Markdown skill
file to `skills/`. Skills are automatically injected into the system prompt at the start of
every session.

> **Note:** the `skills/` directory is excluded from version control (see `.gitignore`).

---

## Session Persistence

- On startup, if a previous `session.json` exists, you are offered to restore it.
- Use `/save` to save at any time; use `/load` to restore.
- On `exit`/`quit`, you are offered to save before closing.

> **Note:** `session.json` may contain sensitive information and is excluded from version control.

---

## License

[MIT](LICENSE)
