import ast
import difflib
import shutil
from pathlib import Path
from rich.console import Console
from rich.rule import Rule

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
