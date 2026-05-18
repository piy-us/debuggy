# #!/usr/bin/env python3
# """
# debuggy3.py — CLI-based autonomous code debugger.

# Usage:
#     python debuggy3.py fix "python myapp.py"
# """
# from __future__ import annotations

# import ast
# import difflib
# import json
# import os
# import re
# import shutil
# import subprocess
# import sys
# from dataclasses import dataclass, field
# from pathlib import Path
# from typing import List, Optional

# import typer
# from groq import Groq
# from rich.console import Console
# from rich.panel import Panel
# from rich.prompt import Confirm
# from rich.syntax import Syntax
# from rich.text import Text

# from agent2 import ask_agent

# # ── Config ────────────────────────────────────────────────────────────────────

# MAX_RETRIES = 6
# MAX_DIFF_LINES = 80          # reject patches larger than this
# console = Console()
# app = typer.Typer()

# # ── Data class ────────────────────────────────────────────────────────────────


# @dataclass
# class ParsedError:
#     language: str
#     error_type: str
#     error_message: str
#     file_path: Optional[Path]
#     line_number: Optional[int]
#     relevant_lines: List[str] = field(default_factory=list)
#     raw_stderr: str = ""

# # ── Stderr helpers ────────────────────────────────────────────────────────────


# def read_stderr_file(path: Path) -> str:
#     raw = path.read_bytes()
#     if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
#         return raw.decode("utf-16")
#     return raw.decode("utf-8", errors="replace")


# def clean_powershell_noise(stderr: str) -> str:
#     lines = stderr.splitlines()
#     cleaned = []
#     skip = ("At line:", "+ ", "    + ", "CategoryInfo", "FullyQualifiedErrorId")
#     for line in lines:
#         if re.match(r'^\w[\w. ]+ : ', line):
#             line = re.sub(r'^\w[\w. ]+ : ', '', line)
#         if any(line.strip().startswith(s) for s in skip):
#             continue
#         cleaned.append(line)
#     return "\n".join(cleaned)


# def get_lines_around(path: Path, line_no: int, context: int = 12) -> List[str]:
#     try:
#         lines = path.read_text(encoding="utf-8").splitlines()
#         start = max(0, line_no - context - 1)
#         end = min(len(lines), line_no + context)
#         out = []
#         for i, l in enumerate(lines[start:end], start=start):
#             marker = ">>>" if (i + 1) == line_no else "   "
#             out.append(f"{marker} {i+1:4}: {l}")
#         return out
#     except Exception:
#         return []

# # ── Traceback parser ──────────────────────────────────────────────────────────


# def parse_traceback(stderr: str) -> ParsedError:
#     stderr = clean_powershell_noise(stderr)

#     # Python
#     py_matches = re.findall(r'File "([^"]+)", line (\d+)', stderr)
#     if py_matches:
#         your_files = [
#             (f, int(l)) for f, l in py_matches
#             if not any(s in f for s in ('site-packages', 'venv', '<frozen'))
#         ]
#         if your_files:
#             file_path, line_no = your_files[-1]
#             last_line = stderr.strip().splitlines()[-1]
#             parts = last_line.split(":", 1)
#             error_type = parts[0].strip()
#             error_msg = parts[1].strip() if len(parts) > 1 else last_line
#             p = Path(file_path)
#             return ParsedError(
#                 language="python",
#                 error_type=error_type,
#                 error_message=error_msg,
#                 file_path=p if p.exists() else None,
#                 line_number=line_no,
#                 relevant_lines=get_lines_around(p, line_no) if p.exists() else [],
#                 raw_stderr=stderr,
#             )

#     # Node.js
#     node_matches = re.findall(r'at .+?\((.+?):(\d+):\d+\)', stderr)
#     if node_matches:
#         your_files = [
#             (f, int(l)) for f, l in node_matches
#             if not any(s in f for s in ('node_modules', 'node:internal', '<anonymous>'))
#         ]
#         if your_files:
#             file_path, line_no = your_files[0]
#             first_line = stderr.strip().splitlines()[0]
#             parts = first_line.split(":", 1)
#             error_type = parts[0].strip()
#             error_msg = parts[1].strip() if len(parts) > 1 else first_line
#             p = Path(file_path)
#             return ParsedError(
#                 language="javascript",
#                 error_type=error_type,
#                 error_message=error_msg,
#                 file_path=p if p.exists() else None,
#                 line_number=line_no,
#                 relevant_lines=get_lines_around(p, line_no) if p.exists() else [],
#                 raw_stderr=stderr,
#             )

#     # Fallback
#     return ParsedError(
#         language="unknown",
#         error_type="Error",
#         error_message=stderr.strip().splitlines()[-1] if stderr.strip() else "unknown error",
#         file_path=None,
#         line_number=None,
#         raw_stderr=stderr,
#     )

# # ── Prompt builder ────────────────────────────────────────────────────────────


# def build_prompt(error: ParsedError, command: str) -> str:
#     """
#     Build a rich, context-aware prompt for the repair agent.
#     Includes: crash location, surrounding lines, full file contents,
#     imports, and the full stderr — so the agent never needs to guess.
#     """
#     crash_location = (
#         f"File: {error.file_path}, line {error.line_number}"
#         if error.file_path else "Could not identify specific file"
#     )
#     relevant = "\n".join(error.relevant_lines) or "(not available)"

#     # Read full file content so the agent has complete context
#     full_file_section = ""
#     if error.file_path and error.file_path.exists():
#         full_content = error.file_path.read_text(encoding="utf-8")
#         full_file_section = (
#             f"\nFULL FILE CONTENTS ({error.file_path}):\n"
#             f"```{error.language}\n{full_content}\n```\n"
#         )

#     return f"""Fix a {error.language} runtime error.

# COMMAND THAT FAILED:
# {command}

# ERROR:
# {error.error_type}: {error.error_message}

# CRASH LOCATION:
# {crash_location}

# LINES AROUND THE CRASH:
# {relevant}
# {full_file_section}
# FULL STDERR:
# {error.raw_stderr[:3000]}

# INSTRUCTIONS:
# 1. Read any files you need using read_file before editing.
# 2. Use replace_in_file for minimal targeted patches — do NOT rewrite whole files.
# 3. After patching, run the command with run_shell to verify the fix.
# 4. Preserve ALL existing functions, classes, and business logic.
# 5. Return your final answer as JSON with status "fixed" when done.
# """

# # ── Validation helpers ────────────────────────────────────────────────────────


# def validate_python_syntax(content: str, path: str) -> bool:
#     """Return True if content is valid Python, False otherwise."""
#     try:
#         ast.parse(content)
#         return True
#     except SyntaxError as e:
#         console.print(f"[red]Syntax error in proposed fix for {path}: {e}[/red]")
#         return False


# def extract_function_names(content: str) -> set[str]:
#     """Return a set of top-level function/class names defined in Python source."""
#     try:
#         tree = ast.parse(content)
#         return {
#             node.name
#             for node in ast.walk(tree)
#             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
#         }
#     except Exception:
#         return set()


# def diff_line_count(old: str, new: str) -> int:
#     diff = list(difflib.unified_diff(
#         old.splitlines(), new.splitlines(), lineterm=""
#     ))
#     return len([l for l in diff if l.startswith(('+', '-')) and not l.startswith(('+++', '---'))])

# # ── Display & apply fix ───────────────────────────────────────────────────────


# def show_diff(file_path: Path, fixed_content: str) -> None:
#     old_content = file_path.read_text(encoding="utf-8")
#     diff = difflib.unified_diff(
#         old_content.splitlines(),
#         fixed_content.splitlines(),
#         fromfile=str(file_path),
#         tofile=f"{file_path} (fixed)",
#         lineterm="",
#     )
#     diff_text = "\n".join(diff)
#     if not diff_text.strip():
#         console.print("[yellow]No changes detected.[/yellow]")
#         return
#     console.print(Syntax(diff_text, "diff", theme="monokai", line_numbers=False))


# def apply_fix(file_path: Path, fixed_content: str) -> bool:
#     """Show diff, run guards, ask for confirmation, then write the fix."""
#     old_content = file_path.read_text(encoding="utf-8")

#     # ── Guard: diff size ──────────────────────────────────────────────────────
#     changed = diff_line_count(old_content, fixed_content)
#     if changed > MAX_DIFF_LINES:
#         console.print(
#             f"[red]Rejecting fix: {changed} changed lines exceeds limit of "
#             f"{MAX_DIFF_LINES}. This looks like a full rewrite.[/red]"
#         )
#         return False

#     # ── Guard: Python syntax ──────────────────────────────────────────────────
#     if file_path.suffix == ".py" and not validate_python_syntax(fixed_content, str(file_path)):
#         console.print("[red]Rejecting fix: proposed content has syntax errors.[/red]")
#         return False

#     # ── Guard: regression — no functions deleted ──────────────────────────────
#     if file_path.suffix == ".py":
#         old_names = extract_function_names(old_content)
#         new_names = extract_function_names(fixed_content)
#         removed = old_names - new_names
#         if removed:
#             console.print(
#                 f"[red]Rejecting fix: the following functions/classes would be "
#                 f"deleted: {', '.join(sorted(removed))}[/red]"
#             )
#             return False

#     # ── Show diff and ask user ────────────────────────────────────────────────
#     console.print(Panel(
#         Text(str(file_path), style="bold"),
#         title="[cyan]Proposed fix[/cyan]",
#         border_style="cyan",
#         expand=False,
#     ))
#     show_diff(file_path, fixed_content)

#     if not Confirm.ask("\n  Apply this fix?"):
#         console.print("[yellow]  Cancelled.[/yellow]")
#         return False

#     backup = file_path.with_suffix(file_path.suffix + ".bak")
#     shutil.copy(file_path, backup)
#     file_path.write_text(fixed_content, encoding="utf-8")
#     console.print(f"[green]  ✓ Applied.[/green] [dim]Backup → {backup}[/dim]")
#     return True

# # ── Shell runner ──────────────────────────────────────────────────────────────


# def run_command(command: str):
#     if command.strip().endswith(".py") and not command.strip().startswith("python"):
#         command = f"python {command}"
#     result = subprocess.run(
#         command, shell=True, capture_output=True, text=True
#     )
#     return result.stdout, result.stderr, result.returncode

# # ── Agent response parser ─────────────────────────────────────────────────────


# def parse_agent_response(raw: str) -> dict:
#     """
#     Parse the agent's JSON response.
#     Handles: plain JSON, JSON wrapped in markdown fences, or plain text fallback.
#     """
#     raw = raw.strip()

#     # Strip markdown fences if present
#     raw = re.sub(r'^```(?:json)?\s*', '', raw)
#     raw = re.sub(r'\s*```$', '', raw)

#     try:
#         return json.loads(raw)
#     except json.JSONDecodeError:
#         # Try to extract the first {...} block
#         match = re.search(r'\{.*\}', raw, re.DOTALL)
#         if match:
#             try:
#                 return json.loads(match.group(0))
#             except json.JSONDecodeError:
#                 pass

#     # Give up — return a minimal structure
#     return {"thought": raw, "actions": [], "status": "needs_more_work"}


# def extract_write_actions(agent_response: dict) -> dict[str, str]:
#     """
#     Extract any write_file actions from the agent's action list.
#     Returns {path: content}.
#     Falls back to scanning for legacy <fixed_file> XML blocks too.
#     """
#     fixes: dict[str, str] = {}

#     for action in agent_response.get("actions", []):
#         if action.get("tool") == "write_file":
#             args = action.get("arguments", {})
#             path = args.get("path")
#             content = args.get("content")
#             if path and content:
#                 fixes[path] = content

#     # Legacy fallback: <fixed_file path="..."> blocks in the thought field
#     thought = agent_response.get("thought", "")
#     legacy = re.findall(
#         r'<fixed_file path="([^"]+)">\s*(.*?)\s*</fixed_file>',
#         thought,
#         re.DOTALL,
#     )
#     for path, content in legacy:
#         content = re.sub(r'^```\w*\n?', '', content)
#         content = re.sub(r'\n?```$', '', content)
#         if path not in fixes:
#             fixes[path] = content.strip()

#     return fixes

# # ── Main fix loop ─────────────────────────────────────────────────────────────


# def cmd_fix(command: str) -> None:
#     if not command:
#         console.print("[red]Please provide a command to run.[/red]")
#         raise typer.Exit(1)

#     console.print(f"\n[cyan]Running:[/cyan] {command}")
#     stdout, stderr, code = run_command(command)

#     if code == 0:
#         console.print("[green]Program exited successfully. Nothing to fix.[/green]")
#         if stdout.strip():
#             console.print(stdout)
#         raise typer.Exit(0)

#     if not stderr.strip():
#         console.print("[yellow]Empty stderr — nothing to fix.[/yellow]")
#         raise typer.Exit(0)

#     error = parse_traceback(stderr)
#     console.print(
#         f"\n[bold cyan]debuggy[/bold cyan] detected: "
#         f"[red]{error.error_type}[/red] — {error.error_message}"
#     )
#     if error.file_path:
#         console.print(f"  File: [bold]{error.file_path}[/bold]  line {error.line_number}")
#     else:
#         console.print("  [yellow]Could not identify a source file.[/yellow]")

#     for attempt in range(1, MAX_RETRIES + 1):
#         console.print(f"\n[bold cyan]─── Attempt {attempt}/{MAX_RETRIES} ───[/bold cyan]")

#         # Re-run to get the latest error state
#         stdout, stderr, code = run_command(command)

#         if code == 0:
#             console.print("\n[bold green]✓ Program running successfully![/bold green]")
#             if stdout.strip():
#                 console.print("\n[cyan]Output:[/cyan]")
#                 console.print(stdout)
#             return

#         error = parse_traceback(stderr)
#         console.print(f"[red]{error.error_type}[/red]: {error.error_message}")

#         # Build prompt and call agent
#         prompt = build_prompt(error, command)
#         raw_response = ask_agent(prompt)
#         agent_resp = parse_agent_response(raw_response)

#         console.print(f"\n[dim]Agent thought:[/dim] {agent_resp.get('thought', '(none)')[:200]}")

#         status = agent_resp.get("status", "needs_more_work")

#         if status == "fixed":
#             # Agent claims it fixed things via tool calls — re-run to confirm
#             console.print("[green]Agent reports fix applied via tools. Verifying...[/green]")
#             stdout, stderr, code = run_command(command)
#             if code == 0:
#                 console.print("[bold green]✓ Verified — program runs successfully![/bold green]")
#                 return
#             else:
#                 console.print("[yellow]Agent said 'fixed' but program still fails. Retrying...[/yellow]")
#                 error = parse_traceback(stderr)
#                 continue

#         # Check for explicit write_file actions in the response
#         fixes = extract_write_actions(agent_resp)

#         if not fixes:
#             console.print(
#                 "[yellow]No file fixes in agent response. "
#                 "Agent may have patched via tools already — re-running command...[/yellow]"
#             )
#             # The agent may have used replace_in_file/run_shell directly;
#             # just loop back and re-check.
#             continue

#         # Apply any explicit file rewrites the agent returned
#         any_applied = False
#         for fix_path_str, fix_content in fixes.items():
#             p = Path(fix_path_str)
#             if not p.exists() and error.file_path:
#                 p = error.file_path.parent / fix_path_str
#             if p.exists():
#                 applied = apply_fix(p, fix_content)
#                 if applied:
#                     any_applied = True
#             else:
#                 console.print(f"[yellow]Skipping {fix_path_str} — file not found.[/yellow]")

#         if not any_applied:
#             console.print("[red]No fixes could be applied.[/red]")

#     console.print("\n[bold red]Exceeded maximum retries without a successful fix.[/bold red]")

# # ── CLI ───────────────────────────────────────────────────────────────────────


# @app.command()
# def fix(command: str = typer.Argument(..., help="Command to run, e.g. 'python app.py'")):
#     """Run COMMAND, detect errors, and autonomously attempt to fix them."""
#     cmd_fix(command)

# @app.command()
# def donothing():
#     pass

# if __name__ == "__main__":
#     app()

