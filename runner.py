import os
import re
import subprocess
import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax
from rich.text import Text
from rich.rule import Rule
from rich.table import Table
from rich import box
from pprint import pformat
from pathlib import Path
#from parser import parse_traceback
from pydantic import BaseModel, Field
import time 

console = Console()
_TAIL_CHARS = 4_000   # keep last N chars for stdout/stderr tails

BASE_DIR = Path("./workspace").resolve()

def safe_path(path: str) -> Path:
    
    full = (BASE_DIR / path).resolve()
    if not str(full).startswith(str(BASE_DIR)):
        raise ValueError(f"Path '{path}' escapes workspace — rejected.")
    
    return full

def _tail(text: str, max_chars: int = _TAIL_CHARS) -> str | None:
    if not text:
        return None
    return text[-max_chars:] if len(text) > max_chars else text


class ShellCommandRecord(BaseModel):
    command: str
 
    cwd: str | None = None
 
    exit_code: int
 
    stdout_tail: str | None = None
    stderr_tail: str | None = None
 
    duration_sec: float
 
    timestamp: float = Field(default_factory=time.time)
 
    # Populated when stderr contains a recognisable traceback.
    #parsed_traceback: ParsedTraceback | None = None


# ── Shell execution ───────────────────────────────────────────────────────────
def run_shell(command: str) -> ShellCommandRecord:
    """
    Execute a shell command (after user confirmation) and return a
    fully-populated ShellCommandRecord — including a parsed traceback
    when stderr looks like a runtime failure.
    """
 
    # Auto-prefix bare .py paths
    if re.match(r'^[\w./ \\:]+\.py$', command) and not command.startswith("python"):
        command = f"python {safe_path(command)}"
 
    console.print()
    console.print(
        Panel(
            command,
            title="[bold yellow]Proposed Shell Command[/bold yellow]",
            border_style="yellow",
            expand=False,
        )
    )
 
    if not Confirm.ask("[bold yellow]Run this command?[/bold yellow]"):
        # Return a minimal record representing the rejection
        return ShellCommandRecord(
            command      = command,
            cwd          = str(Path.cwd()),
            exit_code    = -1,
            stdout_tail  = None,
            stderr_tail  = "Shell command rejected by user.",
            duration_sec = 0.0,
        )
 
    t0 = time.time()
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return ShellCommandRecord(
            command      = command,
            cwd          = str(Path.cwd()),
            exit_code    = -1,
            stderr_tail  = "Command timed out after 120 seconds.",
            duration_sec = time.time() - t0,
        )
    except Exception as exc:
        return ShellCommandRecord(
            command      = command,
            cwd          = str(Path.cwd()),
            exit_code    = -1,
            stderr_tail  = f"Shell execution failed: {exc}",
            duration_sec = time.time() - t0,
        )
 
    duration = time.time() - t0
 
    # Display output in the terminal (unchanged UX)
    parts = []
    if result.stdout:
        parts.append("STDOUT:\n" + result.stdout.strip())
    if result.stderr:
        parts.append("STDERR:\n" + result.stderr.strip())
    parts.append(f"Exit Code: {result.returncode}")
    console.print(
        Panel(
            "\n\n".join(parts),
            title="[bold cyan]Command Output[/bold cyan]",
            border_style="cyan",
        )
    )
 
    # Parse traceback only when there's something in stderr
    #parsed = parse_traceback(result.stderr) if result.stderr.strip() else None
 
    return ShellCommandRecord(
        command          = command,
        cwd              = str(Path.cwd()),
        exit_code        = result.returncode,
        stdout_tail      = _tail(result.stdout),
        stderr_tail      = _tail(result.stderr),
        duration_sec     = duration,
        #parsed_traceback = parsed,
    )
 

# def run_command(command: str):
#     cmd = command.strip()

#     if re.match(r'^[\w./ \\:]+\.py$', cmd) and not cmd.startswith("python"):
#         cmd = f"python {cmd}"

#     try:
#         result = subprocess.run(
#             cmd,
#             shell=True,
#             capture_output=True,
#             text=True,
#             timeout=15,   # prevents hanging forever
#         )

#         return result.stdout, result.stderr, result.returncode

#     except subprocess.TimeoutExpired:
#         return (
#             "",
#             "Process timed out after 15 seconds.",
#             124,
#         )

# def run_shell(command: str) -> str:
#     """
#     Execute shell command with user confirmation.
#     """
#     if re.match(r'^[\w./ \\:]+\.py$', command) and not command.startswith("python"):
#         command = f"python {command}"
#     console.print()

#     console.print(
#         Panel(
#             command,
#             title="[bold yellow]Proposed Shell Command[/bold yellow]",
#             border_style="yellow",
#             expand=False,
#         )
#     )

#     approved = Confirm.ask(
#         "[bold yellow]Run this command?[/bold yellow]"
#     )

#     if not approved:
#         return "Shell command rejected by user."

#     try:

#         result = subprocess.run(
#             command,
#             shell=True,
#             capture_output=True,
#             text=True,
            
#             timeout=120,
#         )

#         parts = []

#         if result.stdout:
#             parts.append(
#                 "STDOUT:\n" + result.stdout.strip()
#             )

#         if result.stderr:
#             parts.append(
#                 "STDERR:\n" + result.stderr.strip()
#             )

#         parts.append(
#             f"Exit Code: {result.returncode}"
#         )
#         parts ="\n\n".join(parts)   
#         console.print(
#             Panel(
#                 parts,
#                 title="[bold cyan]Command Output[/bold cyan]",
#                 border_style="cyan",
#             )
#         )
#         #return "\n\n".join(parts)
#         return result.stdout, result.stderr, result.returncode

#     except subprocess.TimeoutExpired:
#         return "Command timed out after 120 seconds."

#     except Exception as e:
#         return f"Shell execution failed: {e}"
