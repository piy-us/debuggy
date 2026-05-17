from pathlib import Path
import re
import subprocess
import sys      
import time
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax
from rich.text import Text
from rich.rule import Rule
from rich.table import Table
from rich import box
from pprint import pformat
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

from pydantic import BaseModel, Field
from state import ParsedTraceback, StackFrame
# ── Models ────────────────────────────────────────────────────────────────────
 
# class StackFrame(BaseModel):
#     file: str
#     line: int
#     function: str
#     code: str | None = None
 
 
# class ParsedTraceback(BaseModel):
#     error_type: str
#     error_message: str
 
#     file: str | None = None
#     line: int | None = None
#     function: str | None = None
 
#     code_snippet: str | None = None
 
#     raw_stderr: str | None = None
 
#     stack_frames: list[StackFrame] = Field(default_factory=list)

# ── Stderr helpers ────────────────────────────────────────────────────────────
 
 
 
def _read_snippet(path: Path, line_no: int, context: int = 12) -> str | None:
    """Return an annotated slice of *path* centred on *line_no*, or None."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        start = max(0, line_no - context - 1)
        end   = min(len(lines), line_no + context)
        out   = []
        for i, l in enumerate(lines[start:end], start=start):
            marker = ">>>" if (i + 1) == line_no else "   "
            out.append(f"{marker} {i+1:4}: {l}")
        return "\n".join(out) if out else None
    except Exception:
        return None

 
def clean_powershell_noise(stderr: str) -> str:
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
 
 
 
 
def _line_code(path: Path, line_no: int) -> str | None:
    """Return the single source line at *line_no* (1-based), stripped."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        idx = line_no - 1
        return lines[idx].strip() if 0 <= idx < len(lines) else None
    except Exception:
        return None
 
 
# ── Traceback parser ──────────────────────────────────────────────────────────
 
def parse_traceback(stderr: str) -> ParsedTraceback:
    stderr = clean_powershell_noise(stderr)
 
    # ── Python ────────────────────────────────────────────────────────────────
    # Each frame: File "path", line N, in <function>
    py_frame_re = re.compile(
        r'File "([^"]+)", line (\d+), in (\S+)'
    )
    py_matches = py_frame_re.findall(stderr)
 
    if py_matches:
        frames: list[StackFrame] = []
        for file_path, lineno, func in py_matches:
            p = Path(file_path)
            frames.append(StackFrame(
                file     = file_path,
                line     = int(lineno),
                function = func,
                code     = _line_code(p, int(lineno)) if p.exists() else None,
            ))
 
        # Focus on the innermost user frame (skip stdlib / venv)
        user_frames = [
            f for f in frames
            if not any(s in f.file for s in ('site-packages', 'venv', '<frozen'))
        ]
        focal = user_frames[-1] if user_frames else frames[-1]
 
        last_line = stderr.strip().splitlines()[-1]
        parts      = last_line.split(":", 1)
        error_type = parts[0].strip()
        error_msg  = parts[1].strip() if len(parts) > 1 else last_line
 
        p = Path(focal.file)
        return ParsedTraceback(
            error_type    = error_type,
            error_message = error_msg,
            file          = focal.file,
            line          = focal.line,
            function      = focal.function,
            code_snippet  = _read_snippet(p, focal.line) if p.exists() else None,
            raw_stderr    = stderr,
            stack_frames  = frames,
        )
 
    # ── Node.js ───────────────────────────────────────────────────────────────
    # at FunctionName (/abs/path/file.js:line:col)
    # at /abs/path/file.js:line:col
    node_frame_re = re.compile(
        r'at (?:(.+?) )?\(?((?:[A-Za-z]:)?[^():\n]+):(\d+):\d+\)?'
    )
    node_matches = node_frame_re.findall(stderr)
 
    if node_matches:
        frames = []
        for func, file_path, lineno in node_matches:
            if any(s in file_path for s in ('node_modules', 'node:internal', '<anonymous>')):
                continue
            p = Path(file_path)
            frames.append(StackFrame(
                file     = file_path,
                line     = int(lineno),
                function = func.strip() if func.strip() else "<anonymous>",
                code     = _line_code(p, int(lineno)) if p.exists() else None,
            ))
 
        if frames:
            focal = frames[0]   # Node prints outermost (throw site) first
            first_line = stderr.strip().splitlines()[0]
            parts      = first_line.split(":", 1)
            error_type = parts[0].strip()
            error_msg  = parts[1].strip() if len(parts) > 1 else first_line
 
            p = Path(focal.file)
            return ParsedTraceback(
                error_type    = error_type,
                error_message = error_msg,
                file          = focal.file,
                line          = focal.line,
                function      = focal.function,
                code_snippet  = _read_snippet(p, focal.line) if p.exists() else None,
                raw_stderr    = stderr,
                stack_frames  = frames,
            )
 
    # ── Fallback ──────────────────────────────────────────────────────────────
    last = stderr.strip().splitlines()[-1] if stderr.strip() else "unknown error"
    return ParsedTraceback(
        error_type    = "Error",
        error_message = last,
        raw_stderr    = stderr,
    )
 


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
# # 5th
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
#         end   = min(len(lines), line_no + context)
#         out   = []
#         for i, l in enumerate(lines[start:end], start=start):
#             marker = ">>>" if (i + 1) == line_no else "   "
#             out.append(f"{marker} {i+1:4}: {l}")
#         return out
#     except Exception:
#         return []

# # ── Traceback parser ──────────────────────────────────────────────────────────
# # 4th
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
#             last_line  = stderr.strip().splitlines()[-1]
#             parts      = last_line.split(":", 1)
#             error_type = parts[0].strip()
#             error_msg  = parts[1].strip() if len(parts) > 1 else last_line
#             p = Path(file_path)
#             return ParsedError(
#                 language       = "python",
#                 error_type     = error_type,
#                 error_message  = error_msg,
#                 file_path      = p if p.exists() else None,
#                 line_number    = line_no,
#                 relevant_lines = get_lines_around(p, line_no) if p.exists() else [],
#                 raw_stderr     = stderr,
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
#             parts      = first_line.split(":", 1)
#             error_type = parts[0].strip()
#             error_msg  = parts[1].strip() if len(parts) > 1 else first_line
#             p = Path(file_path)
#             return ParsedError(
#                 language       = "javascript",
#                 error_type     = error_type,
#                 error_message  = error_msg,
#                 file_path      = p if p.exists() else None,
#                 line_number    = line_no,
#                 relevant_lines = get_lines_around(p, line_no) if p.exists() else [],
#                 raw_stderr     = stderr,
#             )

#     # Fallback
#     return ParsedError(
#         language      = "unknown",
#         error_type    = "Error",
#         error_message = stderr.strip().splitlines()[-1] if stderr.strip() else "unknown error",
#         file_path     = None,
#         line_number   = None,
#         raw_stderr    = stderr,
#     )

