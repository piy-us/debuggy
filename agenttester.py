import json
import os
import re
from typing import List

from dotenv import load_dotenv
from pydantic import BaseModel, Field 

from langchain_groq import ChatGroq
from langchain.agents import create_agent

from langgraph.checkpoint.memory import MemorySaver
from langchain_google_genai import ChatGoogleGenerativeAI
# ── Load env ──────────────────────────────────────────────────────────────────
from langchain_core.rate_limiters  import InMemoryRateLimiter
load_dotenv()
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.pretty import Pretty
from rich.live import Live
from rich.text import Text
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.pretty import Pretty
from rich.live import Live
from rich.table import Table
from rich.text import Text
# ── Tools ─────────────────────────────────────────────────────────────────────
from rich.console import Console

from filesystem import ALL_TOOLS
#from tools.claude_tools import dev_tools
import typer

TOOLS =  ALL_TOOLS

# ── Memory ────────────────────────────────────────────────────────────────────
console        = Console()
memory = MemorySaver()

# ── Model ─────────────────────────────────────────────────────────────────────

# MODEL = "qwen/qwen3-32b"

# llm = ChatGroq(
#     model=MODEL,
#     temperature=0.2,
#     api_key=os.environ.get("GROQ_API_KEY"),
# )
from langchain_openai import ChatOpenAI
# llm = ChatOpenAI(
#     model="openai/gpt-oss-120b:free",
#     api_key=os.environ["OPENROUTER_API_KEY"],
#     base_url="https://openrouter.ai/api/v1",
#     temperature=0.4,
#     default_headers={
#         "HTTP-Referer": "http://localhost:3000",
#         "X-Title": "debuggy-agent",
#     },
# )
# rate_limiter = InMemoryRateLimiter(
#     requests_per_second=0.08,  # ~5 per minute
#     check_every_n_seconds=1,
#     max_bucket_size=1,
# )

llm = ChatGoogleGenerativeAI(
    model="gemini-3.1-flash-lite",
    temperature=0.2,
    api_key=os.environ.get("GEMINI_API_KEY"),
)
# ── Structured output ─────────────────────────────────────────────────────────
from langchain_core.messages import ToolMessage
from langchain_core.messages import (
    ToolMessage,
    AIMessage,
    HumanMessage,
)
from langchain_core.rate_limiters  import InMemoryRateLimiter
class RepairResponse(BaseModel):
    reply: str = Field(description="The agent's final reply, including its thought process and reasoning.")
    thought: str = Field(description="The agent's current thought or reasoning about the bug and the fix.") 
    actions: List[str] = Field(description="A list of tool calls the agent made, in chronological order.")
    expected_result: str = Field(description="The expected result of the repair.")
    status: str = Field(description="The status of the repair attempt.")

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are a software repair agent.

Workflow:
*Always start with greeting the user before any toolcalls, and end with a goodbye after the final response.*
* Based on the provided error and context, first plan and reason about the root cause of the failure.
* Then, use the provided tools to inspect the codebase and identify the minimal fix.
1. Inspect the codebase using tools.
2. Identify the root cause.
5. Retry up to 3 times if needed.

Rules:
- Read snippets from files before editing.
- Prefer minimal edits.
- Never fake success.
- Use only provided tools.
- Do not invent tool names.
- Stop after 3 failed attempts.

OS: Windows
Shell: PowerShell
Avoid bash utilities like cat/grep/ls.
Use Get-Content, dir, Select-String.

Output:
Output should be in the format: {RepairResponse}
"""

# ── Agent ─────────────────────────────────────────────────────────────────────

agent = create_agent(
    model=llm,
    system_prompt=SYSTEM_PROMPT,
    checkpointer=memory,
)

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "configurable": {
        "thread_id": "bugfix-session-1"
    }
}

# ── Public API ────────────────────────────────────────────────────────────────


def ask_agent(
    prompt: str,
    thread_id: str = "bugfix-session-2",
) -> dict:

    def _fail(reason: str):
        return (
            {
                "thought": reason,
                "actions": [],
                "expected_result": "",
                "status": "failed",
            },
            [],
        )

    try:

        full_input = f"""
Bug report:
{prompt}

Use tools to inspect the repository as needed.
"""

        config = {
            "configurable": {
                "thread_id": thread_id
            },
            
        }

        seen_messages = set()

        final_state = None

        console.print()
        console.print(
            Rule(
                "[bold cyan]debuggy agent[/bold cyan]",
                style="cyan",
            )
        )

        #with Live(refresh_per_second=12, console=console) as live:

        for chunk in agent.stream(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": full_input,
                    }
                ]
            },
            config=config,
            stream_mode="values",
        ):

            final_state = chunk

            messages = chunk.get("messages", [])
            print("messages:", messages)

            if not messages:
                continue

            msg = messages[-1]

            # prevent duplicate rendering
            msg_id = id(msg)

            if msg_id in seen_messages:
                continue

            seen_messages.add(msg_id)

            # ─────────────────────────────────────
            # HUMAN
            # ─────────────────────────────────────

            if isinstance(msg, AIMessage):

                print("AI Message:", msg)
            

            # ─────────────────────────────────────
            # TOOL OUTPUT
            # ─────────────────────────────────────

            elif isinstance(msg, ToolMessage):

                tool_name = msg.name or "tool"

                console.print(
                    f"[bold cyan]🔧 Running[/bold cyan] [white]{tool_name}[/white]"
                )

                content = str(msg.content).strip()

                # code/file-like output
                if "\n" in content and len(content) < 12000:

                    syntax = Syntax(
                        content,
                        "python",
                        theme="monokai",
                        line_numbers=True,
                    )

                    console.print(
                        Panel(
                            syntax,
                            title=f"[bold yellow]{tool_name}[/bold yellow]",
                            border_style="yellow",
                        )
                    )

                else:

                    console.print(
                        Panel(
                            content,
                            title=f"[bold yellow]{tool_name}[/bold yellow]",
                            border_style="yellow",
                            expand=False,
                        )
                    )

            # ─────────────────────────────────────
            # AI MESSAGE
            # ─────────────────────────────────────

            elif isinstance(msg, AIMessage):

                # tool planning
                if getattr(msg, "tool_calls", None):

                    table = Table(
                        title="Planned Tool Calls",
                        border_style="cyan",
                    )

                    table.add_column(
                        "Tool",
                        style="bold cyan",
                    )

                    table.add_column(
                        "Arguments",
                        style="white",
                    )

                    for tc in msg.tool_calls:

                        table.add_row(
                            tc.get("name", "unknown"),
                            str(tc.get("args", {})),
                        )

                    console.print(table)

                # reasoning text
                if msg.content:

                    console.print(
                        Panel(
                            str(msg.content).strip(),
                            title="[bold green]Agent Thought[/bold green]",
                            border_style="green",
                        )
                    )

    # ─────────────────────────────────────────
    # FINAL RESPONSE
    # ─────────────────────────────────────────

        if final_state is None:

            console.print(
                Panel(
                    "No final state returned.",
                    title="[bold red]Agent Failed[/bold red]",
                    border_style="red",
                )
            )

        else:

            final_messages = final_state.get("messages", [])

            last_ai = None

            for m in reversed(final_messages):

                if isinstance(m, AIMessage):

                    last_ai = m
                    break

            if last_ai and last_ai.content:

                console.print()

                console.print(
                    Rule(
                        "[bold green]Final Result[/bold green]",
                        style="green",
                    )
                )

                console.print(
                    Panel(
                        str(last_ai.content),
                        border_style="green",
                    )
                )
    except Exception as e:

        console.print(
            Panel(
                f"Agent error: {str(e)}",
                title="[bold red]Agent Error[/bold red]",
                border_style="red",
            )  
    )     

if __name__ == "__main__":
    ask_agent(prompt="Example bug: TypeError in utils.py line 45 when running 'python main.py'")