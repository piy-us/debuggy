def build_prompt(error: ParsedError, command: str) -> str:
    crash_location = (
        f"File: {error.file_path}, line {error.line_number}"
        if error.file_path else "Could not identify specific file"
    )
    relevant = "\n".join(error.relevant_lines) or "(not available)"

    full_file_section = ""
    if error.file_path and error.file_path.exists():
        full_content = error.file_path.read_text(encoding="utf-8")
        full_file_section = (
            f"\nFULL FILE CONTENTS ({error.file_path}):\n"
            f"```{error.language}\n{full_content}\n```\n"
        )

    return f"""Fix a {error.language} runtime error.

COMMAND THAT FAILED:
{command}

ERROR:
{error.error_type}: {error.error_message}

CRASH LOCATION:
{crash_location}

LINES AROUND THE CRASH:
{relevant}
{full_file_section}
FULL STDERR:
{error.raw_stderr[:3000]}

INSTRUCTIONS:
1. Analyze the error carefully.
2. DO NOT modify files directly.
3. DO NOT run shell commands.
4. ONLY return proposed file fixes.
5. Keep patches minimal and targeted.
6. Preserve ALL existing functions, classes, and business logic.
7. Return fixes using write_file actions only.
"""
