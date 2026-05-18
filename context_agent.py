"""
context_agent.py — triage + deep context gathering agent
"""
from __future__ import annotations

import json
import re
from typing import Optional
import os

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table

from state import AgentContext
from tools.filesystem import READ_ONLY_TOOLS
from tools.claude_tools import CONTEXT_TOOLS

CONTEXT_AGENT_TOOLS = READ_ONLY_TOOLS + CONTEXT_TOOLS

# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """
You are a senior engineer doing triage on a bug report.
You will be given an error, a traceback, and access to the codebase.
See the traceback and error provided, and try to provide the solution in the format specified at the end. Always follow the format strictly, as the output will be parsed by a program.
You have also been provided with tools to read files, search code, and inspect git history. Use them as needed to gather context and confirm your hypotheses. 
Dont call the same tool with the same arguments more than once — if you do, the tool will return a notice instead of real data, to encourage you to gather more context instead of getting stuck in loops.
════════════════════════════════════════════════════════
FINAL OUTPUT
════════════════════════════════════════════════════════

End with ONLY this JSON block (valid JSON, no trailing commas):

<context_output>
{
  "triage": "TRIVIAL" | "NEEDS_INVESTIGATION",

  "plan": {
    "root_cause": "one sentence",
    "fix_description": "human-readable summary of what to change",
    "steps": [
      {
        "file": "path/to/file.py",
        "line": 42,
        "find": "exact current code string",
        "replace": "exact replacement string",
        "why": "one sentence explanation"
      }
    ],
    "install_command": null
  },

  "context": {
    "crash_frame": {
      "file": "path/to/file.py",
      "line": 42,
      "code_window": "30+ lines around crash — null if TRIVIAL"
    },
    "failing_function": {
      "name": "fn_name",
      "definition": "full source — null if TRIVIAL",
      "class_context": "full class — null if not a method or TRIVIAL"
    },
    "callers": [
      {"file": "...", "line": 0, "code_window": "..."}
    ],
    "callees": [],
    "locals_at_crash": {},
    "imports_resolved": {},
    "recent_diff": "null if TRIVIAL",
    "related_files": [],
    "search_hits": []
  },

  "confidence": 0.95
}
</context_output>
"""

# ══════════════════════════════════════════════════════════════════════════════
# INFRASTRUCTURE
# ══════════════════════════════════════════════════════════════════════════════

console = Console()
memory  = MemorySaver()

llm = ChatGoogleGenerativeAI(
    model="gemini-3.1-flash-lite",
    temperature=0.1,
    api_key=os.environ.get("GEMINI_API_KEY"),
)

# ── REMOVE this broken block ──────────────────────────────────────────────────


# ── REPLACE with this ─────────────────────────────────────────────────────────

from functools import wraps
from contextvars import ContextVar
from langchain_core.tools import StructuredTool

_visited_files: ContextVar[set] = ContextVar("_visited_files", default=None)

_guarded_tools = {
    "tool_resolve_import",
    "tool_find_definition",
    "tool_get_code_window",
    "tool_get_class_context",
}

def _guard_tool(tool: StructuredTool) -> StructuredTool:
    """
    Wrap tool.func — not the StructuredTool itself — so LangChain's
    inspect.signature call never sees our wrapper object.
    """
    if getattr(tool, "name", None) not in _guarded_tools:
        return tool

    original_func = tool.func

    @wraps(original_func)          # wraps the real callable, not a StructuredTool
    def guarded(*args, **kwargs):
        visited = _visited_files.get()
        if visited is None:
            return original_func(*args, **kwargs)

        # Pull the file path from however the tool receives it
        file_arg = (
            kwargs.get("file_path")
            or kwargs.get("path")
            or kwargs.get("file")
            or (str(args[0]) if args else None)
        )

        if file_arg:
            if file_arg in visited:
                return f"[skipped: {file_arg} already read this session — avoiding import loop]"
            visited.add(file_arg)

        return original_func(*args, **kwargs)

    # model_copy (Pydantic v2) / copy (Pydantic v1) — both just swap the func
    try:
        return tool.model_copy(update={"func": guarded})   # pydantic v2
    except AttributeError:
        return tool.copy(update={"func": guarded})         # pydantic v1


CONTEXT_AGENT_TOOLS = [_guard_tool(t) for t in (READ_ONLY_TOOLS + CONTEXT_TOOLS)]
context_agent = create_agent(
    model=llm,
    tools=CONTEXT_AGENT_TOOLS,
    system_prompt=SYSTEM_PROMPT,
    checkpointer=memory,
)


# ══════════════════════════════════════════════════════════════════════════════
# OUTPUT PARSER
# ══════════════════════════════════════════════════════════════════════════════

def _parse_context_output(text: str) -> Optional[dict]:
    m = re.search(r"<context_output>\s*(\{.*?})\s*</context_output>", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# DISPLAY
# ══════════════════════════════════════════════════════════════════════════════

def _show_ai(msg: AIMessage) -> None:
    if getattr(msg, "tool_calls", None):
        table = Table(border_style="cyan", show_header=False, box=None, padding=(0, 1))
        table.add_column(style="bold cyan")
        table.add_column(style="dim white")
        for tc in msg.tool_calls:
            table.add_row(tc.get("name", "?"), str(tc.get("args", {}))[:120])
        console.print(table)

    content = re.sub(
        r"<context_output>.*?</context_output>", "",
        str(msg.content), flags=re.DOTALL
    ).strip()
    if content:
        console.print(Panel(
            content,
            title="[bold cyan]Context agent[/bold cyan]",
            border_style="cyan",
        ))


def _show_tool(msg: ToolMessage) -> None:
    name    = getattr(msg, "name", None) or "tool"
    content = str(msg.content).strip()
    console.print(f"  [bold cyan]→[/bold cyan] [white]{name}[/white]")
    if "\n" in content and len(content) < 8_000:
        lexer = "python" if any(kw in content for kw in ("def ", "import ", "class ")) else "text"
        console.print(Panel(
            Syntax(content, lexer, theme="monokai", line_numbers=False),
            border_style="yellow", padding=(0, 1),
        ))
    elif content:
        console.print(Panel(content[:1000], border_style="yellow", expand=False, padding=(0, 1)))


def _show_plan(plan: dict, triage: str) -> None:
    """Pretty-print the plan of action before handing off to the debug agent."""
    colour = "yellow" if triage == "TRIVIAL" else "cyan"
    label  = "Trivial fix" if triage == "TRIVIAL" else "Plan of action"

    lines = [f"[bold]{plan.get('root_cause', '')}[/bold]\n"]

    if plan.get("install_command"):
        lines.append(f"[dim]Run:[/dim] {plan['install_command']}")

    for i, step in enumerate(plan.get("steps") or [], 1):
        lines.append(
            f"[dim]Step {i}[/dim]  {step.get('file')}:{step.get('line')}\n"
            f"  [red]- {step.get('find', '')}[/red]\n"
            f"  [green]+ {step.get('replace', '')}[/green]\n"
            f"  [dim]{step.get('why', '')}[/dim]"
        )

    console.print(Panel(
        "\n".join(lines),
        title=f"[bold {colour}]{label}[/bold {colour}]",
        border_style=colour,
    ))


# ══════════════════════════════════════════════════════════════════════════════
# MERGER
# ══════════════════════════════════════════════════════════════════════════════

def _merge(ctx: AgentContext, gathered: dict) -> AgentContext:
    triage  = gathered.get("triage", "NEEDS_INVESTIGATION")
    plan    = gathered.get("plan") or {}
    context = gathered.get("context") or {}

    crash_frame  = context.get("crash_frame") or {}
    failing_fn   = context.get("failing_function") or {}
    callers      = context.get("callers") or []
    locals_repr  = context.get("locals_at_crash") or {}
    recent_diff  = context.get("recent_diff") or ctx.recent_git_diff or ""
    related      = context.get("related_files") or []
    callees      = context.get("callees") or []
    search_hits  = context.get("search_hits") or []

    # Build the code window block
    code_parts = []

    if crash_frame.get("code_window"):
        code_parts.append(
            f"# === Crash site: {crash_frame.get('file')}:{crash_frame.get('line')} ===\n"
            + crash_frame["code_window"]
        )
    if failing_fn.get("definition"):
        code_parts.append(
            f"\n# === Failing function: {failing_fn.get('name')} ===\n"
            + failing_fn["definition"]
        )
    if failing_fn.get("class_context"):
        code_parts.append(f"\n# === Class context ===\n" + failing_fn["class_context"])
    for i, caller in enumerate(callers[:3]):
        if caller.get("code_window"):
            code_parts.append(
                f"\n# === Caller {i+1}: {caller.get('file')}:{caller.get('line')} ===\n"
                + caller["code_window"]
            )
    if locals_repr:
        locals_block = "\n".join(f"  {k} = {v}" for k, v in locals_repr.items())
        code_parts.append(f"\n# === Locals at crash frame ===\n{locals_block}")

    # Inject the plan + triage directly into reasoning so the debug agent
    # reads it at the top of its context window
    extra_reasoning = list(ctx.recent_reasoning or [])
    extra_reasoning.append(f"Triage: {triage}")
    if plan.get("root_cause"):
        extra_reasoning.append(f"Root cause: {plan['root_cause']}")
    if plan.get("fix_description"):
        extra_reasoning.append(f"Suggested fix: {plan['fix_description']}")

    # Encode each step as a reasoning hint so debug agent can act immediately
    for step in (plan.get("steps") or []):
        extra_reasoning.append(
            f"Action: in {step.get('file')}:{step.get('line')} "
            f"replace `{step.get('find')}` with `{step.get('replace')}` — {step.get('why')}"
        )

    if plan.get("install_command"):
        extra_reasoning.append(f"Install needed: {plan['install_command']}")

    symbols = list(dict.fromkeys(
        callees + [h.get("snippet", "") for h in search_hits if h.get("snippet")]
    ))

# context_agent.py — _merge(), replace the return statement

    return ctx.model_copy(update={
        "crash_file_code_window": "\n".join(code_parts) or ctx.crash_file_code_window,
        "related_symbols":        symbols or ctx.related_symbols,
        "recent_git_diff":        recent_diff or ctx.recent_git_diff,
        "modified_files":         list(dict.fromkeys(
                                      list(ctx.modified_files or []) + related
                                  )),
        "recent_reasoning":       extra_reasoning,

        # ── NEW ──────────────────────────────────────────────
        "triage": gathered.get("triage"),
        "context_agent_confidence": gathered.get("confidence"),
        "action_plan": ActionPlan(          # build from the plan dict
            root_cause=plan.get("root_cause", ""),
            fix_description=plan.get("fix_description", ""),
            steps=[
                ActionStep(**s) for s in (plan.get("steps") or [])
                if all(k in s for k in ("file", "line", "find", "replace", "why"))
            ],
            install_command=plan.get("install_command"),
        ) if plan.get("steps") or plan.get("root_cause") else None,
    })
# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def run_context_agent(
    ctx: AgentContext,
    thread_id: str,
    max_tool_calls: int = 20,
) -> AgentContext:
    """
    Triage the error, optionally investigate, always produce a plan.
    Returns an enriched AgentContext. Never raises.
    """
    token = _visited_files.set(set())
    
    try:
        # ... all your existing streaming code unchanged ...

        error = ctx.current_error
        prompt = (
            f"## Command\n`{ctx.command}`\n\n"
            f"## Raw error\n```\n{error}\n```\n\n"
            f"## Parsed fields\n"
            f"- File     : `{getattr(error, 'file_path', 'unknown')}`\n"
            f"- Line     : `{getattr(error, 'line_number', 'unknown')}`\n"
            f"- Function : `{getattr(error, 'function_name', 'unknown')}`\n"
            f"- Error    : `{getattr(error, 'error_type', '')}: {getattr(error, 'error_message', '')}`\n"
            f"- Snippet  :\n```python\n{getattr(error, 'code_snippet', '') or 'none'}\n```\n\n"
            "Triage this error, investigate if needed, then produce the plan and JSON."
        )

        config          = {"configurable": {"thread_id": f"{thread_id}-ctx"}}
        seen: set[int]  = set()
        final_state     = None
        tool_call_count = 0

        console.print()
        console.print(Rule("[bold cyan]Context agent  ·  triage + investigation[/bold cyan]", style="cyan"))

        try:
            for chunk in context_agent.stream(
                {"messages": [{"role": "user", "content": prompt}]},
                config=config,
                stream_mode="values",
            ):
                final_state = chunk
                messages    = chunk.get("messages", [])
                if not messages:
                    continue
                msg = messages[-1]
                mid = id(msg)
                if mid in seen:
                    continue
                seen.add(mid)
                if isinstance(msg, AIMessage):
                    _show_ai(msg)
                elif isinstance(msg, ToolMessage):
                    tool_call_count += 1
                    _show_tool(msg)
                    if tool_call_count >= max_tool_calls:
                        console.print(f"  [yellow]Tool limit ({max_tool_calls}) reached.[/yellow]")
                        break

        except Exception as exc:
            console.print(Panel(str(exc), title="[bold red]Context agent error[/bold red]", border_style="red"))
            return ctx

        gathered: Optional[dict] = None
        if final_state:
            for m in reversed(final_state.get("messages", [])):
                if isinstance(m, AIMessage) and m.content:
                    gathered = _parse_context_output(str(m.content))
                    if gathered:
                        break

        if gathered is None:
            console.print(Panel("No <context_output> found — passing original ctx.", border_style="yellow"))
            return ctx

        # Show the plan before handing off
        if gathered.get("plan"):
            _show_plan(gathered["plan"], gathered.get("triage", "NEEDS_INVESTIGATION"))

        return _merge(ctx, gathered)
    finally:
        _visited_files.reset(token)   # clean up even if agent errors
