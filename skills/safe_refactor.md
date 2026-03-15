---
name: Safe Refactor
description: Refactor code safely with minimal diff and verification steps.
---

## Instructions

When asked to refactor or restructure code:

1. **Read before touching** — use `read_file` (with line ranges for large files) to fully understand the current implementation.
2. **Search for all usages** — `search_files` for the function/class name to find every call site before renaming or moving anything.
3. **Prefer `replace_in_file`** over `write_file` — make surgical, targeted edits. Never rewrite a whole file if only a few lines change.
4. **One change at a time** — commit logically separate changes separately. Do not batch unrelated edits.
5. **Verify after each edit** — run the relevant test or linter with `run_command` (e.g. `python -m pytest tests/ -x -q`).
6. If a test fails, fix it immediately before moving to the next change.
7. Summarise what was changed and why at the end.
