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
from patchers import validate_python_syntax, extract_function_names, diff_line_count, show_diff, apply_fix

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
    
    
    replace_in_file,
    run_shell,
]
READ_ONLY_TOOLS=[list_files]