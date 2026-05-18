"""
agent.py — debuggy repair agent
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
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

from state import AgentContext, AgentOutput, PatchRecord  # your models file
from tools.filesystem import ALL_TOOLS
from tools.claude_tools import dev_tools
import typer
from patchers import _patch_records
load_dotenv()


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """
You are a software repair agent running on a developer's machine. Always reason over entire context so that bug can be identified in fewer steps.

Workflow
--------
1. Read the error and context carefully. Think aloud before calling any tool.
2. Inspect the relevant file(s) — always read before you edit.
3. Identify the root cause in one sentence.
4. Apply the minimal fix using replace_lines or write_file.
5. Stop — the orchestrator will re-run the command and verify.

Rules
-----
- Always read a file before editing it.
- Minimal edits only. Do not refactor unrelated code.
- If an action_plan is provided with confidence > 0.85, apply it directly
  without re-investigating. State "following context agent plan" and execute.
- If the plan looks wrong, say why in one sentence, then apply your own fix.

- Before each tool call, say what you are doing and why (one sentence).
- After each tool result, say what you learned (one sentence).
- Never fake success. If you cannot find the cause, say so honestly.
- Stop after applying one fix. Do not chain multiple edits speculatively.
- This is a windows system,
 
Final message
-------------
End your LAST message with this block (valid JSON, no trailing commas):

<agent_output>
{"completed": true, "success_likely": true,
 "summary": "one sentence description of what was fixed",
 "plan_followed": true,
 "reasoning_summary": ["identified root cause", "located the line"],
 "actions_summary": ["read src/foo.py lines 1-20", "replaced dict lookup with .get()"],
 "files_touched": ["src/foo.py"],
 "suggested_next_step": null,
 "confidence": 0.85}
</agent_output>
"""
 

# ══════════════════════════════════════════════════════════════════════════════
# INFRASTRUCTURE
# ══════════════════════════════════════════════════════════════════════════════

console = Console()
memory  = MemorySaver()

llm = ChatGoogleGenerativeAI(
    model="gemini-3.1-flash-lite",
    temperature=0.2,
    api_key=os.environ.get("GEMINI_API_KEY"),
)

agent = create_agent(
    model=llm,
    tools=ALL_TOOLS+ dev_tools,
    system_prompt=SYSTEM_PROMPT,
    checkpointer=memory,
)


# ══════════════════════════════════════════════════════════════════════════════
# CONTEXT SERIALIZER
# ══════════════════════════════════════════════════════════════════════════════

def serialize_context(ctx: AgentContext) -> str:
    """Render AgentContext into a clean labelled prompt string."""

    lines: list[str] = [
        f"## Attempt #{ctx.attempt_number}",
        f"**Command:** `{ctx.command}`",
        "",
    ]

    # ── NEW: triage + plan go FIRST so they dominate attention ──────────────
    if ctx.triage:
        lines += [f"### Triage: {ctx.triage}"]

    if ctx.action_plan:
        p = ctx.action_plan
        lines += [
            "",
            "### Action plan from context agent",
            f"**Root cause:** {p.root_cause}",
            f"**Fix:** {p.fix_description}",
        ]
        if p.install_command:
            lines += [f"**Install:** `{p.install_command}`"]
        for i, step in enumerate(p.steps, 1):
            lines += [
                f"\n**Step {i}** — `{step.file}` line {step.line}",
                "```diff",
                f"- {step.find}",
                f"+ {step.replace}",
                "```",
                f"_{step.why}_",
            ]
        conf = ctx.context_agent_confidence
        if conf is not None:
            lines += [f"\n_Context agent confidence: {conf:.0%}_"]
        lines += [
            "",
            "> If confidence is high and the plan looks correct, apply it directly.",
            "> If you spot a problem with the plan, explain why and apply your own fix.",
        ]

    # ── existing fields unchanged below ─────────────────────────────────────
    lines += [
        "",
        "### Current error",
        "```",
        str(ctx.current_error),
        "```",
    ]

    # ... rest of serialize_context exactly as before ...
    # lines: list[str] = [
    #     f"## Attempt #{ctx.attempt_number}",
    #     f"**Command:** `{ctx.command}`",
    #     "",
    #     "### Current error",
    #     "```",
    #     str(ctx.current_error),
    #     "```",
    # ]

    if ctx.crash_file_code_window:
        lines += ["", "### Crash site", "```python", ctx.crash_file_code_window, "```"]

    if ctx.modified_files:
        lines += ["", "### Files modified in previous attempts"]
        lines += [f"- {f}" for f in ctx.modified_files]

    if ctx.recent_patches:
        lines += ["", "### Recent patches (last 3)"]
        for p in ctx.recent_patches:
            status = "applied" if p.applied else ("rejected by user" if p.approved else "rejected by guard")
            lines += [f"**{p.file_path}** — {status}", "```diff", p.diff[:600], "```"]

    if ctx.previous_attempts:
        lines += ["", "### Previous attempts"]
        for a in ctx.previous_attempts:
            icon = "✓" if a.success else "✗"
            lines.append(f"- [{icon}] Attempt {a.attempt_number}: {a.outcome}")
            if a.next_hypothesis:
                lines.append(f"  → Suggested next: {a.next_hypothesis}")

    if ctx.recent_reasoning:
        lines += ["", "### Recent reasoning"]
        lines += [f"- {r}" for r in ctx.recent_reasoning]

    if ctx.related_symbols:
        lines += ["", "### Related symbols"]
        lines += [f"- {s}" for s in ctx.related_symbols]

    if ctx.recent_git_diff:
        lines += ["", "### Recent git diff", "```diff", ctx.recent_git_diff, "```"]

    lines += ["", "---", "Inspect the repository and apply a fix."]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# OUTPUT PARSER
# ══════════════════════════════════════════════════════════════════════════════

def _parse_output(text: str) -> Optional[AgentOutput]:
    m = re.search(r"<agent_output>\s*(\{.*?})\s*</agent_output>", text, re.DOTALL)
    if not m:
        return None
    try:
        return AgentOutput(**json.loads(m.group(1)))
    except Exception:
        return None


def _fallback(reason: str) -> AgentOutput:
    return AgentOutput(
        completed=False,
        success_likely=False,
        summary=reason,
        confidence=0.0,
    )


# ══════════════════════════════════════════════════════════════════════════════
# DISPLAY HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _show_ai(msg: AIMessage) -> None:
    if getattr(msg, "tool_calls", None):
        table = Table(border_style="cyan", show_header=False, box=None, padding=(0, 1))
        table.add_column(style="bold cyan")
        table.add_column(style="dim white")
        for tc in msg.tool_calls:
            table.add_row(tc.get("name", "?"), str(tc.get("args", {}))[:120])
        console.print(table)

    content = re.sub(r"<agent_output>.*?</agent_output>", "", str(msg.content), flags=re.DOTALL).strip()
    if content:
        console.print(Panel(content, title="[bold green]Agent[/bold green]", border_style="green"))


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


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def run_agent(
    ctx: AgentContext,
    thread_id: str,
    patch_records: list[PatchRecord],
    max_tool_calls: int = 15,
) -> AgentOutput:
    """
    Run one agent session.

    Streams thoughts + tool output to console.
    Appends PatchRecord entries to patch_records (caller owns the list).
    Returns a typed AgentOutput — never raises, never returns None.
    """
    _patch_records.set(patch_records)  # ← add this line before the stream loop

    prompt     = serialize_context(ctx)
    config     = {"configurable": {"thread_id": thread_id}}
    seen:  set[int] = set()
    final_state     = None
    tool_call_count = 0

    console.print()
    console.print(Rule(
        f"[bold cyan]debuggy  ·  attempt {ctx.attempt_number}[/bold cyan]",
        style="cyan",
    ))

    try:
        for chunk in agent.stream(
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

                # patch_records is populated by apply_fix() inside your tools,
                # which receives the same list via a context var or direct pass-through.
                # See tools/filesystem.py for the integration point.

                if tool_call_count >= max_tool_calls:
                    console.print(f"  [yellow]Tool call limit ({max_tool_calls}) reached.[/yellow]")
                    break

    except Exception as exc:
        console.print(Panel(str(exc), title="[bold red]Agent error[/bold red]", border_style="red"))
        return _fallback(f"Agent raised: {exc}")

    # ── Parse structured output ────────────────────────────────────────────────
    output: Optional[AgentOutput] = None
    if final_state:
        for m in reversed(final_state.get("messages", [])):
            if isinstance(m, AIMessage) and m.content:
                output = _parse_output(str(m.content))
                if output:
                    break

    if output is None:
        console.print(Panel(
            "No <agent_output> block found.",
            title="[bold red]Parse failed[/bold red]",
            border_style="red",
        ))
        output = _fallback("Agent finished without structured output.")

    console.print()
    console.print(Rule("[bold green]Result[/bold green]", style="green"))
    console.print(Panel(output.model_dump_json(indent=2), border_style="green"))

    return output