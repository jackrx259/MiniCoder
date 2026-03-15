---
name: Explore Codebase
description: Systematically map an unfamiliar project before making changes.
---

## Instructions

When asked to understand or explore a new codebase, follow this order:

1. `get_cwd` — confirm current directory.
2. `list_dir` on the root — get a top-level overview of the project layout.
3. Read key files first: `README.md`, `pyproject.toml` / `package.json` / `Cargo.toml` (whichever applies) to understand the project's purpose and dependencies.
4. `find_files` with `*.py` (or relevant extension) to enumerate source files.
5. `search_files` for entry points: `if __name__ == "__main__"`, `def main`, `app =`, `export default`, etc.
6. Read the entry point and the most central module first; fan out only as needed.
7. Summarise findings in a short paragraph before proposing any changes.

**Do not rewrite or modify anything during exploration.**
