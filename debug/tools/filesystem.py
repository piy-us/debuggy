import subprocess
from pathlib import Path

from langchain.tools import tool
from pydantic import BaseModel
# ── Config ──────────────────────────────────────────────────────────────────
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


# ── Config ────────────────────────────────────────────────────────────────────
console        = Console()

MAX_RETRIES    = 6
MAX_DIFF_LINES = 80

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

# ── Tools ───────────────────────────────────────────────────────────────────
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

def show_diff(file_path: Path, fixed_content: str) -> bool:
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

def apply_fix(file_path: Path, fixed_content: str) -> bool:
    """
    Show guards → show diff → ask user → write file.
    Returns True if the fix was applied, False if rejected/cancelled.
    """
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

@tool
def list_files(path: str = ".") -> str:
    """
    List all files and directories recursively under the workspace.
    Use this to understand project structure before making any edits.
    """
    target = safe_path(path)

    if not target.exists():
        return f"Path does not exist: {path}"

    output = []
    for item in target.rglob("*"):
        if any(part in IGNORE_DIRS for part in item.parts):
            continue
        rel = item.relative_to(BASE_DIR)
        prefix = "[DIR] " if item.is_dir() else "[FILE]"
        output.append(f"{prefix} {rel}")

    return "\n".join(sorted(output)) if output else "(empty workspace)"


@tool
def read_file(path: str) -> str:
    """
    Read the full contents of a file.
    ALWAYS call this before modifying any file — never assume contents.
    """
    file_path = safe_path(path)

    if not file_path.exists():
        return f"File does not exist: {path}"

    return file_path.read_text(encoding="utf-8")


class WriteFileInput(BaseModel):
    path: str
    content: str


@tool(args_schema=WriteFileInput)
def write_file(path: str, content: str) -> str:
    """
    Write (overwrite) an entire file with new content.
    Prefer replace_in_file for targeted edits.
    Only use this when creating a new file or replacing a severely corrupted one.
    """
    file_path = safe_path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return f"Wrote {path} ({len(content)} chars)"


class ReplaceInput(BaseModel):
    path: str
    old_text: str
    new_text: str


# @tool(args_schema=ReplaceInput)
# def replace_in_file(path: str, old_text: str, new_text: str) -> str:
#     """
#     Replace a specific block of text in a file with new text.
#     This is the PREFERRED editing tool — use it for targeted, minimal patches.
#     old_text must match the file content exactly (including whitespace/indentation).
#     Only the first occurrence is replaced.
#     """
#     file_path = safe_path(path)

#     if not file_path.exists():
#         return f"File does not exist: {path}"

#     content = file_path.read_text(encoding="utf-8")

#     if old_text not in content:
#         return (
#             "Target text not found in file. "
#             "Make sure old_text matches exactly (including indentation).\n\n"
#             f"Searched for:\n{old_text}"
#         )

#     updated = content.replace(old_text, new_text, 1)
#     file_path.write_text(updated, encoding="utf-8")
#     return f"Successfully patched {path}"

@tool
def replace_in_file(path: str, old_text: str, new_text: str) -> str:
    """
Replace a specific block of text in a file with new text.
    This is the PREFERRED editing tool — use it for targeted, minimal patches.
    old_text must match the file content exactly (including whitespace/indentation).
    Only the first occurrence is replaced.
    """

    file_path = safe_path(path)

    original = file_path.read_text(encoding="utf-8")

    if old_text not in original:
        return "Target text not found in file."

    updated = original.replace(old_text, new_text, 1)

    approved = apply_fix(file_path, updated)

    if approved:
        return f"Successfully patched {path}"

    return f"Patch rejected for {path}"


# @tool
# def run_shell(command: str) -> str:
#     """
#     Execute a shell command inside the workspace directory and return its output.
#     Use this to run the failing command after applying a fix to verify it works.
#     Always call this after every patch to confirm the error is resolved.
#     """
#     result = subprocess.run(
#         command,
#         shell=True,
#         capture_output=True,
#         text=True,
#         cwd=str(BASE_DIR),
#         timeout=120,
#     )

#     parts = []
#     if result.stdout:
#         parts.append("STDOUT:\n" + result.stdout.strip())
#     if result.stderr:
#         parts.append("STDERR:\n" + result.stderr.strip())
#     parts.append(f"Exit Code: {result.returncode}")

#     return "\n\n".join(parts)
@tool
def run_shell(command: str) -> str:
    """
    Execute a shell command inside the workspace directory.

    ALWAYS requires human approval before execution.

    Use this to:
    - run tests
    - reproduce bugs
    - verify fixes
    - inspect runtime behavior
    """

    console.print()

    console.print(
        Panel(
            command,
            title="[bold yellow]Proposed Shell Command[/bold yellow]",
            border_style="yellow",
            expand=False,
        )
    )

    approved = Confirm.ask(
        "[bold yellow]Run this command?[/bold yellow]"
    )

    if not approved:
        return "Shell command rejected by user."

    try:

        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
            timeout=120,
        )

        parts = []

        if result.stdout:
            parts.append(
                "STDOUT:\n" + result.stdout.strip()
            )

        if result.stderr:
            parts.append(
                "STDERR:\n" + result.stderr.strip()
            )

        parts.append(
            f"Exit Code: {result.returncode}"
        )

        return "\n\n".join(parts)

    except subprocess.TimeoutExpired:
        return "Command timed out after 120 seconds."

    except Exception as e:
        return f"Shell execution failed: {e}"

# ── All tools (import this list in agent2.py) ────────────────────────────────

ALL_TOOLS = [
    list_files,
    run_shell
    
    
]