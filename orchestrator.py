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
from runner import run_shell
from parsers import parse_traceback
from parsers import _read_snippet
console = Console()
MAX_RETRIES = 5
MAX_DIFF_LINES = 20
from rich.table import Table
from rich.rule  import Rule
from agents.agent2 import ask_agent
def display_parsed_traceback(error: ParsedTraceback) -> None:
    # ── Header ────────────────────────────────────────────────────────────────
    console.print((f"[bold red]{error.error_type}: {error.error_message}[/bold red]"))
    #console.print(f"  [red]{error.error_message}[/red]\n")

    # ── Primary location ──────────────────────────────────────────────────────
    if error.file:
        loc_parts = [f"[cyan]{error.file}[/cyan]"]
        if error.line:
            loc_parts.append(f"line [yellow]{error.line}[/yellow]")
        if error.function:
            loc_parts.append(f"in [bold]{error.function}[/bold]")
        console.print("  " + "  ·  ".join(loc_parts) + "\n")

    # ── Stack frames ──────────────────────────────────────────────────────────
    if error.stack_frames:
        table = Table(
            show_header=True,
            header_style="bold dim",
            border_style="dim",
            box=box.SIMPLE,
            padding=(0, 1),
        )
        table.add_column("#",        style="dim",    width=3,  justify="right")
        table.add_column("File",     style="cyan",   no_wrap=False)
        table.add_column("Line",     style="yellow", width=6,  justify="right")
        table.add_column("Function", style="bold",   width=20)
        table.add_column("Code",     style="white",  no_wrap=False)

        for i, frame in enumerate(error.stack_frames):
            is_focal = (frame.file == error.file and frame.line == error.line)
            style = "on dark_red" if is_focal else ""
            table.add_row(
                str(i + 1),
                frame.file,
                str(frame.line),
                frame.function,
                frame.code or "",
                style=style,
            )

        console.print(table)
def build_prompt(error: ParsedTraceback, command: str) -> str:
    prompt = f"""The following shell command failed with the given error and traceback. Propose a fix to get it running. 
            Command: {command}\n\n
            error_type    = {error.error_type},\n
            error_message = {error.error_message},\n
            file          = {error.file},\n
            line          = {error.line},\n
            function      = {error.function},\n
            code_snippet  = {error.code_snippet},\n
            raw_stderr    = {error.raw_stderr},\n
            stack_frames  = {error.stack_frames},\n
            
"""
    return prompt


def cmd_fix(command: str) -> None:
    console.print()
    console.print(Rule(f"[bold cyan]debuggy[/bold cyan]", style="cyan"))
    console.print(f"  [cyan]Command:[/cyan] {command}\n")

    #results = run_shell(command)

    record = run_shell(command)

    if record.exit_code == 0:
        console.print("[bold green]✓ Program exited successfully. Nothing to fix.[/bold green]")
        if record.stdout_tail:
            console.print(record.stdout_tail)
        return

    if not record.stderr_tail or not record.stderr_tail.strip():
        console.print("[yellow]Empty stderr — nothing to fix.[/yellow]")
        return

    error = parse_traceback(record.stderr_tail)
    #display_parsed_traceback(error)
     # # ── Show crash context ────────────────────────────────────────────────────
    # if error.relevant_lines:
    #     crash_code = "\n".join(error.relevant_lines)
    #     console.print(
    #         Panel(
    #             Syntax(crash_code, error.language, theme="monokai", line_numbers=False),
    #             title="[bold]Crash Context[/bold]",
    #             border_style="dim",
    #         )
    #     )
    if error.code_snippet:
        lang = "python"   # swap for error.language if you add that field back
        console.print(
            Panel(
                Syntax(error.code_snippet, lang, theme="monokai", line_numbers=False),
                title="[bold]Crash Context[/bold]",
                border_style="dim",
            )
        )

    #── Retry loop ────────────────────────────────────────────────────────────
    for attempt in range(1, MAX_RETRIES + 1):
        console.print()
        console.print(Rule(
            f"[bold cyan]Attempt {attempt} / {MAX_RETRIES}[/bold cyan]",
            style="cyan",
        ))
        #record = run_shell(command)

        if record.exit_code == 0:
            console.print("[bold green]✓ Program running successfully![/bold green]")
            if record.stdout_tail and record.stdout_tail.strip():
                console.print("\n[cyan]Output:[/cyan]")
                console.print(record.stdout_tail)
            return

        if not record.stderr_tail or not record.stderr_tail.strip():
            console.print("[yellow]Empty stderr — nothing to fix.[/yellow]")
            return

        error = parse_traceback(record.stderr_tail)
        #display_parsed_traceback(error)
        console.print(f"  [red]Still failing:[/red] {error.error_type}: {error.error_message}")

        #Build prompt and call agent (single API call)
        prompt = build_prompt(error, command)
        print(ask_agent(prompt))

    #     # ── Show agent activity (tool calls + results) ────────────────────────
    #     if msgs:
    #         print_agent_activity(msgs)

    #     # ── Show agent's final thought ────────────────────────────────────────
    #     thought = agent_resp.get("thought", "").strip()
    #     if thought:
    #         console.print(
    #             Panel(
    #                 thought[:600] + ("…" if len(thought) > 600 else ""),
    #                 title="[bold blue]Agent Analysis[/bold blue]",
    #                 border_style="blue",
    #             )
    #         )

    #     # Check for explicit write_file actions
    #     fixes = extract_write_actions(agent_resp)
    #     console.print(f"[cyan]Extracted fixes:[/cyan] {list(fixes.keys())}")

    #     if not fixes:
    #         console.print(
    #             "  [red]Agent did not return any proposed fixes.[/red]"
    #         )
    #         continue

    #     any_applied = False
    #     for fix_path_str, fix_content in fixes.items():
    #         p = Path(fix_path_str)
    #         if not p.exists() and error.file_path:
    #             p = error.file_path.parent / fix_path_str
    #         if p.exists():
    #             console.print(f"\n  [cyan]Proposed fix for:[/cyan] [bold]{p}[/bold]")
    #             # if apply_fix(p, fix_content):
    #             #     any_applied = True
    #             if apply_fix(p, fix_content):
    #                 any_applied = True

    #                 console.print(
    #                     "\n[cyan]Re-running command to verify fix...[/cyan]\n"
    #                 )
    #         else:
    #             console.print(f"  [yellow]Skipping {fix_path_str} — file not found.[/yellow]")

    #     if not any_applied:
    #         console.print("  [red]No fixes could be applied this round.[/red]")
    #         continue

    console.print(f"\n[bold red]✗ Failed after {MAX_RETRIES} attempts.[/bold red]")


