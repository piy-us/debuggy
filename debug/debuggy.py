#!/usr/bin/env python3
"""
debuggy3.py — CLI-based autonomous code debugger.

Usage:
    python debuggy3.py fix "python workspace/file2.py"
    python debuggy3.py "python workspace/file2.py"
"""
from __future__ import annotations

import ast
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax
from rich.text import Text
from rich.rule import Rule
from rich.table import Table
from rich import box
from pprint import pformat

from agent2 import ask_agent

# ── Config ────────────────────────────────────────────────────────────────────

MAX_RETRIES    = 6
MAX_DIFF_LINES = 80
console        = Console()
app            = typer.Typer(invoke_without_command=True)
COMMAND_TIMEOUT = 15
timeout=COMMAND_TIMEOUT
# ── Data class ────────────────────────────────────────────────────────────────

BASE_DIR = Path("./workspace").resolve()

IGNORE_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "node_modules",
    "dist",
    "build",
}

# ── Path safety ─────────────────────────────────────────────────────────────

def safe_path(path: str) -> Path:
    
    full = (BASE_DIR / path).resolve()
    if not str(full).startswith(str(BASE_DIR)):
        raise ValueError(f"Path '{path}' escapes workspace — rejected.")
    
    return full

@dataclass
class ParsedError:
    language: str
    error_type: str
    error_message: str
    file_path: Optional[Path]
    line_number: Optional[int]
    relevant_lines: List[str] = field(default_factory=list)
    raw_stderr: str = ""

# ── Stderr helpers ────────────────────────────────────────────────────────────
# 5th
def clean_powershell_noise(stderr: str) -> str:
    lines = stderr.splitlines()
    cleaned = []
    skip = ("At line:", "+ ", "    + ", "CategoryInfo", "FullyQualifiedErrorId")
    for line in lines:
        if re.match(r'^\w[\w. ]+ : ', line):
            line = re.sub(r'^\w[\w. ]+ : ', '', line)
        if any(line.strip().startswith(s) for s in skip):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def get_lines_around(path: Path, line_no: int, context: int = 12) -> List[str]:
    try:
        path = safe_path(str(path))
        lines = path.read_text(encoding="utf-8").splitlines()
        start = max(0, line_no - context - 1)
        end   = min(len(lines), line_no + context)
        out   = []
        for i, l in enumerate(lines[start:end], start=start):
            marker = ">>>" if (i + 1) == line_no else "   "
            out.append(f"{marker} {i+1:4}: {l}")
        return out
    except Exception:
        return []

# ── Traceback parser ──────────────────────────────────────────────────────────
# 4th
def parse_traceback(stderr: str) -> ParsedError:
    stderr = clean_powershell_noise(stderr)

    # Python
    py_matches = re.findall(r'File "([^"]+)", line (\d+)', stderr)
    if py_matches:
        your_files = [
            (f, int(l)) for f, l in py_matches
            if not any(s in f for s in ('site-packages', 'venv', '<frozen'))
        ]
        if your_files:
            file_path, line_no = your_files[-1]
            last_line  = stderr.strip().splitlines()[-1]
            parts      = last_line.split(":", 1)
            error_type = parts[0].strip()
            error_msg  = parts[1].strip() if len(parts) > 1 else last_line
            p = safe_path(file_path)
            return ParsedError(
                language       = "python",
                error_type     = error_type,
                error_message  = error_msg,
                file_path      = p if p.exists() else None,
                line_number    = line_no,
                relevant_lines = get_lines_around(p, line_no) if p.exists() else [],
                raw_stderr     = stderr,
            )

    # Node.js
    node_matches = re.findall(r'at .+?\((.+?):(\d+):\d+\)', stderr)
    if node_matches:
        your_files = [
            (f, int(l)) for f, l in node_matches
            if not any(s in f for s in ('node_modules', 'node:internal', '<anonymous>'))
        ]
        if your_files:
            file_path, line_no = your_files[0]
            first_line = stderr.strip().splitlines()[0]
            parts      = first_line.split(":", 1)
            error_type = parts[0].strip()
            error_msg  = parts[1].strip() if len(parts) > 1 else first_line
            p = safe_path(file_path)
            return ParsedError(
                language       = "javascript",
                error_type     = error_type,
                error_message  = error_msg,
                file_path      = p if p.exists() else None,
                line_number    = line_no,
                relevant_lines = get_lines_around(p, line_no) if p.exists() else [],
                raw_stderr     = stderr,
            )

    # Fallback
    return ParsedError(
        language      = "unknown",
        error_type    = "Error",
        error_message = stderr.strip().splitlines()[-1] if stderr.strip() else "unknown error",
        file_path     = None,
        line_number   = None,
        raw_stderr    = stderr,
    )

# ── Prompt builder ────────────────────────────────────────────────────────────
# 6th
def build_prompt(error: ParsedError, command: str) -> str:
    crash_location = (
        f"File: {safe_path(error.file_path)}, line {error.line_number}"
        if error.file_path else "Could not identify specific file"
    )
    relevant = "\n".join(error.relevant_lines) or "(not available)"

    full_file_section = ""
    if error.file_path and error.file_path.exists():
        full_content = safe_path(error.file_path).read_text(encoding="utf-8")
        full_file_section = (
            f"\nFULL FILE CONTENTS ({safe_path(error.file_path)}):\n"
            f"```{error.language}\n{full_content}\n```\n"
        )

    return f"""Fix a {error.language} runtime error.

COMMAND THAT FAILED:
{command}

ERROR:
{error.error_type}: {error.error_message}

CRASH LOCATION:
{crash_location}

LINES AROUND THE CRASH:
{relevant}
{full_file_section}
FULL STDERR:
{error.raw_stderr[:3000]}

INSTRUCTIONS:
1. Analyze the error carefully.
2. DO NOT modify files directly.
3. DO NOT run shell commands.
4. ONLY return proposed file fixes.
5. Keep patches minimal and targeted.
6. Preserve ALL existing functions, classes, and business logic.
7. Return fixes using write_file actions only.
"""

# ── Validation ────────────────────────────────────────────────────────────────

def validate_python_syntax(content: str, path: str) -> bool:
    try:
        ast.parse(content)
        return True
    except SyntaxError as e:
        console.print(f"[red]  Syntax error in proposed fix for {path}: {e}[/red]")
        return False


def extract_function_names(content: str) -> set:
    try:
        tree = ast.parse(content)
        return {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
    except Exception:
        return set()


def diff_line_count(old: str, new: str) -> int:
    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm=""))
    return len([l for l in diff if l.startswith(('+', '-')) and not l.startswith(('+++', '---'))])

# ── Diff display ──────────────────────────────────────────────────────────────

# def show_diff(file_path: Path, fixed_content: str) -> bool:
#     """
#     Render a syntax-highlighted unified diff.
#     Returns True if there are actual changes, False if nothing changed.
#     """
#     old_content = file_path.read_text(encoding="utf-8")

#     diff_lines = list(difflib.unified_diff(
#         old_content.splitlines(),
#         fixed_content.splitlines(),
#         fromfile=f"{file_path}  (original)",
#         tofile=f"{file_path}  (fixed)",
#         lineterm="",
#     ))

#     if not diff_lines:
#         console.print("  [yellow]No changes detected in proposed fix.[/yellow]")
#         return False

#     diff_text = "\n".join(diff_lines)
#     console.print(
#         Panel(
#             Syntax(diff_text, "diff", theme="monokai", line_numbers=False),
#             title=f"[bold cyan]Diff — {file_path.name}[/bold cyan]",
#             border_style="cyan",
#             expand=True,
#         )
#     )
#     return True
def show_diff(file_path: Path, fixed_content: str) -> bool:
    file_path = safe_path(str(file_path))
    old_content = file_path.read_text(encoding="utf-8")

    diff = difflib.unified_diff(
        old_content.splitlines(),
        fixed_content.splitlines(),
        fromfile=str(file_path),
        tofile=f"{file_path} (fixed)",
        lineterm=""
    )

    diff_text = "\n".join(diff)

    if not diff_text.strip():
        console.print("[yellow]No changes detected.[/yellow]")
        return False

    syntax = Syntax(
        diff_text,
        "diff",
        theme="monokai",
        line_numbers=False
    )

    console.print()
    console.print(
        Rule(f"[bold cyan]Proposed Changes — {file_path.name}[/bold cyan]")
    )

    console.print(syntax)

    return True
# ── Apply fix (with diff + confirmation) ─────────────────────────────────────

def apply_fix(file_path: Path, fixed_content: str) -> bool:
    """
    Show guards → show diff → ask user → write file.
    Returns True if the fix was applied, False if rejected/cancelled.
    """
    file_path = safe_path(str(file_path))
    old_content = file_path.read_text(encoding="utf-8")

    # Guard: diff size
    changed = diff_line_count(old_content, fixed_content)
    if changed > MAX_DIFF_LINES:
        console.print(
            f"  [red]✗ Rejecting: {changed} changed lines exceeds safety limit of "
            f"{MAX_DIFF_LINES}. Looks like a full rewrite.[/red]"
        )
        return False

    # Guard: Python syntax
    if file_path.suffix == ".py" and not validate_python_syntax(fixed_content, str(file_path)):
        console.print("  [red]✗ Rejecting: proposed content has syntax errors.[/red]")
        return False

    # Guard: no functions deleted
    if file_path.suffix == ".py":
        old_names = extract_function_names(old_content)
        new_names = extract_function_names(fixed_content)
        removed   = old_names - new_names
        if removed:
            console.print(
                f"  [red]✗ Rejecting: these would be deleted: "
                f"{', '.join(sorted(removed))}[/red]"
            )
            return False

    # Show the diff
    has_changes = show_diff(file_path, fixed_content)
    if not has_changes:
        return False

    # Ask the user
    console.print()
    if not Confirm.ask("  [bold]Apply this fix?[/bold]"):
        console.print("  [yellow]Skipped.[/yellow]")
        return False

    backup = file_path.with_suffix(file_path.suffix + ".bak")
    shutil.copy(file_path, backup)
    file_path.write_text(fixed_content, encoding="utf-8")
    console.print(f"  [bold green]✓ Applied.[/bold green]  [dim]Backup → {backup}[/dim]\n")
    return True

# ── Shell runner ──────────────────────────────────────────────────────────────

# def run_command(command: str):
#     cmd = command.strip()
#     if re.match(r'^[\w./ \\:]+\.py$', cmd) and not cmd.startswith("python"):
#         cmd = f"python {cmd}"
#     result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
#     return result.stdout, result.stderr, result.returncode

#3rd
def run_command(command: str):
    cmd = command.strip()
    
    if re.match(r'^[\w./ \\:]+\.py$', cmd) and not cmd.startswith("python"):
        cmd = f"python {safe_path(cmd)}"

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15,   # prevents hanging forever
        )

        return result.stdout, result.stderr, result.returncode

    except subprocess.TimeoutExpired:
        return (
            "",
            "Process timed out after 15 seconds.",
            124,
        )

# ── Agent activity display ────────────────────────────────────────────────────
# 7th
def print_agent_activity(messages: list) -> None:
    """
    Walk the LangGraph message list and pretty-print every tool call
    and tool result so the user can follow what the agent is doing.
    """
    console.print(Rule("[bold blue]Agent Activity[/bold blue]", style="blue"))

    for msg in messages:
        role = getattr(msg, "type", None) or msg.__class__.__name__.lower()

        # Tool calls made by the AI
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                tool_name = tc.get("name", "?")
                tool_args = tc.get("args", {})

                # Format args as a small table
                tbl = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
                tbl.add_column("key",   style="dim cyan",  no_wrap=True)
                tbl.add_column("value", style="white",     overflow="fold")
                for k, v in tool_args.items():
                    val_str = str(v)
                    # Truncate very long values (e.g. full file reads) for display
                    if len(val_str) > 1000:
                        val_str = val_str[:300] + "…"
                    tbl.add_row(k, val_str)

                console.print(
                    Panel(
                        tbl,
                        title=f"[bold yellow]🔧 Tool Call → {tool_name}[/bold yellow]",
                        border_style="yellow",
                        expand=False,
                    )
                )

        # Tool results returned to the agent
        # if role in ("tool", "toolmessage"):
        #     content = getattr(msg, "content", "")
        #     name    = getattr(msg, "name", "tool")
        #     # Truncate large outputs (e.g. full file reads)
        #     display = content if len(content) <= 300 else content[:600] + "\n…(truncated)"
        #     console.print(
        #         Panel(
        #             display,
        #             title=f"[bold green]📤 Tool Result ← {name}[/bold green]",
        #             border_style="green",
        #             expand=False,
        #         )
        #     )
        # Tool results returned to the agent
        if role in ("tool", "toolmessage"):

            from pprint import pformat

            content = getattr(msg, "content", "")
            name = getattr(msg, "name", "tool")

            # Normalize content into printable text
            if content is None:
                content = ""

            elif not isinstance(content, str):
                try:
                    content = pformat(content, compact=True)
                except Exception:
                    content = str(content)

            # Truncate large outputs
            if len(content) > 600:
                display = content[:600] + "\n…(truncated)"
            else:
                display = content

            console.print(
                Panel(
                    display,
                    title=f"[bold green]📤 Tool Result ← {name}[/bold green]",
                    border_style="green",
                    expand=False,
                )
            )
        # Agent's reasoning text (if any)
        if role in ("ai", "aimessage"):
            text = getattr(msg, "content", "")
            if text and text.strip():
                # Only show if it's actual reasoning, not just a blank filler
                console.print(f"[dim]💭 Agent:[/dim] {text.strip()[:400]}")

    console.print(Rule(style="blue"))

# ── Agent response parser ─────────────────────────────────────────────────────

# def extract_write_actions(agent_response: dict) -> dict:
#     fixes: dict = {}
#     for action in agent_response.get("actions", []):
#         if action.get("tool") == "write_file":
#             args    = action.get("arguments", {})
#             path    = args.get("path")
#             content = args.get("content")
#             if path and content:
#                 fixes[path] = content
#     # Legacy XML fallback
#     thought = agent_response.get("thought", "")
#     legacy  = re.findall(
#         r'<fixed_file path="([^"]+)">\s*(.*?)\s*</fixed_file>',
#         thought, re.DOTALL,
#     )
#     for path, content in legacy:
#         content = re.sub(r'^```\w*\n?', '', content)
#         content = re.sub(r'\n?```$',    '', content)
#         if path not in fixes:
#             fixes[path] = content.strip()
#     return fixes

# 8th
def extract_write_actions(agent_response: dict) -> dict:
    fixes = {}

    #
    # FORMAT 1
    # {
    #   "actions": [
    #       {
    #           "tool": "write_file",
    #           "arguments": {...}
    #       }
    #   ]
    # }
    #
    for action in agent_response.get("actions", []):
        tool = action.get("tool") or action.get("type")

        if tool == "write_file":
            args = action.get("arguments", action)

            path = args.get("path")
            path = safe_path(str(path))
            content = args.get("content")

            if path and content:
                fixes[path] = content

    #
    # FORMAT 2
    # {
    #   "action": "write_file",
    #   "path": "...",
    #   "content": "..."
    # }
    #
    if agent_response.get("action") == "write_file":
        path = agent_response.get("path")
        path = safe_path(str(path))
        content = agent_response.get("content")

        if path and content:
            fixes[path] = content

    #
    # FORMAT 3
    # JSON embedded inside "thought"
    #
    thought = agent_response.get("thought", "")

    json_matches = re.findall(
        r'```json\s*(\{.*?\})\s*```',
        thought,
        re.DOTALL
    )

    for match in json_matches:
        try:
            data = json.loads(match)

            # nested extraction
            nested = extract_write_actions(data)

            fixes.update(nested)

        except Exception:
            pass

    #
    # FORMAT 4
    # Legacy XML fallback
    #
    legacy = re.findall(
        r'<fixed_file path="([^"]+)">\s*(.*?)\s*</fixed_file>',
        thought,
        re.DOTALL,
    )

    for path, content in legacy:
        content = re.sub(r'^```\w*\n?', '', content)
        content = re.sub(r'\n?```$', '', content)

        if path not in fixes:
            fixes[path] = content.strip()

    return fixes
# ── Core fix logic ────────────────────────────────────────────────────────────
# 2nd
def cmd_fix(command: str) -> None:
    console.print()
    console.print(Rule(f"[bold cyan]debuggy[/bold cyan]", style="cyan"))
    console.print(f"  [cyan]Command:[/cyan] {command}\n")

    stdout, stderr, code = run_command(command)

    if code == 0:
        console.print("[bold green]✓ Program exited successfully. Nothing to fix.[/bold green]")
        if stdout.strip():
            console.print(stdout)
        return

    if not stderr.strip():
        console.print("[yellow]Empty stderr — nothing to fix.[/yellow]")
        return

    error = parse_traceback(stderr)

    # ── Error summary panel ───────────────────────────────────────────────────
    summary = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    summary.add_column("label", style="dim")
    summary.add_column("value")
    summary.add_row("Error",    f"[bold red]{error.error_type}[/bold red]: {error.error_message}")
    summary.add_row("File",     str(error.file_path) if error.file_path else "[yellow]unknown[/yellow]")
    summary.add_row("Line",     str(error.line_number) if error.line_number else "[yellow]unknown[/yellow]")
    summary.add_row("Language", error.language)
    console.print(Panel(summary, title="[bold red]Error Detected[/bold red]", border_style="red"))

    # ── Show crash context ────────────────────────────────────────────────────
    if error.relevant_lines:
        crash_code = "\n".join(error.relevant_lines)
        console.print(
            Panel(
                Syntax(crash_code, error.language, theme="monokai", line_numbers=False),
                title="[bold]Crash Context[/bold]",
                border_style="dim",
            )
        )

    # ── Retry loop ────────────────────────────────────────────────────────────
    for attempt in range(1, MAX_RETRIES + 1):
        console.print()
        console.print(Rule(
            f"[bold cyan]Attempt {attempt} / {MAX_RETRIES}[/bold cyan]",
            style="cyan",
        ))

        stdout, stderr, code = run_command(command)

        if code == 0:
            console.print("\n[bold green]✓ Program running successfully![/bold green]")
            if stdout.strip():
                console.print("\n[cyan]Output:[/cyan]")
                console.print(stdout)
            return

        error = parse_traceback(stderr)
        console.print(f"  [red]Still failing:[/red] {error.error_type}: {error.error_message}")

        # Build prompt and call agent (single API call)
        prompt             = build_prompt(error, command)
        agent_resp, msgs   = ask_agent(prompt)

        # ── Show agent activity (tool calls + results) ────────────────────────
        if msgs:
            print_agent_activity(msgs)

        # ── Show agent's final thought ────────────────────────────────────────
        thought = agent_resp.get("thought", "").strip()
        if thought:
            console.print(
                Panel(
                    thought[:600] + ("…" if len(thought) > 600 else ""),
                    title="[bold blue]Agent Analysis[/bold blue]",
                    border_style="blue",
                )
            )

        # status = agent_resp.get("status", "needs_more_work")

        # if status == "fixed":
        #     console.print("  [green]Agent reports fix applied via tools. Verifying…[/green]")
        #     stdout, stderr, code = run_command(command)
        #     if code == 0:
        #         console.print("[bold green]✓ Verified — program runs successfully![/bold green]")
        #         return
        #     else:
        #         console.print("  [yellow]Agent said fixed but program still fails. Retrying…[/yellow]")
        #         error = parse_traceback(stderr)
        #         continue

        # Check for explicit write_file actions
        fixes = extract_write_actions(agent_resp)
        console.print(f"[cyan]Extracted fixes:[/cyan] {list(fixes.keys())}")

        # if not fixes:
        #     console.print(
        #         "  [dim]No explicit file rewrites in response — "
        #         "agent patched via tools. Re-checking…[/dim]"
        #     )
        #     continue
        if not fixes:
            console.print(
                "  [red]Agent did not return any proposed fixes.[/red]"
            )
            continue

        any_applied = False
        for fix_path_str, fix_content in fixes.items():
            #p = Path(fix_path_str)
            p = safe_path(str(fix_path_str))
            if not p.exists() and error.file_path:
                p = error.file_path.parent / fix_path_str
            if p.exists():
                console.print(f"\n  [cyan]Proposed fix for:[/cyan] [bold]{p}[/bold]")
                # if apply_fix(p, fix_content):
                #     any_applied = True
                if apply_fix(p, fix_content):
                    any_applied = True

                    console.print(
                        "\n[cyan]Re-running command to verify fix...[/cyan]\n"
                    )
            else:
                console.print(f"  [yellow]Skipping {fix_path_str} — file not found.[/yellow]")

        # if not any_applied:
        #     console.print("  [red]No fixes could be applied this round.[/red]")
        if not any_applied:
            console.print("  [red]No fixes could be applied this round.[/red]")
            continue

    console.print("\n[bold red]Exceeded maximum retries without a successful fix.[/bold red]")




# ── CLI ───────────────────────────────────────────────────────────────────────
# 1st
@app.command("fix")
def fix_command(
    command: str = typer.Argument(..., help="Command to run, e.g. 'python workspace/file2.py'")
):
    """Run COMMAND, detect errors, and autonomously attempt to fix them."""
    cmd_fix(command)

@app.command()
def donothing():
    pass

if __name__ == "__main__":
    app()