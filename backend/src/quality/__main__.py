from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from backend.src.quality.m0 import default_tasks, render_markdown_report, run_quality_suite, run_with_static_executor
from backend.src.quality.ollama import LiveOllamaExecutor


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a BSNexus M0 quality report from harness telemetry.")
    parser.add_argument("--json-out", type=Path, help="Optional path to write the JSON report.")
    parser.add_argument("--markdown-out", type=Path, help="Optional path to write the Markdown report.")
    parser.add_argument("--live-ollama", action="store_true", help="Run live local Ollama probe instead of fixture data.")
    parser.add_argument("--model", default="qwen3-coder:30b", help="Ollama model for --live-ollama.")
    parser.add_argument("--ollama-url", default="http://host.docker.internal:11434/api/generate")
    args = parser.parse_args()

    tasks = default_tasks()
    if args.live_ollama:
        report = asyncio.run(
            run_quality_suite(
                tasks=tasks,
                executor=LiveOllamaExecutor(model=args.model, endpoint=args.ollama_url),
            )
        )
    else:
        report = asyncio.run(run_with_static_executor(tasks))
    markdown = render_markdown_report(report)
    if args.json_out:
        args.json_out.write_text(json.dumps(report.to_dict(), indent=2, default=str) + "\n")
    if args.markdown_out:
        args.markdown_out.write_text(markdown)
    if not args.json_out and not args.markdown_out:
        print(markdown, end="")


if __name__ == "__main__":
    main()
