from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field
from time import time


# ============================================================
# TRACEBACK MODELS
# ============================================================
from typing import Literal

class ActionStep(BaseModel):
    """One concrete find→replace edit the debug agent should apply."""
    file: str
    line: int
    find: str                  # exact current string in the file
    replace: str               # exact replacement
    why: str                   # one sentence explanation


class ActionPlan(BaseModel):
    """
    Structured plan produced by the context agent.
    The debug agent should follow this unless it finds a reason not to.
    """
    root_cause: str
    fix_description: str
    steps: list[ActionStep] = Field(default_factory=list)
    install_command: str | None = None   # e.g. "pip install httpx"

class StackFrame(BaseModel):
    """
    One runtime stack frame from a traceback.
    """

    file: str
    line: int
    function: str
    code: str | None = None


class ParsedTraceback(BaseModel):
    """
    Structured runtime failure information extracted
    from stderr / traceback parsing.
    """

    error_type: str
    error_message: str

    file: str | None = None
    line: int | None = None
    function: str | None = None

    code_snippet: str | None = None

    raw_stderr: str | None = None

    stack_frames: list[StackFrame] = Field(default_factory=list)


from contextvars import ContextVar

_patch_records: ContextVar[list] = ContextVar("patch_records", default=[])

# ============================================================
# TOOL EXECUTION TELEMETRY
# ============================================================

class ToolCallRecord(BaseModel):
    """
    Observability record for one tool execution.
    """

    tool_name: str

    arguments: dict[str, Any] = Field(default_factory=dict)

    success: bool = True

    output_summary: str | None = None

    duration_sec: float | None = None

    timestamp: float = Field(default_factory=time)


# ============================================================
# SHELL EXECUTION TELEMETRY
# ============================================================

class ShellCommandRecord(BaseModel):
    """
    One shell command execution.
    """

    command: str

    cwd: str | None = None

    exit_code: int

    stdout_tail: str | None = None
    stderr_tail: str | None = None

    duration_sec: float

    timestamp: float = Field(default_factory=time)


# ============================================================
# PATCH / FILE MODIFICATION RECORDS
# ============================================================

class PatchRecord(BaseModel):
    """
    One accepted or rejected patch attempt.
    """

    file_path: str

    patch_summary: str

    diff: str

    approved: bool

    applied: bool

    timestamp: float = Field(default_factory=time)


# ============================================================
# VERIFICATION RUNS
# ============================================================

class VerificationRun(BaseModel):
    """
    Result of rerunning tests / program after repair.
    """

    command: str

    success: bool

    exit_code: int

    stdout_tail: str | None = None
    stderr_tail: str | None = None

    duration_sec: float

    timestamp: float = Field(default_factory=time)


# ============================================================
# ATTEMPT-LEVEL SUMMARIES
# ============================================================

class AttemptSummary(BaseModel):
    """
    Compact summary of one repair cycle.
    """

    attempt_number: int

    goal: str

    reasoning: list[str] = Field(default_factory=list)

    actions_taken: list[str] = Field(default_factory=list)

    files_modified: list[str] = Field(default_factory=list)

    outcome: str

    next_hypothesis: str | None = None

    success: bool = False

    timestamp: float = Field(default_factory=time)


# ============================================================
# SESSION STATE
# ============================================================

class RepairSession(BaseModel):
    """
    Persistent orchestrator-owned repair state.

    THIS is the source of truth.
    NOT the LLM.
    """

    # --------------------------------------------------------
    # Session metadata
    # --------------------------------------------------------

    session_id: str

    command: str

    started_at: float = Field(default_factory=time)

    success: bool = False

    completed: bool = False

    current_attempt: int = 0

    # --------------------------------------------------------
    # Runtime failures
    # --------------------------------------------------------

    current_error: ParsedTraceback | None = None

    previous_errors: list[ParsedTraceback] = Field(
        default_factory=list
    )

    repeated_failures: int = 0

    # --------------------------------------------------------
    # Tool + execution telemetry
    # --------------------------------------------------------

    tool_calls: list[ToolCallRecord] = Field(
        default_factory=list
    )

    shell_commands: list[ShellCommandRecord] = Field(
        default_factory=list
    )

    verification_runs: list[VerificationRun] = Field(
        default_factory=list
    )

    patches: list[PatchRecord] = Field(
        default_factory=list
    )

    # --------------------------------------------------------
    # High-level attempt summaries
    # --------------------------------------------------------

    attempts: list[AttemptSummary] = Field(
        default_factory=list
    )

    # --------------------------------------------------------
    # Runtime reasoning telemetry
    # --------------------------------------------------------

    reasoning_log: list[str] = Field(
        default_factory=list
    )

    # --------------------------------------------------------
    # Repository state
    # --------------------------------------------------------

    modified_files: set[str] = Field(
        default_factory=set
    )

    # --------------------------------------------------------
    # Metrics
    # --------------------------------------------------------

    total_tool_calls: int = 0

    total_shell_commands: int = 0

    total_tokens: int = 0

    total_input_tokens: int = 0

    total_output_tokens: int = 0

    # --------------------------------------------------------
    # Safety + supervision
    # --------------------------------------------------------

    stopped_reason: str | None = None

    max_attempts_reached: bool = False

    max_tool_calls_reached: bool = False

    timeout_reached: bool = False


# ============================================================
# AGENT CONTEXT
# ============================================================

class AgentContext(BaseModel):
    """
    Compact orchestrator-generated context
    passed INTO the agent.

    This should stay compressed and relevant.
    """

    # --------------------------------------------------------
    # Current execution state
    # --------------------------------------------------------

    attempt_number: int

    command: str

    # --------------------------------------------------------
    # Current runtime failure
    # --------------------------------------------------------

    current_error: ParsedTraceback

    # --------------------------------------------------------
    # Repository state
    # --------------------------------------------------------

    modified_files: list[str] = Field(default_factory=list)

    # --------------------------------------------------------
    # Recent telemetry
    # --------------------------------------------------------

    recent_tool_calls: list[ToolCallRecord] = Field(
        default_factory=list
    )

    recent_shell_commands: list[
        ShellCommandRecord
    ] = Field(default_factory=list)

    recent_patches: list[PatchRecord] = Field(
        default_factory=list
    )

    # --------------------------------------------------------
    # Historical summaries
    # --------------------------------------------------------

    previous_attempts: list[AttemptSummary] = Field(
        default_factory=list
    )

    # --------------------------------------------------------
    # Recent reasoning
    # --------------------------------------------------------

    recent_reasoning: list[str] = Field(
        default_factory=list
    )

    # --------------------------------------------------------
    # Optional repository enrichment
    # --------------------------------------------------------

    crash_file_code_window: str | None = None

    recent_git_diff: str | None = None

    related_symbols: list[str] = Field(
        default_factory=list
    )
    triage: Literal["TRIVIAL", "NEEDS_INVESTIGATION"] | None = None
    # Tells the debug agent upfront how hard this is expected to be.

    action_plan: ActionPlan | None = None
    # Concrete steps from the context agent. If present, the debug
    # agent should execute these directly rather than re-investigating.

    context_agent_confidence: float | None = None
    # If high (>0.85), debug agent can skip its own investigation
    # and go straight to applying the plan.



# ============================================================
# AGENT OUTPUT
# ============================================================

class AgentOutput(BaseModel):
    """
    Final bounded output from ONE agent session.

    The orchestrator still owns:
    - retries
    - verification
    - supervision
    - stopping conditions
    """

    completed: bool

    success_likely: bool

    summary: str

    reasoning_summary: list[str] = Field(
        default_factory=list
    )

    actions_summary: list[str] = Field(
        default_factory=list
    )

    files_touched: list[str] = Field(
        default_factory=list
    )

    suggested_next_step: str | None = None

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        default=0.5,
    )

    tokens_used: int | None = None
    plan_followed: bool | None = None



# ============================================================
# SESSION -> AGENT CONTEXT BUILDER
# ============================================================

def build_agent_context(
    session: RepairSession,
) -> AgentContext:
    """
    Compress full runtime state into a compact,
    high-signal agent context.
    """

    if session.current_error is None:
        raise ValueError(
            "Session has no current_error."
        )

    return AgentContext(

        attempt_number=session.current_attempt,

        command=session.command,

        current_error=session.current_error,

        modified_files=sorted(
            list(session.modified_files)
        ),

        recent_tool_calls=session.tool_calls[-10:],

        recent_shell_commands=session.shell_commands[-5:],

        recent_patches=session.patches[-5:],

        previous_attempts=session.attempts[-3:],

        recent_reasoning=session.reasoning_log[-8:],
    )