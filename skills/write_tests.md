---
name: Write Tests
description: Write thorough unit/integration tests for a module or function.
---

## Instructions

When asked to add tests:

1. `read_file` the target module to understand what needs to be tested.
2. `find_files` for existing test files (`test_*.py`, `*_test.py`) to follow the project's conventions.
3. Read one existing test file to match style (import paths, fixtures, assertion style).
4. Write tests covering:
   - **Happy path** — expected inputs produce expected outputs.
   - **Edge cases** — empty input, None, zero, boundary values.
   - **Error cases** — invalid input raises the right exception.
5. Use `write_file` or `replace_in_file` to add tests to the appropriate file.
6. Run tests with `run_command`: `python -m pytest <test_file> -v` and confirm they pass.
7. Report: N tests added, all passing.
