import typer
from orc2 import cmd_fix

app = typer.Typer(invoke_without_command=True)

@app.command("fix")
def fix_command(
    command: str = typer.Argument(..., help="Command to run, e.g. 'python workspace/file2.py'")
):
    """Run COMMAND, detect errors, and autonomously attempt to fix them."""
    cmd_fix(command)

@app.command()
def donothing():
    pass

if __name__ == "__main__":
    app()