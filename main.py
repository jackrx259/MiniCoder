import os
import json
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import Agent
from tui import TUI


def parse_cli_args():
    """Wire up argparse and return the parsed Namespace."""
    parser = argparse.ArgumentParser(
        description="MiniCoder — Agentic CLI Coding Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                        # Normal mode (human approval)
  python main.py --auto                 # Auto-approve all tool calls
  python main.py --model gpt-4o-mini   # Override model
  python main.py --max-loops 10        # Override loop limit
  python main.py --no-skills           # Disable skills injection
        """
    )
    parser.add_argument(
        "--auto", "--yolo",
        action="store_true",
        help="Auto-approve all tool calls without asking (equivalent to --yolo)."
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override the model name from config.json (e.g. 'gpt-4o-mini')."
    )
    parser.add_argument(
        "--max-loops",
        type=int,
        default=None,
        metavar="N",
        help="Override the maximum tool-call loops per turn (default: 20)."
    )
    parser.add_argument(
        "--no-skills",
        action="store_true",
        help="Disable automatic skills injection into the system prompt."
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to a custom config.json (default: config.json in script directory)."
    )
    return parser.parse_args()


def main():
    args = parse_cli_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_file = args.config or os.path.join(script_dir, "config.json")

    # ── Config loading (pre-TUI) ─────────────────────────────────────────────────
    # use plain print() here because the TUI session hasn't started yet.
    if not os.path.exists(config_file):
        print(f"[warn] Config file not found at: {config_file}")
        print("[info] Generating a default config…")
        default_config = {
            "api_key": "YOUR_API_KEY_HERE",
            "api_base": "https://api.openai.com/v1",
            "model": "gpt-4o",
            "max_loops": 20,
            "timeout": 60,
            "max_retries": 3,
            "system_prompt": (
                "You are MiniCoder, a powerful CLI coding assistant like Claude Code. "
                "You run locally on the user's machine with full access to tools allowing you "
                "to read/write files, list directories, and execute terminal commands. "
                "When asked to perform a task, think step by step, read the relevant context, "
                "propose a plan if necessary, and execute the required changes using your tools. "
                "Always ensure your code is solid."
            )
        }
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=4, ensure_ascii=False)
        print(f"[ok] Created default config at: {config_file}")
        print("[info] Please update it with your API key before running again.")
        sys.exit(1)

    with open(config_file, 'r', encoding='utf-8') as f:
        config = json.load(f)

    if config.get("api_key", "") in ("", "YOUR_API_KEY_HERE"):
        print(f"[error] Please update '{config_file}' with your actual API key.")
        sys.exit(1)

    # ── Apply CLI overrides ───────────────────────────────────────────────────
    if args.auto:
        config["approval_mode"] = "auto"
    if args.model:
        config["model"] = args.model
    if args.max_loops is not None:
        config["max_loops"] = args.max_loops
    if args.no_skills:
        config["load_skills"] = False

    # ── Start ─────────────────────────────────────────────────────────────────
    # hand control to the TUI here; it owns the main thread for the rest of the session.
    tui = TUI()
    agent = Agent(config, tui=tui)
    tui.run(agent.start_loop)


if __name__ == "__main__":
    main()
