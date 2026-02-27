"""
Clawd 🦞 — Main Entry Point
Interactive offensive security AI assistant powered by local LLM.

Usage:
    python clawd.py
"""

import sys
from rich.console import Console
from rich.markdown import Markdown
from rich.live import Live
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style

import config
from engine import ClawdEngine
from memory import Memory
from utils import print_banner, print_status, print_help

# ──────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────

console = Console()
engine = ClawdEngine()
memory = Memory()

# Prompt styling
prompt_style = Style.from_dict({
    "prompt": "bold ansired",
    "indicator": "bold ansiyellow",
})

PROMPT_TEXT = HTML("<prompt>clawd</prompt> <indicator>🦞 ❯ </indicator>")

# ──────────────────────────────────────────────
# Slash Command Handlers
# ──────────────────────────────────────────────

def handle_save(args: str):
    """Save current conversation to memory."""
    if not args:
        console.print("[yellow]Usage: /save <name>  (e.g., /save htb-lame)[/yellow]")
        return

    # Format conversation for saving
    conversation = []
    for msg in engine.history:
        role = "🧑 Mike" if msg["role"] == "user" else "🦞 Clawd"
        conversation.append(f"**{role}:**\n{msg['content']}\n")

    if not conversation:
        console.print("[yellow]Nothing to save — conversation is empty.[/yellow]")
        return

    content = "\n---\n\n".join(conversation)
    filepath = memory.save(args.strip(), content)
    console.print(f"[green]✓ Saved to:[/green] [dim]{filepath}[/dim]")


def handle_append(args: str):
    """Append current conversation to an existing note."""
    if not args:
        console.print("[yellow]Usage: /append <name>[/yellow]")
        return

    conversation = []
    for msg in engine.history:
        role = "🧑 Mike" if msg["role"] == "user" else "🦞 Clawd"
        conversation.append(f"**{role}:**\n{msg['content']}\n")

    if not conversation:
        console.print("[yellow]Nothing to append — conversation is empty.[/yellow]")
        return

    content = "\n---\n\n".join(conversation)
    filepath = memory.save(args.strip(), content, append=True)
    console.print(f"[green]✓ Appended to:[/green] [dim]{filepath}[/dim]")


def handle_load(args: str):
    """Load a note into the conversation context."""
    if not args:
        console.print("[yellow]Usage: /load <name>  (e.g., /load htb-lame)[/yellow]")
        return

    content = memory.load(args.strip())
    if content is None:
        console.print(f"[red]✗ Note not found:[/red] {args.strip()}")
        # Suggest similar notes
        matches = memory.search(args.strip())
        if matches:
            console.print("[dim]Did you mean:[/dim]")
            for m in matches:
                console.print(f"  [cyan]{m['name']}[/cyan]")
        return

    engine.inject_context(content)
    console.print(f"[green]✓ Loaded note:[/green] [cyan]{args.strip()}[/cyan] into context")
    console.print(f"[dim]  ({len(content)} chars injected)[/dim]")


def handle_notes(_args: str):
    """List all saved notes."""
    from rich.table import Table

    notes = memory.list_notes()
    if not notes:
        console.print("[dim]No notes saved yet. Use /save <name> after a session.[/dim]")
        return

    table = Table(
        title="🗂️  Saved Notes",
        border_style="bright_red",
        show_header=True,
        header_style="bold yellow",
    )
    table.add_column("Name", style="bold cyan")
    table.add_column("Modified", style="dim")
    table.add_column("Size", style="dim", justify="right")

    for note in notes:
        size_str = f"{note['size']:,} B" if note["size"] < 1024 else f"{note['size'] / 1024:.1f} KB"
        table.add_row(note["name"], note["modified"], size_str)

    console.print(table)


def handle_search(args: str):
    """Search notes by keyword."""
    if not args:
        console.print("[yellow]Usage: /search <keyword>[/yellow]")
        return

    results = memory.search(args.strip())
    if not results:
        console.print(f"[dim]No notes matching '{args.strip()}'[/dim]")
        return

    console.print(f"[green]Found {len(results)} note(s):[/green]")
    for r in results:
        console.print(f"  [cyan]{r['name']}[/cyan]  [dim]({r['modified']})[/dim]")


def handle_delete(args: str):
    """Delete a saved note."""
    if not args:
        console.print("[yellow]Usage: /delete <name>[/yellow]")
        return

    if memory.delete(args.strip()):
        console.print(f"[green]✓ Deleted:[/green] {args.strip()}")
    else:
        console.print(f"[red]✗ Note not found:[/red] {args.strip()}")


def handle_clear(_args: str):
    """Clear conversation history."""
    engine.clear_history()
    console.print("[green]✓ Conversation cleared.[/green]")


COMMANDS = {
    "/save": handle_save,
    "/append": handle_append,
    "/load": handle_load,
    "/notes": handle_notes,
    "/search": handle_search,
    "/delete": handle_delete,
    "/clear": handle_clear,
    "/help": lambda _: print_help(),
}

# ──────────────────────────────────────────────
# Main REPL
# ──────────────────────────────────────────────

def main():
    """Main interactive loop."""
    # Startup
    console.clear()
    print_banner()

    # Test connection
    connected = engine.test_connection()
    print_status(connected, config.MODEL_NAME, config.LM_STUDIO_URL)

    if not connected:
        console.print(
            "[bold red]⚠️  Cannot reach LM Studio![/bold red]\n"
            f"[dim]Make sure the server is running at {config.LM_STUDIO_URL}[/dim]\n"
            "[dim]Start it in LM Studio → Local Server → Start Server[/dim]\n"
        )

    print_help()

    # Set up prompt with history
    history_file = config.BASE_DIR + "/.clawd_history"
    session = PromptSession(
        history=FileHistory(history_file),
        style=prompt_style,
    )

    # REPL
    while True:
        try:
            user_input = session.prompt(PROMPT_TEXT).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Exiting Clawd... 🦞[/dim]")
            break

        if not user_input:
            continue

        # ── Exit ──
        if user_input.lower() in ("/exit", "/quit", "exit", "quit"):
            console.print("[dim]Later. Stay sharp. 🦞[/dim]")
            break

        # ── Slash Commands ──
        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if cmd in COMMANDS:
                COMMANDS[cmd](args)
            else:
                console.print(f"[red]Unknown command:[/red] {cmd}. Type /help for available commands.")
            continue

        # ── Chat with Clawd ──
        console.print()
        response_text = ""

        try:
            with Live(console=console, refresh_per_second=8, vertical_overflow="visible") as live:
                for token in engine.chat_stream(user_input):
                    response_text += token
                    # Render as markdown in real-time
                    live.update(Markdown(response_text))
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            continue

        console.print()  # Spacing after response


if __name__ == "__main__":
    main()
