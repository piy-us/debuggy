import ast
import difflib
import shutil
from pathlib import Path
from rich.console import Console
from rich.rule import Rule
from state import _patch_records, PatchRecord
from rich.syntax import Syntax
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

console = Console()

MAX_DIFF_LINES = 80

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

# def apply_fix(file_path: Path, fixed_content: str) -> bool:
#     """
#     Show guards → show diff → ask user → write file.
#     Returns True if the fix was applied, False if rejected/cancelled.
#     """
#     old_content = file_path.read_text(encoding="utf-8")

#     # Guard: diff size
#     changed = diff_line_count(old_content, fixed_content)
#     if changed > MAX_DIFF_LINES:
#         console.print(
#             f"  [red]✗ Rejecting: {changed} changed lines exceeds safety limit of "
#             f"{MAX_DIFF_LINES}. Looks like a full rewrite.[/red]"
#         )
#         return False

#     # Guard: Python syntax
#     if file_path.suffix == ".py" and not validate_python_syntax(fixed_content, str(file_path)):
#         console.print("  [red]✗ Rejecting: proposed content has syntax errors.[/red]")
#         return False

#     # Guard: no functions deleted
#     if file_path.suffix == ".py":
#         old_names = extract_function_names(old_content)
#         new_names = extract_function_names(fixed_content)
#         removed   = old_names - new_names
#         if removed:
#             console.print(
#                 f"  [red]✗ Rejecting: these would be deleted: "
#                 f"{', '.join(sorted(removed))}[/red]"
#             )
#             return False

#     # Show the diff
#     has_changes = show_diff(file_path, fixed_content)
#     if not has_changes:
#         return False

#     # Ask the user
#     console.print()
#     if not Confirm.ask("  [bold]Apply this fix?[/bold]"):
#         console.print("  [yellow]Skipped.[/yellow]")
#         return False

#     backup = file_path.with_suffix(file_path.suffix + ".bak")
#     shutil.copy(file_path, backup)
#     file_path.write_text(fixed_content, encoding="utf-8")
#     console.print(f"  [bold green]✓ Applied.[/bold green]  [dim]Backup → {backup}[/dim]\n")
#     return True


def apply_fix(file_path: Path, fixed_content: str) -> bool:
    records = _patch_records.get()          # ← only source of truth, no parameter
    old_content = file_path.read_text(encoding="utf-8")

    changed = diff_line_count(old_content, fixed_content)
    if changed > MAX_DIFF_LINES:
        console.print(f"  [red]✗ Rejecting: {changed} lines exceeds limit.[/red]")
        records.append(PatchRecord(         # ← records, not patch_records
            file_path=str(file_path),
            patch_summary=f"Rejected — {changed} lines exceeds safety limit",
            diff=_make_diff(file_path, old_content, fixed_content),
            approved=False,
            applied=False,
        ))
        return False

    if file_path.suffix == ".py" and not validate_python_syntax(fixed_content, str(file_path)):
        console.print("  [red]✗ Rejecting: syntax errors.[/red]")
        records.append(PatchRecord(
            file_path=str(file_path),
            patch_summary="Rejected — proposed content has syntax errors",
            diff=_make_diff(file_path, old_content, fixed_content),
            approved=False,
            applied=False,
        ))
        return False

    if file_path.suffix == ".py":
        removed = extract_function_names(old_content) - extract_function_names(fixed_content)
        if removed:
            console.print(f"  [red]✗ Rejecting: would delete {', '.join(sorted(removed))}[/red]")
            records.append(PatchRecord(
                file_path=str(file_path),
                patch_summary=f"Rejected — would delete: {', '.join(sorted(removed))}",
                diff=_make_diff(file_path, old_content, fixed_content),
                approved=False,
                applied=False,
            ))
            return False

    has_changes = show_diff(file_path, fixed_content)
    if not has_changes:
        return False

    console.print()
    if not Confirm.ask("  [bold]Apply this fix?[/bold]"):
        console.print("  [yellow]Skipped.[/yellow]")
        records.append(PatchRecord(
            file_path=str(file_path),
            patch_summary="User rejected the patch",
            diff=_make_diff(file_path, old_content, fixed_content),
            approved=False,
            applied=False,
        ))
        return False

    backup = file_path.with_suffix(file_path.suffix + ".bak")
    shutil.copy(file_path, backup)
    file_path.write_text(fixed_content, encoding="utf-8")
    console.print(f"  [bold green]✓ Applied.[/bold green]  [dim]Backup → {backup}[/dim]\n")

    records.append(PatchRecord(
        file_path=str(file_path),
        patch_summary=f"Applied — {changed} lines changed in {file_path.name}",
        diff=_make_diff(file_path, old_content, fixed_content),
        approved=True,
        applied=True,
    ))
    return True

# def apply_fix(
#     file_path: Path,
#     fixed_content: str,
#     patch_records: list[PatchRecord],  # pass in from outside, append to it
# ) -> bool:
#     records = _patch_records.get()

#     old_content = file_path.read_text(encoding="utf-8")

#     # ── Guards ────────────────────────────────────────────────────────────────
#     changed = diff_line_count(old_content, fixed_content)
#     if changed > MAX_DIFF_LINES:
#         console.print(f"  [red]✗ Rejecting: {changed} lines exceeds limit.[/red]")
#         patch_records.append(PatchRecord(
#             file_path=str(file_path),
#             patch_summary=f"Rejected — {changed} changed lines exceeds safety limit",
#             diff=_make_diff(file_path, old_content, fixed_content),
#             approved=False,
#             applied=False,
#         ))
#         return False

#     if file_path.suffix == ".py" and not validate_python_syntax(fixed_content, str(file_path)):
#         console.print("  [red]✗ Rejecting: syntax errors.[/red]")
#         patch_records.append(PatchRecord(
#             file_path=str(file_path),
#             patch_summary="Rejected — proposed content has syntax errors",
#             diff=_make_diff(file_path, old_content, fixed_content),
#             approved=False,
#             applied=False,
#         ))
#         return False

#     if file_path.suffix == ".py":
#         removed = extract_function_names(old_content) - extract_function_names(fixed_content)
#         if removed:
#             console.print(f"  [red]✗ Rejecting: would delete {', '.join(sorted(removed))}[/red]")
#             patch_records.append(PatchRecord(
#                 file_path=str(file_path),
#                 patch_summary=f"Rejected — would delete functions: {', '.join(sorted(removed))}",
#                 diff=_make_diff(file_path, old_content, fixed_content),
#                 approved=False,
#                 applied=False,
#             ))
#             return False

#     # ── Show diff ─────────────────────────────────────────────────────────────
#     has_changes = show_diff(file_path, fixed_content)
#     if not has_changes:
#         return False

#     # ── User decision ─────────────────────────────────────────────────────────
#     console.print()
#     if not Confirm.ask("  [bold]Apply this fix?[/bold]"):
#         console.print("  [yellow]Skipped.[/yellow]")
#         patch_records.append(PatchRecord(
#             file_path=str(file_path),
#             patch_summary="User rejected the patch",
#             diff=_make_diff(file_path, old_content, fixed_content),
#             approved=False,
#             applied=False,
#         ))
#         return False

#     # ── Apply ─────────────────────────────────────────────────────────────────
#     backup = file_path.with_suffix(file_path.suffix + ".bak")
#     shutil.copy(file_path, backup)
#     file_path.write_text(fixed_content, encoding="utf-8")
#     console.print(f"  [bold green]✓ Applied.[/bold green]  [dim]Backup → {backup}[/dim]\n")

#     patch_records.append(PatchRecord(
#         file_path=str(file_path),
#         patch_summary=f"Applied — {changed} lines changed in {file_path.name}",
#         diff=_make_diff(file_path, old_content, fixed_content),
#         approved=True,
#         applied=True,
#     ))
#     return True


def _make_diff(file_path: Path, old: str, new: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=str(file_path),
        tofile=f"{file_path} (fixed)",
    ))