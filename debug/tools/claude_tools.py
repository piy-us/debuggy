"""
Context Agent Tools
-------------------
All tools the context agent needs to build a full debugging context.
Wrap each function with @tool when integrating into LangChain.
"""

import ast
import os
import subprocess
import sys
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Optional
import re
from tools.filesystem import apply_fix, validate_python_syntax,extract_function_names,show_diff,diff_line_count
# ---------------------------------------------------------------------------
# 1. CRASH FRAME CAPTURE
# ---------------------------------------------------------------------------

def run_with_capture(fn):
    """
    Run a callable and capture full exception context.
    Returns error, traceback, and per-frame locals with types.
    """
    try:
        fn()
    except Exception as e:
        exc_type, exc_value, exc_tb = sys.exc_info()

        frames = []
        tb = exc_tb

        while tb:
            frame = tb.tb_frame
            raw_locals = dict(frame.f_locals)

            # Attach type info to every local variable
            typed_locals = {
                k: {
                    "value": repr(v),
                    "type": type(v).__name__
                }
                for k, v in raw_locals.items()
            }

            frames.append({
                "file": frame.f_code.co_filename,
                "function": frame.f_code.co_name,
                "line": tb.tb_lineno,
                "locals": typed_locals
            })

            tb = tb.tb_next

        return {
            "error": str(e),
            "error_type": exc_type.__name__,
            "traceback": traceback.format_exc(),
            "frames": frames
        }


# ---------------------------------------------------------------------------
# 2. CODE WINDOW
# ---------------------------------------------------------------------------

def get_code_window(path: str, line: int, radius: int = 10) -> str:
    """
    Return lines around a crash site with line numbers.
    radius=10 gives more context than the original 5.
    """
    path = safe_path(path)
    lines = Path(path).read_text().splitlines()
    start = max(0, line - radius - 1)
    end = min(len(lines), line + radius)

    output = []
    for i in range(start, end):
        marker = ">>>" if (i + 1) == line else "   "
        output.append(f"{marker} {i+1:4d} | {lines[i]}")

    return "\n".join(output)


# ---------------------------------------------------------------------------
# 3. FULL FILE READ
# ---------------------------------------------------------------------------

def read_file(path: str) -> str:
    """
    Read the entire content of a file.
    Used when code window is not enough.
    """
    return Path(path).read_text()


# ---------------------------------------------------------------------------
# 4. SYMBOL INDEX + FIND DEFINITION
# ---------------------------------------------------------------------------

def build_symbol_index(root_dir: str) -> dict:
    """
    Walk all .py files under root_dir and build a symbol index.
    Returns: { symbol_name: { file, line, type } }
    """
    index = {}
    root = Path(root_dir)
    #print(root)
    for py_file in root.rglob("*.py"):
        #print(py_file)
        try:
            code = py_file.read_text()
            tree = ast.parse(code)
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                index[node.name] = {
                    "file": str(py_file),
                    "line": node.lineno,
                    "type": "function"
                }
            elif isinstance(node, ast.ClassDef):
                index[node.name] = {
                    "file": str(py_file),
                    "line": node.lineno,
                    "type": "class"
                }

    return index

def find_definition(symbol: str, symbol_index: dict) -> dict:
    """
    Look up where a symbol (function/class) is defined.
    Also returns the actual source code of the definition.
    """
    info = symbol_index.get(symbol)
    if not info:
        return {"error": f"Symbol '{symbol}' not found in index"}

    code = Path(info["file"]).read_text()
    tree = ast.parse(code)

    for node in ast.walk(tree):
        is_match = (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == symbol
        )
        if is_match:
            source = ast.get_source_segment(code, node)
            return {**info, "source": source}

    return info
#print(find_definition("read_file", index))

# ---------------------------------------------------------------------------
# 5. CALLERS (upward traversal)
# ---------------------------------------------------------------------------

def build_call_graph(root_dir: str) -> dict:
    """
    Build a call graph for all .py files under root_dir.
    Returns: { caller_function: [callee_function, ...] }
    """
    call_graph = defaultdict(list)
    root = Path(root_dir)

    for py_file in root.rglob("*.py"):
        try:
            code = py_file.read_text()
            tree = ast.parse(code)
        except Exception:
            continue

        extractor = _CallExtractor()
        extractor.visit(tree)

        for call in extractor.calls:
            caller = call["caller"] or f"<module:{py_file}>"
            call_graph[caller].append(call["callee"])

    return dict(call_graph)


class _CallExtractor(ast.NodeVisitor):
    def __init__(self):
        self.current_function = None
        self.calls = []

    def visit_FunctionDef(self, node):
        previous = self.current_function
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        callee = None
        if isinstance(node.func, ast.Name):
            callee = node.func.id
        elif isinstance(node.func, ast.Attribute):
            callee = node.func.attr  # e.g. obj.method -> "method"

        if callee:
            self.calls.append({
                "caller": self.current_function,
                "callee": callee
            })

        self.generic_visit(node)
call_graph= build_call_graph("debug/") 


def get_callees(function: str, call_graph: dict) -> list:
    """What functions does this function call (downward)."""
    return call_graph.get(function, [])

def get_callers(function: str, call_graph: dict) -> list:
    """What functions call this function (upward traversal)."""
    return [
        caller
        for caller, callees in call_graph.items()
        if function in callees
    ]

#print(get_callers("build_call_graph", call_graph))   
# ---------------------------------------------------------------------------
# 6. IMPORT RESOLUTION
# ---------------------------------------------------------------------------

def resolve_import(module: str, symbol: str, root_dir: str) -> dict:
    """
    Resolve 'from module import symbol' to the actual file and definition.
    e.g. resolve_import("db.profile", "get_profile", "project/")
    """
    relative_path = module.replace(".", "/") + ".py"
    full_path = Path(root_dir) / relative_path

    if not full_path.exists():
        return {"error": f"Cannot resolve module '{module}' to file '{full_path}'"}

    code = full_path.read_text()
    tree = ast.parse(code)

    for node in ast.walk(tree):
        is_match = (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == symbol
        )
        if is_match:
            return {
                "module": module,
                "symbol": symbol,
                "file": str(full_path),
                "line": node.lineno,
                "source": ast.get_source_segment(code, node)
            }

    return {
        "module": module,
        "symbol": symbol,
        "file": str(full_path),
        "source": None,
        "note": "Symbol not found as top-level def; may be dynamically defined"
    }

#print(resolve_import("tools.filesystem", "list_files", "debug/"))  
# ---------------------------------------------------------------------------
# 7. CLASS CONTEXT
# ---------------------------------------------------------------------------

def get_class_context(path: str, class_name: str) -> dict:
    """
    Return full class definition: all methods, class variables, __init__ args.
    Used when crash is inside a method and shared state may be the bug.
    """
    code = Path(path).read_text()
    tree = ast.parse(code)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            methods = []
            class_vars = []

            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = [a.arg for a in item.args.args]
                    methods.append({
                        "name": item.name,
                        "line": item.lineno,
                        "args": args,
                        "source": ast.get_source_segment(code, item)
                    })
                elif isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            class_vars.append({
                                "name": target.id,
                                "line": item.lineno
                            })

            return {
                "class": class_name,
                "file": path,
                "line": node.lineno,
                "class_variables": class_vars,
                "methods": methods,
                "full_source": ast.get_source_segment(code, node)
            }

    return {"error": f"Class '{class_name}' not found in {path}"}

#print(get_class_context("debug/tools/claude_tools.py", "_CallExtractor"))
# ---------------------------------------------------------------------------
# 8. LOCALS WITH TYPES AT FRAME
# ---------------------------------------------------------------------------

def get_locals_at_frame(error_data: dict, frame_index: int) -> dict:
    """
    Return locals with values and types at a specific frame index.
    Flags suspicious values (None, empty, negative) for the fixer agent.

    error_data is the output of run_with_capture().
    """
    frames = error_data.get("frames", [])
    if frame_index >= len(frames):
        return {"error": f"Frame index {frame_index} out of range"}

    frame = frames[frame_index]
    locals_data = frame.get("locals", {})

    # Already typed if using updated run_with_capture
    # But handle both old (raw) and new (typed) formats
    result = {}
    suspicious = []

    for name, info in locals_data.items():
        if isinstance(info, dict) and "type" in info:
            value = info["value"]
            typ = info["type"]
        else:
            value = repr(info)
            typ = type(info).__name__

        is_suspicious = (
            value in ("None", "[]", "{}", "''", '""', "0", "-1")
            or typ == "NoneType"
        )

        result[name] = {"value": value, "type": typ, "suspicious": is_suspicious}

        if is_suspicious:
            suspicious.append(name)

    return {
        "file": frame["file"],
        "function": frame["function"],
        "line": frame["line"],
        "locals": result,
        "suspicious_locals": suspicious
    }
# ---------------------------------------------------------------------------
# 9. FIND RELATED FILES
# ---------------------------------------------------------------------------

def find_related_files(keyword: str, root_dir: str) -> list:
    """
    Find all .py files under root_dir whose name contains the keyword.
    Helps the agent discover files it doesn't know exist.
    e.g. keyword="user" finds user.py, user_utils.py, test_user.py
    """
    root = Path(root_dir)
    keyword_lower = keyword.lower()

    matches = []
    for py_file in root.rglob("*.py"):
        if keyword_lower in py_file.name.lower():
            matches.append(str(py_file))

    return sorted(matches)

# ---------------------------------------------------------------------------
# 10. GIT RECENT DIFF
# ---------------------------------------------------------------------------

def get_recent_diff(path: str, n_commits: int = 1) -> str:
    """
    Return the git diff for a file across the last n_commits.
    Most bugs are caused by a recent change — this often ends the investigation.
    """
    try:
        # Staged + unstaged changes first
        unstaged = subprocess.run(
            ["git", "diff", path],
            capture_output=True, text=True
        )
        staged = subprocess.run(
            ["git", "diff", "--cached", path],
            capture_output=True, text=True
        )

        # Last n commits touching this file
        committed = subprocess.run(
            ["git", "log", f"-{n_commits}", "-p", "--", path],
            capture_output=True, text=True
        )

        output = []
        if unstaged.stdout.strip():
            output.append("=== Unstaged Changes ===\n" + unstaged.stdout)
        if staged.stdout.strip():
            output.append("=== Staged Changes ===\n" + staged.stdout)
        if committed.stdout.strip():
            output.append(f"=== Last {n_commits} Commit(s) ===\n" + committed.stdout)

        return "\n".join(output) if output else "No recent changes found for this file."

    except FileNotFoundError:
        return "git not available in this environment"
    except Exception as e:
        return f"Error fetching diff: {e}"

# ---------------------------------------------------------------------------
# 11. CONTEXT ASSEMBLER
# ---------------------------------------------------------------------------

def build_context(error_data: dict, root_dir: str) -> dict:
    """
    Main entry point for the context agent.
    Ties all tools together into one structured context dict
    ready to be handed to the fixer agent.
    """
    symbol_index = build_symbol_index(root_dir)
    call_graph = build_call_graph(root_dir)

    crash_frames = []
    all_suspicious_locals = []
    files_involved = set()
    relevant_definitions = {}
    call_chain = []

    for i, frame in enumerate(error_data.get("frames", [])):
        path = frame["file"]
        line = frame["line"]
        function = frame["function"]

        files_involved.add(path)
        call_chain.append(function)

        # Code window around crash
        code_window = get_code_window(path, line)

        # Locals with type info and suspicion flags
        frame_locals = get_locals_at_frame(error_data, i)
        all_suspicious_locals.extend(frame_locals.get("suspicious_locals", []))

        crash_frames.append({
            "file": path,
            "function": function,
            "line": line,
            "locals": frame_locals["locals"],
            "suspicious_locals": frame_locals["suspicious_locals"],
            "code_window": code_window
        })

        # Find definitions for all callees of the crashed function
        for callee in get_callees(function, call_graph):
            if callee not in relevant_definitions:
                defn = find_definition(callee, symbol_index)
                if "error" not in defn:
                    relevant_definitions[callee] = defn
                    if "file" in defn:
                        files_involved.add(defn["file"])

    # Check for recent diffs on all involved files
    recent_diffs = {}
    changed_files = []
    for f in files_involved:
        diff = get_recent_diff(f)
        if "No recent changes" not in diff and "not available" not in diff:
            recent_diffs[f] = diff
            changed_files.append(f)

    return {
        "error": error_data.get("error"),
        "error_type": error_data.get("error_type"),
        "traceback": error_data.get("traceback"),
        "crash_frames": crash_frames,
        "relevant_definitions": relevant_definitions,
        "call_chain": call_chain,
        "files_involved": list(files_involved),
        "recent_diffs": recent_diffs,
        "hints": {
            "suspicious_locals": list(set(all_suspicious_locals)),
            "changed_files": changed_files,
            "likely_bug_site": error_data["frames"][-1]["file"] if error_data.get("frames") else None,
            "call_chain": call_chain
        }
    }


# ---------------------------------------------------------------------------
# LANGCHAIN TOOL WRAPPERS
# ---------------------------------------------------------------------------
# Uncomment and use when integrating into a LangChain agent.
# The tools below are thin wrappers that close over shared state
# (symbol_index, call_graph, root_dir) built at agent startup.

from langchain.tools import tool
ROOT_DIR = Path("./workspace").resolve()

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
    full = (ROOT_DIR / path).resolve()
    print(f"Resolving path: {path} -> {full}")
    if not str(full).startswith(str(ROOT_DIR)):
        raise ValueError(f"Path '{path}' escapes workspace — rejected.")
    return full


#ROOT_DIR = "workspace/"
# _symbol_index = build_symbol_index(safe_path(ROOT_DIR))
# _call_graph   = build_call_graph(safe_path(ROOT_DIR))

# @tool
# def tool_get_code_window(path: str, line: int) -> str:
#     """Get lines of code around a crash site."""
#     return get_code_window(path, line)

# @tool
# def tool_read_file(path: str) -> str:
#     """Read the full content of a source file."""
#     return read_file(path)

# @tool
# def tool_find_definition(symbol: str) -> dict:
#     """Find where a function or class is defined."""
#     return find_definition(symbol, _symbol_index)

# @tool
# def tool_get_callees(function: str) -> list:
#     """List functions that this function calls."""
#     return get_callees(function, _call_graph)

# @tool
# def tool_get_callers(function: str) -> list:
#     """List functions that call this function."""
#     return get_callers(function, _call_graph)

# @tool
# def tool_resolve_import(module: str, symbol: str) -> dict:
#     """Resolve a from-import to the actual file and source code."""
#     return resolve_import(module, symbol, ROOT_DIR)

# @tool
# def tool_get_class_context(path: str, class_name: str) -> dict:
#     """Get full class definition including all methods and class variables."""
#     return get_class_context(path, class_name)

# @tool
# def tool_get_locals_at_frame(error_data: dict, frame_index: int) -> dict:
#     """Get typed locals and suspicious values at a specific stack frame."""
#     return get_locals_at_frame(error_data, frame_index)

# @tool
# def tool_find_related_files(keyword: str) -> list:
#     """Find source files whose name contains a keyword."""
#     return find_related_files(keyword, ROOT_DIR)

# @tool
# def tool_get_recent_diff(path: str) -> str:
#     """Get recent git changes for a file. Often reveals the bug immediately."""
#     return get_recent_diff(path)
from langchain.tools import tool

ROOT_DIR = Path("./workspace").resolve()

_symbol_index = build_symbol_index(ROOT_DIR)
_call_graph = build_call_graph(ROOT_DIR)

@tool
def search_codebase(
    query: str,
    file_pattern: str = "*.py",
    max_results: int = 20,
) -> str:
    """
    Search the repository for text matches.

    Useful for:
    - finding function usages
    - locating symbols
    - tracing bugs
    - searching error messages

    Inputs:
    - query: text or regex to search
    - file_pattern: glob pattern (default: *.py)
    - max_results: max number of matches

    Returns:
    - matching file paths and line numbers
    """

    results = []

    for path in ROOT_DIR.rglob(file_pattern):

        if any(part in IGNORE_DIRS for part in path.parts):
            continue

        try:
            lines = path.read_text(
                encoding="utf-8",
                errors="ignore",
            ).splitlines()

        except Exception:
            continue

        for i, line in enumerate(lines, start=1):

            if query.lower() in line.lower():

                results.append(
                    f"{path.relative_to(ROOT_DIR)}:{i} | {line.strip()}"
                )

                if len(results) >= max_results:
                    return "\n".join(results)

    if not results:
        return "No matches found."

    return "\n".join(results)

@tool
def insert_at_line(
    path: str,
    line: int,
    text: str,
) -> str:
    """
    Insert text at a specific line number.

    Useful for:
    - adding imports
    - inserting logging
    - adding helper functions
    - patching code without exact-match replacement
    """

    file_path = safe_path(path)

    content = file_path.read_text(
        encoding="utf-8",
        errors="ignore",
    ).splitlines()

    if line < 1:
        return "Invalid line number."

    insert_index = min(line - 1, len(content))

    new_lines = text.splitlines()

    updated_lines = (
        content[:insert_index]
        + new_lines
        + content[insert_index:]
    )
    updated = "\n".join(updated_lines)
    approved = apply_fix(file_path, updated)

    if approved:
        file_path.write_text(
        "\n".join(updated),
        encoding="utf-8",)

        return f"Inserted text at line {line} in {path}"

    return f"Patch rejected for {path}"

    

    

@tool
def replace_lines(
    path: str,
    start_line: int,
    end_line: int,
    new_text: str,
) -> str:
    """
    Replace a range of lines in a file.

    More reliable than exact string replacement.

    Useful for:
    - patching buggy code
    - replacing functions
    - editing blocks safely
    """

    file_path = safe_path(path)

    lines = file_path.read_text(
        encoding="utf-8",
        errors="ignore",
    ).splitlines()

    total = len(lines)

    if start_line < 1 or end_line > total:
        return "Line range out of bounds."

    if start_line > end_line:
        return "Invalid line range."

    replacement = new_text.splitlines()

    updated_lines = (
        lines[: start_line - 1]
        + replacement
        + lines[end_line:]
    )
    updated = "\n".join(updated_lines)
    approved = apply_fix(
        file_path,
        updated,
    )

    if approved:
        return (
            f"Successfully replaced lines "
            f"{start_line}-{end_line} "
            f"in {path}"
        )

    return f"Patch rejected for {path}"

@tool
def tool_get_code_window(path: str, line: int) -> str:
    """
    Read a focused window of source code around a specific line number.

    Use this tool when:
    - investigating a stack trace
    - examining a crash location
    - understanding nearby logic without loading the full file

    Inputs:
    - path: relative path to the source file
    - line: line number near the suspected bug

    Returns:
    - source code snippet centered around the requested line
    """
    path = safe_path(path)
    return get_code_window(path, line)


@tool
def tool_read_file(path: str) -> str:
    """
    Read the complete contents of a source file.

    Use this tool before making edits or when broader file context is required.

    Important:
    - Always read a file before modifying it.
    - This tool does NOT modify files.

    Inputs:
    - path: relative path to the file

    Returns:
    - full file contents as text
    """
    path = safe_path(path)
    return read_file(path)


@tool
def tool_find_definition(symbol: str) -> dict:
    """
    Locate where a function, class, or symbol is defined in the codebase.

    Useful for:
    - tracing bugs to implementation code
    - navigating unfamiliar projects
    - finding the source of imported functions/classes

    Inputs:
    - symbol: function name, class name, or identifier

    Returns:
    - file path
    - line number
    - definition metadata
    """
    return find_definition(symbol, _symbol_index)


@tool
def tool_get_callees(function: str) -> list:
    """
    List functions directly called by a given function.

    Use this tool to:
    - understand execution flow
    - trace downstream side effects
    - investigate where invalid values propagate

    Inputs:
    - function: fully qualified or unique function name

    Returns:
    - list of called functions
    """
    return get_callees(function, _call_graph)


@tool
def tool_get_callers(function: str) -> list:
    """
    Find which functions invoke a given function.

    Useful for:
    - tracing where bad inputs originate
    - understanding entry points
    - identifying affected execution paths

    Inputs:
    - function: fully qualified or unique function name

    Returns:
    - list of caller functions
    """
    return get_callers(function, _call_graph)


@tool
def tool_resolve_import(module: str, symbol: str) -> dict:
    """
    Resolve a Python import to its actual implementation source.

    Use this tool when:
    - debugging imported symbols
    - tracing indirect dependencies
    - locating dynamically imported code

    Inputs:
    - module: imported module name
    - symbol: imported symbol name

    Returns:
    - resolved file path
    - source code
    - symbol metadata
    """
    return resolve_import(module, symbol, ROOT_DIR)


@tool
def tool_get_class_context(path: str, class_name: str) -> dict:
    """
    Retrieve the full context for a class definition.

    Includes:
    - class declaration
    - methods
    - class variables
    - inheritance context

    Use this tool when:
    - debugging object state
    - analyzing class behavior
    - fixing attribute or initialization bugs

    Inputs:
    - path: source file path
    - class_name: target class name

    Returns:
    - structured class definition data
    """
    path = safe_path(path)
    return get_class_context(path, class_name)


@tool
def tool_get_locals_at_frame(error_data: dict, frame_index: int) -> dict:
    """
    Inspect local variables and runtime values for a stack frame.

    Useful for:
    - diagnosing runtime crashes
    - identifying invalid variable states
    - detecting None values, type mismatches, or corrupted data

    Inputs:
    - error_data: parsed traceback or runtime error structure
    - frame_index: stack frame index

    Returns:
    - local variables
    - inferred types
    - suspicious values
    """
    return get_locals_at_frame(error_data, frame_index)


@tool
def tool_find_related_files(keyword: str) -> list:
    """
    Search the repository for files related to a keyword.

    Use this tool to:
    - locate relevant modules
    - find configuration or utility files
    - explore unfamiliar repositories

    Inputs:
    - keyword: partial filename or search term

    Returns:
    - matching file paths
    """
    return find_related_files(keyword, ROOT_DIR)


@tool
def tool_get_recent_diff(path: str) -> str:
    """
    Retrieve recent git changes for a file.

    Very useful for debugging regressions because recent edits often introduced the bug.

    Use this tool when:
    - a bug appeared recently
    - investigating broken refactors
    - tracking suspicious modifications

    Inputs:
    - path: source file path

    Returns:
    - recent git diff or commit-related changes
    """
    path = safe_path(path)
    return get_recent_diff(path)


dev_tools = [
    tool_get_code_window,
    insert_at_line,
    replace_lines,  
    search_codebase,
]
