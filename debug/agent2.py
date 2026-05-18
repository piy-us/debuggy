# # from pathlib import Path
# # import os
# # import json

# # from dotenv import load_dotenv

# # # LangChain ReAct agent
# # from langchain.agents import AgentExecutor, create_react_agent
# # from langchain_core.prompts import PromptTemplate
# # from langchain_groq import ChatGroq

# # # ── Load env ──────────────────────────────────────────────────────────────────

# # load_dotenv()

# # # ── Import tools ──────────────────────────────────────────────────────────────

# # from tools.filesystem import ALL_TOOLS, list_files

# # # ── System prompt ─────────────────────────────────────────────────────────────

# # SYSTEM_PROMPT = Path("prompts/system_prompt.txt").read_text(encoding="utf-8")

# # # ReAct prompt — LangChain requires specific variables: tools, tool_names,
# # # input, agent_scratchpad.  We embed the system prompt as a static prefix.
# # REACT_TEMPLATE = (
# #     SYSTEM_PROMPT
# #     + """

# # You have access to the following tools:
# # {tools}

# # Tool names you may use: {tool_names}

# # Use this exact format for every reasoning step:

# # Thought: your reasoning about what to do
# # Action: tool_name
# # Action Input: {{"arg": "value"}}
# # Observation: <filled in by system>

# # When the bug is fully fixed, write:

# # Thought: final explanation of what was fixed
# # Final Answer: {{"thought": "...", "actions": [], "expected_result": "...", "status": "fixed"}}

# # Human request:
# # {input}

# # {agent_scratchpad}"""
# # )

# # react_prompt = PromptTemplate.from_template(REACT_TEMPLATE)

# # # ── Model ─────────────────────────────────────────────────────────────────────

# # MODEL = "llama-3.3-70b-versatile"

# # llm = ChatGroq(
# #     model=MODEL,
# #     temperature=0,
# #     api_key=os.environ.get("GROQ_API_KEY"),
# # )

# # # ── Agent ─────────────────────────────────────────────────────────────────────

# # agent = create_react_agent(
# #     llm=llm,
# #     tools=ALL_TOOLS,
# #     prompt=react_prompt,
# # )

# # agent_executor = AgentExecutor(
# #     agent=agent,
# #     tools=ALL_TOOLS,
# #     verbose=True,
# #     max_iterations=20,          # allow up to 20 tool calls per run
# #     handle_parsing_errors=True, # don't crash on malformed LLM output
# #     return_intermediate_steps=True,
# # )

# # # ── Public API ────────────────────────────────────────────────────────────────


# # def ask_agent(prompt: str) -> str:
# #     """
# #     Run the agent with the given prompt.
# #     Returns the Final Answer string (JSON) or a plain-text fallback.
# #     """
# #     try:
# #         # Inject repo map so the agent always knows what files exist
# #         repo_map = list_files.invoke(".")

# #         context = f"""Project structure:
# # {repo_map}

# # Task:
# # {prompt}
# # """

# #         response = agent_executor.invoke({"input": context})

# #         # AgentExecutor puts the final answer in response["output"]
# #         final = response.get("output", "")

# #         # Try to parse as JSON to validate structure; return raw string either way
# #         try:
# #             parsed = json.loads(final)
# #             return json.dumps(parsed)  # normalized JSON
# #         except json.JSONDecodeError:
# #             # Agent may have returned plain text — wrap it
# #             return json.dumps({
# #                 "thought": final,
# #                 "actions": [],
# #                 "status": "needs_more_work",
# #             })

# #     except Exception as e:
# #         return json.dumps({
# #             "thought": f"Agent error: {str(e)}",
# #             "actions": [],
# #             "status": "needs_more_work",
# #         })
# """
# agent2.py — Repair agent using LangGraph's create_react_agent (modern, stable).

# Install requirements:
#     pip install langgraph langchain-groq langchain-core langchain

# LangChain 1.0+ removed AgentExecutor from langchain.agents.
# The modern replacement is langgraph.prebuilt.create_react_agent,
# which is simpler AND more capable.
# """
# from pathlib import Path
# import os
# import json

# from dotenv import load_dotenv
# from langchain_groq import ChatGroq

# # LangGraph's create_react_agent — works with LangChain 0.3+ and 1.0+
# # This is the correct modern import (NOT langchain.agents)
# from langgraph.prebuilt import create_react_agent

# # ── Load env ──────────────────────────────────────────────────────────────────

# load_dotenv()

# # ── Import tools ──────────────────────────────────────────────────────────────

# from tools.filesystem import ALL_TOOLS, list_files

# # ── System prompt ─────────────────────────────────────────────────────────────

# SYSTEM_PROMPT = Path("prompts/system_prompts.txt").read_text(encoding="utf-8")

# # ── Model ─────────────────────────────────────────────────────────────────────

# MODEL = "openai/gpt-oss-120b"

# llm = ChatGroq(
#     model=MODEL,
#     temperature=0,
#     api_key=os.environ.get("GROQ_API_KEY"),
# )

# # ── Agent (LangGraph style — no prompt template needed) ───────────────────────
# #
# # create_react_agent from langgraph.prebuilt accepts:
# #   model      — any LangChain chat model
# #   tools      — list of tools
# #   prompt     — system prompt string (or a SystemMessage)
# #
# # It returns a compiled StateGraph that you call with .invoke()

# agent = create_react_agent(
#     model=llm,
#     tools=ALL_TOOLS,
#     prompt=SYSTEM_PROMPT,
# )

# # ── Public API ────────────────────────────────────────────────────────────────


# def ask_agent(prompt: str) -> str:
#     """
#     Run the repair agent with the given prompt.
#     Returns a JSON string with keys: thought, actions, status.
#     """
#     try:
#         # Inject repo map so the agent always knows what files exist
#         repo_map = list_files.invoke(".")

#         full_input = f"""Project structure:
# {repo_map}

# Task:
# {prompt}
# """

#         # LangGraph agent invocation — messages format
#         result = agent.invoke({
#             "messages": [
#                 {"role": "user", "content": full_input}
#             ]
#         })

#         # LangGraph returns {"messages": [...]} — last message is the final answer
#         messages = result.get("messages", [])
#         if not messages:
#             return json.dumps({
#                 "thought": "Agent returned no messages.",
#                 "actions": [],
#                 "status": "needs_more_work",
#             })

#         final_content = messages[-1].content

#         # Attempt to parse as JSON (agent should return structured JSON per system prompt)
#         # Strip markdown fences if present
#         import re
#         cleaned = re.sub(r'^```(?:json)?\s*', '', final_content.strip())
#         cleaned = re.sub(r'\s*```$', '', cleaned)

#         try:
#             parsed = json.loads(cleaned)
#             return json.dumps(parsed)
#         except json.JSONDecodeError:
#             # Try to extract first {...} block
#             match = re.search(r'\{.*\}', cleaned, re.DOTALL)
#             if match:
#                 try:
#                     parsed = json.loads(match.group(0))
#                     return json.dumps(parsed)
#                 except json.JSONDecodeError:
#                     pass

#             # Give up — wrap plain text response
#             return json.dumps({
#                 "thought": final_content,
#                 "actions": [],
#                 "status": "needs_more_work",
#             })

#     except Exception as e:
#         return json.dumps({
#             "thought": f"Agent error: {str(e)}",
#             "actions": [],
#             "status": "needs_more_work",
#         })
"""
agent2.py — Repair agent using LangGraph's create_react_agent + ChatGroq.

Exports:
    ask_agent(prompt)  ->  (response_dict, messages_list)
    agent              ->  compiled LangGraph graph (for direct use)
    list_files         ->  tool (for repo map)

Install:
    pip install langgraph langchain-groq langchain-core langchain python-dotenv
"""
# from pathlib import Path
# import os
# import re
# import json

# from dotenv import load_dotenv
# from langchain_groq import ChatGroq
# from langgraph.prebuilt import create_react_agent

# load_dotenv()

# # ── Import tools ──────────────────────────────────────────────────────────────

# from tools.filesystem import ALL_TOOLS, list_files
# from tools.claude_tools import dev_tools
# # ── Model ─────────────────────────────────────────────────────────────────────
# from langgraph.checkpoint.memory import MemorySaver

# memory = MemorySaver()

# MODEL = "openai/gpt-oss-120b"

# llm = ChatGroq(
#     model=MODEL,
#     temperature=0.2,
#     api_key=os.environ.get("GROQ_API_KEY"),
# )

# # ── System prompt ─────────────────────────────────────────────────────────────
# # Keep SHORT — Groq validates tool names mentioned in the prompt against
# # the registered schemas. A long prompt that says "call read_file()" causes
# # a 400 error before the model produces any output.

# SYSTEM_PROMPT = """You are an expert software repair agent.

# When given a bug report:
# 1. You have access to tools for inspecting and editing the codebase, and running shell commands.
# 2. Use the tools to investigate the error and identify the root cause.
# 3. Suggest a specific, minimal code change to fix the bug. Always prefer targeted edits using replace_in_file over write_file.
# 4. After suggesting a fix, use run_shell to run the failing command and verify the fix works.
# 5. Repeat this process until the command exits with code 0.
# 6. When the bug is fully fixed, return a JSON object with keys: thought, actions, expected_result, status.
# 7. Always explain your reasoning in the thought field before making changes.
# 8. Never delete functions, classes, or business logic. Only make minimal edits to fix the bug.
# Never hardcode outputs to fake success. Always verify with run_shell.
# 9. If you are unsure about the fix, use the tools to gather more information instead of guessing.
# 10. Always verify your fix by running the command — don't just assume it's correct.
# 11. If the command still fails after your fix, analyze the new error message and iterate on your solution.
# 12. Use run_shell to run the failing command and verify the fix works.
# 13. Repeat until the command exits with code 0.

# Rules:
# - Always read a file before editing it.
# - Prefer replace_in_file over write_file.
# - Never delete functions, classes, or business logic.
# - Never hardcode outputs to fake success.
# """

# # ── Agent ─────────────────────────────────────────────────────────────────────

# # agent = create_react_agent(
# #     model=llm,
# #     tools=dev_tools+ALL_TOOLS ,
# #     prompt=SYSTEM_PROMPT,
# # )
# agent = create_react_agent(
#     model=llm,
#     tools=dev_tools + ALL_TOOLS,
#     prompt=SYSTEM_PROMPT,
#     checkpointer=memory,
# )
# config = {
#     "configurable": {
#         "thread_id": "bugfix-session-1"
#     }
# }

# # ── Public API ────────────────────────────────────────────────────────────────

# def ask_agent(prompt: str) -> tuple[dict, list]:
#     """
#     Run the repair agent.

#     Returns:
#         (response_dict, messages)
#         - response_dict  has keys: thought, actions, status
#         - messages       is the full LangGraph message list (for trace display)
#     """
#     _fail = lambda reason: (
#         {"thought": reason, "actions": [], "status": "needs_more_work"},
#         [],
#     )

#     try:
#         repo_map = list_files.invoke(".")

#         full_input = f"""Project files:
# {repo_map}
# Tools Available: {[tool.name for tool in dev_tools+ALL_TOOLS]}
# \n
# Bug report:
# {prompt}
# """
#         result   = agent.invoke({"messages": [{"role": "user", "content": full_input}]}, config=config)
#         messages = result.get("messages", [])

#         if not messages:
#             return _fail("No response from agent.")

#         final_content = messages[-1].content

#         # Strip markdown fences
#         cleaned = re.sub(r'^```(?:json)?\s*', '', final_content.strip())
#         cleaned = re.sub(r'\s*```$', '', cleaned)

#         # Parse JSON
#         try:
#             return json.loads(cleaned), messages
#         except json.JSONDecodeError:
#             pass

#         match = re.search(r'\{.*\}', cleaned, re.DOTALL)
#         if match:
#             try:
#                 return json.loads(match.group(0)), messages
#             except json.JSONDecodeError:
#                 pass

#         # Plain-text response — still return messages for trace display
#         return {"thought": final_content, "actions": [], "status": "needs_more_work"}, messages

#     except Exception as e:
#         return _fail(f"Agent error: {str(e)}")

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

from tools.filesystem import ALL_TOOLS
from tools.claude_tools import dev_tools
import typer
app            = typer.Typer(invoke_without_command=True)

TOOLS = dev_tools + ALL_TOOLS

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
1. Inspect the codebase using tools.
2. Identify the root cause.
3. Make minimal edits.
5. Retry up to 3 times if needed.

Rules:
- Read snippets from files before editing.
- Prefer minimal edits.
- Never fake success.
- Use only provided tools.
- Do not invent tool names.
- Stop after 3 failed attempts.

Output:
Output should be in the format: {RepairResponse}
"""

# ── Agent ─────────────────────────────────────────────────────────────────────

agent = create_agent(
    model=llm,
    tools=TOOLS,
    system_prompt=SYSTEM_PROMPT,
    checkpointer=memory,
    response_format=RepairResponse,
)

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "configurable": {
        "thread_id": "bugfix-session-1"
    }
}

# ── Public API ────────────────────────────────────────────────────────────────

# def ask_agent(
#     prompt: str,
#     thread_id: str = "bugfix-session-1",
# ) -> tuple[dict, list]:
#     """
#     Run the repair agent.

#     Returns:
#         (response_dict, messages)
#     """

#     def _fail(reason: str):
#         return (
#             {
#                 "thought": reason,
#                 "actions": [],
#                 "expected_result": "",
#                 "status": "needs_more_work",
#             },
#             [],
#         )

#     try:
#         full_input = f"""
# Bug report:
# {prompt}

# Use tools to inspect the repository as needed.
# """

#         config = {
#             "configurable": {
#                 "thread_id": thread_id
#             }
#         }

#         result = agent.invoke(
#             {
#                 "messages": [
#                     {
#                         "role": "user",
#                         "content": full_input,
#                     }
#                 ]
#             },
#             config=config,
#         )

#         messages = result.get("messages", [])

#         structured = result.get("structured_response")

#         if structured:
#             return structured.model_dump(), messages
#             print("Agent response (structured):", structured.model_dump())
#         if messages:
#             final_message = messages[-1].content
#             print("Agent response (unstructured):", final_message)
#             return (
#                 {
#                     "thought": str(final_message),
#                     "actions": [],
#                     "expected_result": "",
#                     "status": "needs_more_work",
#                 },
#                 messages,
#             )

#         return _fail("No response from agent.")

#     except Exception as e:
#         return _fail(f"Agent error: {str(e)}")

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

        # result = agent.invoke(
        #     {
        #         "messages": [
        #             {
        #                 "role": "user",
        #                 "content": full_input,
        #             }
        #         ]
        #     },
        #     config=config,
        # )
        # for msg in result["messages"]:
        #     print("\n====================")
        #     print("TYPE:", type(msg).__name__)

        #     if isinstance(msg, ToolMessage):
        #         print("TOOL:", msg.name)
        #         print("OUTPUT:")
        #         print(msg.content)

        #     else:
        #         print(msg)
    #     final_state = None

    #     for chunk in agent.stream(
    #         {
    #             "messages": [
    #                 {
    #                     "role": "user",
    #                     "content": full_input,
    #                 }
    #             ]
    #         },
    #         config=config,
    #         stream_mode="values",
    #     ):
    #         final_state = chunk

    #         messages = chunk.get("messages", [])

    #         if not messages:
    #             continue

    #         msg = messages[-1]

    #         print("\n====================")
    #         print("TYPE:", type(msg).__name__)

    #         if isinstance(msg, ToolMessage):
    #             print("TOOL:", msg.name)
    #             print("OUTPUT:")
    #             print(msg.content)

    #         elif isinstance(msg, AIMessage):
    #             print("AI:")
    #             print(msg.content)

    #             if getattr(msg, "tool_calls", None):
    #                 print("\nTOOL CALLS:")
    #                 for tc in msg.tool_calls:
    #                     print(tc)

    #         elif isinstance(msg, HumanMessage):
    #             print("HUMAN:")
    #             print(msg.content)

    #         else:
    #             print(msg)
    #     if final_state is None:
    #         return {
    #             "status": "failed",
    #             "messages": "No final state returned"
    #         }

    #     response = final_state.get("structured_response")

    #     print("\n=== FINAL STRUCTURED RESPONSE ===")
    #     print(response)
    #     # response: RepairResponse = result.get("structured_response")

    #     # print("Parsed structured response:", response)

    #     # messages = response.reply

    #     # print("Agent messages:", messages)

    #     return {
    #         "thought": response.thought,
    #         "actions": response.actions,
    #         "expected_result": response.expected_result,
    #         "status": response.status,
    #         "messages": messages
    #     }
    # except Exception as e:
    #     return _fail(f"Agent error: {str(e)}")
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

            if isinstance(msg, HumanMessage):

                console.print(
                    Panel(
                        msg.content.strip(),
                        title="[bold blue]Bug Report[/bold blue]",
                        border_style="blue",
                        expand=False,
                    )
                )

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
prompt = '''Traceback (most recent call last):
  File "D:\debuggy\debug\workspace\test_script.py", line 16, in <module>
    user = get_user(users, 3)          # KeyError: 3
  File "D:\debuggy\debug\workspace\test_script.py", line 4, in get_user
    return users[user_id]
           ~~~~~^^^^^^^^^
KeyError: 3
'''

@app.command("fix")
def fix_command(
    command: str = typer.Argument(..., help="Command to run, e.g. 'python workspace/file2.py'")
):
    """Run COMMAND, detect errors, and autonomously attempt to fix them."""
    ask_agent(prompt)

@app.command()
def donothing():
    pass

if __name__ == "__main__":
    app()