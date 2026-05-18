#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import re
import argparse
import subprocess
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List
import difflib
from groq import Groq
from rich.console import Console
from rich.prompt import Confirm
from rich.panel import Panel
from rich.text import Text
from rich.panel import Panel
# ── Config ─────────────────────────────────────────────────────────────────

# MODEL   = "openai/gpt-oss-20b"
# console = Console()
# client  = Groq(api_key=os.environ.get("GROQ_API_KEY"))
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from dotenv import load_dotenv
load_dotenv()
console = Console()
memory  = MemorySaver()

llm = ChatGoogleGenerativeAI(
    model="gemini-3.1-flash-lite",
    temperature=0.1,
    api_key=os.environ.get("GEMINI_API_KEY"),
)

# ── Data class ──────────────────────────────────────────────────────────────
@dataclass
class ParsedError:
    language: str
    error_type: str
    error_message: str
    file_path: Optional[Path]
    line_number: Optional[int]
    relevant_lines: List[str] = field(default_factory=list)
    raw_stderr: str = ""

# ── Helpers ─────────────────────────────────────────────────────────────────
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


def read_stderr_file(path: Path) -> str:
    """Read stderr file regardless of encoding (handles PowerShell UTF-16)."""
    raw = path.read_bytes()
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode("utf-16")
    return raw.decode("utf-8", errors="replace")


def clean_powershell_noise(stderr: str) -> str:
    """Strip PowerShell wrapper lines that pollute the real traceback."""
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
    """Return lines around the crash point with markers."""
    try:
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

# ── Traceback parser ────────────────────────────────────────────────────────

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


# Root cause first, then the fixed file. Nothing after the closing tag."""
def build_prompt(error: ParsedError, full_file: str, command: str) -> str:
    crash_location = (
        f"File: {error.file_path}, line {error.line_number}"
        if error.file_path else "Could not identify specific file"
    )
    relevant     = "\n".join(error.relevant_lines) or "(not available)"
    file_section = (
        f"CRASH FILE ({error.file_path}):\n```{error.language}\n{full_file}\n```"
        if full_file else "(file content not available)"
    )

    # collect all other files in the project
    repo_section = ""
    if error.file_path:
        repo_files = collect_repo_files(error.file_path.parent)
        other_files = {
            p: content for p, content in repo_files.items()
            if p != str(error.file_path)
        }
        if other_files:
            parts = []
            for path, content in other_files.items():
                parts.append(f"FILE: {path}\n```{error.language}\n{content}\n```")
            repo_section = "OTHER FILES IN PROJECT:\n\n" + "\n\n".join(parts)

    return f"""You are a senior software engineer helping fix a {error.language} runtime error.

COMMAND THAT FAILED:
{command}

ERROR:
{error.error_type}: {error.error_message}

CRASH LOCATION:
{crash_location}

LINES AROUND THE CRASH:
{relevant}

FULL STDERR:
{error.raw_stderr[:2000]}

{file_section}

{repo_section}

INSTRUCTIONS:
1. In 1-2 sentences, explain the root cause.
2. You may need to fix multiple files. For each file that needs changes return a block like this:

<fixed_file path="relative/path/to/file.py">
... complete file content ...
</fixed_file>

Root cause first, then the fixed file blocks. Nothing after the last closing tag."""

# ── Groq API ────────────────────────────────────────────────────────────────

# def call_groq(prompt: str) -> str:
#     console.print("\n[bold cyan]debuggy[/bold cyan] [dim]thinking...[/dim]\n")
#     full_response = ""
#     stream = client.chat.completions.create(
#         model       = MODEL,
#         messages    = [{"role": "user", "content": prompt}],
#         stream      = True,
#         max_tokens  = 4096,
#         temperature = 0.2,
#     )
#     for chunk in stream:
#         delta = chunk.choices[0].delta.content
#         if delta:
#             print(delta, end="", flush=True)
#             full_response += delta
#     print("\n")
#     return full_response
def call_gemini(prompt: str) -> str:
    console.print("\n[bold cyan]debuggy[/bold cyan] [dim]thinking...[/dim]\n")

    full_response = ""

    for chunk in llm.stream(prompt):

        # Gemini may return string OR list
        if isinstance(chunk.content, str):
            text = chunk.content

        elif isinstance(chunk.content, list):
            text = "".join(
                part.get("text", "")
                for part in chunk.content
                if isinstance(part, dict)
            )

        else:
            text = str(chunk.content)

        if text:
            print(text, end="", flush=True)
            full_response += text

    print("\n")
    return full_response

def collect_repo_files(root: Path, extensions: tuple = (".py", ".js", ".ts"), max_files: int = 30) -> dict[str, str]:
    """Walk the project directory and read all source files."""
    files = {}
    for path in sorted(root.rglob("*")):
        if path.suffix not in extensions:
            continue
        if any(s in path.parts for s in ("node_modules", ".venv", "venv", "__pycache__", ".git")):
            continue
        if len(files) >= max_files:
            break
        try:
            files[str(path)] = path.read_text(encoding="utf-8")
        except Exception:
            continue
    return files


def extract_fixed_files(response: str) -> dict[str, str]:
    """Returns {filepath: content} for every <fixed_file path="..."> block."""
    matches = re.findall(
        r'<fixed_file path="([^"]+)">\s*(.*?)\s*</fixed_file>',
        response,
        re.DOTALL
    )
    result = {}
    for path, content in matches:
        content = re.sub(r'^```\w*\n?', '', content)
        content = re.sub(r'\n?```$', '', content)
        result[path] = content.strip()
    return result


import difflib
from rich.syntax import Syntax

def show_diff(file_path: Path, fixed_content: str):
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
        return

    syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=False)
    console.print(syntax)


def apply_fix(file_path: Path, fixed_content: str, command: str) -> bool:
    console.print(Panel(
        Text(str(file_path), style="bold"),
        title="[cyan]Proposed fix[/cyan]",
        border_style="cyan",
        expand=False,
    ))
    show_diff(file_path, fixed_content)

    if not Confirm.ask("\n  Apply this fix?"):
        console.print("[yellow]  Cancelled.[/yellow]")
        return False

    backup = file_path.with_suffix(file_path.suffix + ".bak")
    shutil.copy(file_path, backup)
    file_path.write_text(fixed_content, encoding="utf-8")
    console.print(f"[green]  ✓ Applied.[/green] [dim]Backup saved to {backup}[/dim]")
    return True


def run_command(command: str):
    # if the command is just a .py file path, prepend python
    if command.strip().endswith(".py") and not command.strip().startswith("python"):
        command = f"python {safe_path(command.strip())}"

    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True
    )
    return result.stdout, result.stderr, result.returncode
    
def cmd_fix(args):
    if args.cmd:
        console.print(f"[cyan]Running:[/cyan] {args.cmd}")

        stdout, stderr, code = run_command(args.cmd)

        if code == 0:
            console.print("[green]Program exited successfully. Nothing to fix.[/green]")
            sys.exit(0)

    else:
        console.print("[red]Please provide --cmd[/red]")
        sys.exit(1)

    if not stderr.strip():
        console.print("[yellow]Empty stderr — nothing to fix.[/yellow]")
        sys.exit(0)

    error = parse_traceback(stderr)

    console.print(f"\n[bold cyan]debuggy[/bold cyan] detected: "
                  f"[red]{error.error_type}[/red] — {error.error_message}")

    if error.file_path:
        console.print(f"  File: [bold]{error.file_path}[/bold]  line {error.line_number}")
    else:
        console.print("  [yellow]Could not identify a source file.[/yellow]")

    full_file = ""
    if error.file_path and error.file_path.exists():
        full_file = error.file_path.read_text(encoding="utf-8")


    command = args.cmd or (f"python {error.file_path}" if error.file_path else "")
    MAX_RETRIES = 6
    attempt = 1

    while attempt <= MAX_RETRIES:

        console.print(
            f"\n[bold cyan]Attempt {attempt}/{MAX_RETRIES}[/bold cyan]"
        )

        stdout, stderr, code = run_command(command)

        # SUCCESS
        if code == 0:

            console.print(
                "\n[bold green]✓ Program running successfully![/bold green]"
            )

            if stdout.strip():
                console.print("\n[cyan]Program output:[/cyan]")
                console.print(stdout)

            return

        # FAILURE
        error = parse_traceback(stderr)

        console.print(
            f"\n[red]{error.error_type}[/red]: {error.error_message}"
        )

        full_file = ""

        if error.file_path and error.file_path.exists():
            full_file = error.file_path.read_text(encoding="utf-8")

        prompt = build_prompt(error, full_file, command)

        response = call_gemini(prompt)

        #fixed = extract_fixed_file(response)
        fixes = extract_fixed_files(response)

        if not fixes:
            console.print("[red]No valid fix returned.[/red]")
            attempt += 1
            continue

        for fix_path, fix_content in fixes.items():
            p = safe_path(fix_path)
            if not p.exists():
                # try relative to crash file's directory
                p = error.file_path.parent / fix_path
            if p.exists():
                applied = apply_fix(p, fix_content, command)
                if not applied:
                    return
            else:
                console.print(f"[yellow]Skipping {fix_path} — file not found.[/yellow]")


    console.print(
        "\n[bold red]Exceeded maximum retries.[/bold red]"
    )

def main():
    if not os.environ.get("GEMINI_API_KEY"):
        console.print("[red]GEMINI_API_KEY not set.[/red]")
        
        sys.exit(1)

    parser = argparse.ArgumentParser(prog="debuggy")
    sub    = parser.add_subparsers(dest="subcommand", required=True)
    
    p_fix = sub.add_parser("fix")
    p_fix.add_argument("--stderr-file")
    p_fix.add_argument("--cmd")
    p_fix.set_defaults(func=cmd_fix)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()