from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax

from agenttester import run_agent, AgentContext, AgentOutput
from context_agent import run_context_agent          # ← new
from state import ParsedTraceback, AttemptSummary, PatchRecord
from runner import run_shell
from parsers import parse_traceback
from patchers import _patch_records

console = Console()
MAX_RETRIES = 5


def cmd_fix(command: str) -> None:
    console.print()
    console.print(Rule("[bold cyan]debuggy[/bold cyan]", style="cyan"))
    console.print(f"  [cyan]Command:[/cyan] {command}\n")

    record = run_shell(command)

    if record.exit_code == 0:
        console.print("[bold green]✓ Already passing. Nothing to fix.[/bold green]")
        return

    if not record.stderr_tail or not record.stderr_tail.strip():
        console.print("[yellow]Empty stderr — nothing to fix.[/yellow]")
        return

    previous_attempts: list[AttemptSummary] = []
    all_patches: list[PatchRecord] = []
    modified_files: list[str] = []

    for attempt in range(1, MAX_RETRIES + 1):

        console.print()
        console.print(Rule(f"[bold cyan]Attempt {attempt} / {MAX_RETRIES}[/bold cyan]", style="cyan"))

        error = parse_traceback(record.stderr_tail)

        console.print(f"  [red]Error:[/red] {error.error_type}: {error.error_message}")

        if error.code_snippet:
            console.print(Panel(
                Syntax(error.code_snippet, "python", theme="monokai", line_numbers=False),
                title="[bold]Crash context[/bold]",
                border_style="dim",
            ))

        attempt_patches: list[PatchRecord] = []

        ctx = AgentContext(
            attempt_number=attempt,
            command=command,
            current_error=error,
            modified_files=modified_files.copy(),
            previous_attempts=previous_attempts[-2:],
            recent_patches=all_patches[-3:],
        )

        # ── Context agent: read-only pass, streams live ────────────────────
        enriched_ctx = run_context_agent(
            ctx,
            thread_id=f"debuggy-{command[:20]}-{attempt}",
        )

        # ── Debug agent: uses enriched context, streams live ───────────────
        result: AgentOutput = run_agent(
            enriched_ctx,
            thread_id=f"debuggy-{command[:20]}-{attempt}",
            patch_records=attempt_patches,
        )

        if not result.completed:
            console.print("[yellow]Agent did not complete successfully.[/yellow]")
            break

        console.print()
        console.print(Rule("[dim]Verifying…[/dim]", style="dim"))

        record = run_shell(command)

        applied_patches = [p for p in attempt_patches if p.applied]
        all_patches.extend(applied_patches)
        modified_files = list(dict.fromkeys(
            modified_files + [p.file_path for p in applied_patches]
        ))

        previous_attempts.append(AttemptSummary(
            attempt_number=attempt,
            goal=command,
            reasoning=result.reasoning_summary,
            actions_taken=result.actions_summary,
            files_modified=[p.file_path for p in applied_patches],
            outcome=result.summary,
            next_hypothesis=result.suggested_next_step,
            success=(record.exit_code == 0),
        ))

        if record.exit_code == 0:
            console.print()
            console.print(Rule("[bold green]Fixed[/bold green]", style="green"))
            console.print("  [green]✓ Command now passes.[/green]")
            if record.stdout_tail and record.stdout_tail.strip():
                console.print(f"\n[cyan]Output:[/cyan]\n{record.stdout_tail}")
            _print_session_summary(previous_attempts, all_patches)
            return

        console.print(f"  [red]Still failing after attempt {attempt}.[/red]")
        if result.suggested_next_step:
            console.print(f"  [dim]Agent suggests: {result.suggested_next_step}[/dim]")

    console.print()
    console.print(Rule("[bold red]Could not fix[/bold red]", style="red"))
    console.print(f"  Failed after {MAX_RETRIES} attempts.")
    _print_session_summary(previous_attempts, all_patches)


def _print_session_summary(attempts: list[AttemptSummary], patches: list[PatchRecord]) -> None:
    if not attempts:
        return
    console.print()
    console.print(Rule("[dim]Session summary[/dim]", style="dim"))
    for a in attempts:
        icon = "[green]✓[/green]" if a.success else "[red]✗[/red]"
        console.print(f"  {icon} Attempt {a.attempt_number}: {a.outcome}")
        if a.next_hypothesis and not a.success:
            console.print(f"    [dim]→ {a.next_hypothesis}[/dim]")
    applied = [p for p in patches if p.applied]
    if applied:
        console.print("\n  [cyan]Files modified:[/cyan]")
        for p in dict.fromkeys(p.file_path for p in applied):
            console.print(f"    {p}")
# # from __future__ import annotations
# from __future__ import annotations

# from rich.console import Console
# from rich.panel import Panel
# from rich.rule import Rule
# from rich.syntax import Syntax

# from agenttester import run_agent, AgentContext, AgentOutput
# from state import ParsedTraceback, AttemptSummary, PatchRecord
# from runner import run_shell
# from parsers import parse_traceback
# from patchers import _patch_records

# console = Console()
# MAX_RETRIES = 5


# def cmd_fix(command: str) -> None:
#     console.print()
#     console.print(Rule("[bold cyan]debuggy[/bold cyan]", style="cyan"))
#     console.print(f"  [cyan]Command:[/cyan] {command}\n")

#     # Initial execution
#     record = run_shell(command)

#     if record.exit_code == 0:
#         console.print("[bold green]✓ Already passing. Nothing to fix.[/bold green]")
#         return

#     if not record.stderr_tail or not record.stderr_tail.strip():
#         console.print("[yellow]Empty stderr — nothing to fix.[/yellow]")
#         return

#     previous_attempts: list[AttemptSummary] = []
#     all_patches: list[PatchRecord] = []
#     modified_files: list[str] = []

#     for attempt in range(1, MAX_RETRIES + 1):

#         console.print()
#         console.print(
#             Rule(
#                 f"[bold cyan]Attempt {attempt} / {MAX_RETRIES}[/bold cyan]",
#                 style="cyan",
#             )
#         )

#         # ALWAYS PARSE CURRENT ERROR
#         error = parse_traceback(record.stderr_tail)

#         console.print(
#             f"  [red]Error:[/red] "
#             f"{error.error_type}: {error.error_message}"
#         )

#         if error.code_snippet:
#             console.print(
#                 Panel(
#                     Syntax(
#                         error.code_snippet,
#                         "python",
#                         theme="monokai",
#                         line_numbers=False,
#                     ),
#                     title="[bold]Crash context[/bold]",
#                     border_style="dim",
#                 )
#             )

#         attempt_patches: list[PatchRecord] = []

#         ctx = AgentContext(
#             attempt_number=attempt,
#             command=command,
#             current_error=error,
#             modified_files=modified_files.copy(),
#             previous_attempts=previous_attempts[-2:],  # keep context small
#             recent_patches=all_patches[-3:],
#         )

#         result: AgentOutput = run_agent(
#             ctx,
#             thread_id=f"debuggy-{command[:20]}-{attempt}",
#             patch_records=attempt_patches,
#         )

#         # If agent failed structurally, stop poisoning loop
#         if not result.completed:
#             console.print(
#                 "[yellow]Agent did not complete successfully.[/yellow]"
#             )
#             break

#         # Verify patch by ACTUALLY re-running
#         console.print()
#         console.print(Rule("[dim]Verifying…[/dim]", style="dim"))

#         record = run_shell(command)

#         # Track applied patches only AFTER verification
#         applied_patches = [
#             p for p in attempt_patches if p.applied
#         ]

#         all_patches.extend(applied_patches)

#         modified_files = list(
#             dict.fromkeys(
#                 modified_files +
#                 [p.file_path for p in applied_patches]
#             )
#         )

#         previous_attempts.append(
#             AttemptSummary(
#                 attempt_number=attempt,
#                 goal=command,
#                 reasoning=result.reasoning_summary,
#                 actions_taken=result.actions_summary,
#                 files_modified=[
#                     p.file_path for p in applied_patches
#                 ],
#                 outcome=result.summary,
#                 next_hypothesis=result.suggested_next_step,
#                 success=(record.exit_code == 0),
#             )
#         )

#         # SUCCESS = runtime success ONLY
#         if record.exit_code == 0:

#             console.print()
#             console.print(
#                 Rule("[bold green]Fixed[/bold green]", style="green")
#             )

#             console.print(
#                 f"  [green]✓ Command now passes.[/green]"
#             )

#             if record.stdout_tail and record.stdout_tail.strip():
#                 console.print(
#                     f"\n[cyan]Output:[/cyan]\n{record.stdout_tail}"
#                 )

#             _print_session_summary(previous_attempts, all_patches)
#             return

#         # Still failing → continue with NEW traceback next loop
#         console.print(
#             f"  [red]Still failing after attempt {attempt}.[/red]"
#         )

#         if result.suggested_next_step:
#             console.print(
#                 f"  [dim]Agent suggests: "
#                 f"{result.suggested_next_step}[/dim]"
#             )

#     console.print()
#     console.print(
#         Rule("[bold red]Could not fix[/bold red]", style="red")
#     )

#     console.print(
#         f"  Failed after {MAX_RETRIES} attempts."
#     )

#     _print_session_summary(previous_attempts, all_patches)


# def _print_session_summary(
#     attempts: list[AttemptSummary],
#     patches: list[PatchRecord],
# ) -> None:

#     if not attempts:
#         return

#     console.print()
#     console.print(
#         Rule("[dim]Session summary[/dim]", style="dim")
#     )

#     for a in attempts:

#         icon = (
#             "[green]✓[/green]"
#             if a.success
#             else "[red]✗[/red]"
#         )

#         console.print(
#             f"  {icon} Attempt {a.attempt_number}: {a.outcome}"
#         )

#         if a.next_hypothesis and not a.success:
#             console.print(
#                 f"    [dim]→ {a.next_hypothesis}[/dim]"
#             )

#     applied = [p for p in patches if p.applied]

#     if applied:

#         console.print("\n  [cyan]Files modified:[/cyan]")

#         for p in dict.fromkeys(
#             p.file_path for p in applied
#         ):
#             console.print(f"    {p}")
# # import time
# # from pathlib import Path

# # from rich.console import Console
# # from rich.panel import Panel
# # from rich.rule import Rule
# # from rich.syntax import Syntax
# # from rich.console import Console
# # from rich.panel import Panel
# # from rich.prompt import Confirm
# # from rich.syntax import Syntax
# # from rich.text import Text
# # from rich.rule import Rule
# # from rich.table import Table
# # from rich import box
# # from pprint import pformat
# # from pathlib import Path

# # from agenttester import run_agent, AgentContext, AgentOutput
# # from state import ParsedTraceback, AttemptSummary, PatchRecord
# # from runner import run_shell
# # from parsers import parse_traceback
# # from patchers import _patch_records

# # console    = Console()
# # MAX_RETRIES = 5


# # def cmd_fix(command: str) -> None:
# #     console.print()
# #     console.print(Rule("[bold cyan]debuggy[/bold cyan]", style="cyan"))
# #     console.print(f"  [cyan]Command:[/cyan] {command}\n")

# #     # ── First run ─────────────────────────────────────────────────────────────
# #     record = run_shell(command)

# #     if record.exit_code == 0:
# #         console.print("[bold green]✓ Already passing. Nothing to fix.[/bold green]")
# #         return

# #     if not record.stderr_tail or not record.stderr_tail.strip():
# #         console.print("[yellow]Empty stderr — nothing to fix.[/yellow]")
# #         return

# #     # ── Session state (grows across attempts) ─────────────────────────────────
# #     previous_attempts: list[AttemptSummary] = []
# #     all_patches:       list[PatchRecord]    = []
# #     modified_files:    list[str]            = []

# #     # ── Retry loop ────────────────────────────────────────────────────────────
# #     for attempt in range(1, MAX_RETRIES + 1):
# #         console.print()
# #         console.print(Rule(
# #             f"[bold cyan]Attempt {attempt} / {MAX_RETRIES}[/bold cyan]",
# #             style="cyan",
# #         ))

# #         # Parse current error
# #         error = parse_traceback(record.stderr_tail)
# #         console.print(f"  [red]Error:[/red] {error.error_type}: {error.error_message}")
# #         print("traceback:", error)
# #         if error.code_snippet:
# #             console.print(Panel(
# #                 Syntax(error.code_snippet, "python", theme="monokai", line_numbers=False),
# #                 title="[bold]Crash context[/bold]",
# #                 border_style="dim",
# #             ))

# #         # Collect patches this attempt produces
# #         attempt_patches: list[PatchRecord] = []
# #         print("attempt_patches before agent:", attempt_patches)
# #         # Build context for agent
# #         ctx = AgentContext(
# #             attempt_number    = attempt,
# #             command           = command,
# #             current_error     = error,
# #             modified_files    = modified_files.copy(),
# #             previous_attempts = previous_attempts.copy(),
# #             recent_patches    = all_patches[-3:],   # last 3 patches as context
# #         )
# #         print("agent context:", ctx)
# #         # Run agent (streams to console, returns typed output)
# #         result: AgentOutput = run_agent(
# #             ctx,
# #             thread_id      = f"debuggy-{command[:20]}-{attempt}",
# #             patch_records  = attempt_patches,
# #         )
# #         print("result from agent:", result)
# #         # Accumulate patches
# #         all_patches.extend(attempt_patches)
# #         modified_files = list(dict.fromkeys(
# #             modified_files + [p.file_path for p in attempt_patches if p.applied]
# #         ))
# #         print("all_patches after agent:", all_patches)
# #         # Record this attempt
# #         previous_attempts.append(AttemptSummary(
# #             attempt_number = attempt,
# #             goal           = command,
# #             reasoning      = result.reasoning_summary,
# #             actions_taken  = result.actions_summary,
# #             files_modified = [p.file_path for p in attempt_patches if p.applied],
# #             outcome        = result.summary,
# #             next_hypothesis= result.suggested_next_step,
# #             success        = result.success_likely,
# #         ))
# #         print("previous_attempts after agent:", previous_attempts)
# #         # ── Verify: re-run the command ─────────────────────────────────────────
# #         # if not result.completed:
# #         #     console.print("  [yellow]Agent did not complete. Retrying…[/yellow]")
# #         #     continue

# #         # console.print()
# #         # console.print(Rule("[dim]Verifying…[/dim]", style="dim"))
# #         # record = run_shell(command)

# #         if record.exit_code == 0:
# #             console.print()
# #             console.print(Rule("[bold green]Fixed[/bold green]", style="green"))
# #             console.print(f"  [green]✓ {result.summary}[/green]")
# #             if record.stdout_tail and record.stdout_tail.strip():
# #                 console.print(f"\n[cyan]Output:[/cyan]\n{record.stdout_tail}")
# #             _print_session_summary(previous_attempts, all_patches)
# #             return

# #         # Still failing — feed new stderr back into next iteration
# #         console.print(f"  [red]Still failing after attempt {attempt}.[/red]")
# #         if result.suggested_next_step:
# #             console.print(f"  [dim]Agent suggests: {result.suggested_next_step}[/dim]")

# #     # ── Exhausted retries ─────────────────────────────────────────────────────
# #     console.print()
# #     console.print(Rule("[bold red]Could not fix[/bold red]", style="red"))
# #     console.print(f"  Failed after {MAX_RETRIES} attempts.")
# #     _print_session_summary(previous_attempts, all_patches)


# # def _print_session_summary(
# #     attempts: list[AttemptSummary],
# #     patches:  list[PatchRecord],
# # ) -> None:
# #     if not attempts:
# #         return
# #     console.print()
# #     console.print(Rule("[dim]Session summary[/dim]", style="dim"))
# #     for a in attempts:
# #         icon = "[green]✓[/green]" if a.success else "[red]✗[/red]"
# #         console.print(f"  {icon} Attempt {a.attempt_number}: {a.outcome}")
# #         if a.next_hypothesis and not a.success:
# #             console.print(f"    [dim]→ {a.next_hypothesis}[/dim]")
# #     applied = [p for p in patches if p.applied]
# #     if applied:
# #         console.print(f"\n  [cyan]Files modified:[/cyan]")
# #         for p in dict.fromkeys(p.file_path for p in applied):
# #             console.print(f"    {p}")
