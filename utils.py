"""
Clawd 🦞 — Utilities
Banner art, tool reference lookups, and helpers.
"""

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

# ──────────────────────────────────────────────
# Banner
# ──────────────────────────────────────────────

BANNER = r"""
   ██████╗██╗      █████╗ ██╗    ██╗██████╗ 
  ██╔════╝██║     ██╔══██╗██║    ██║██╔══██╗
  ██║     ██║     ███████║██║ █╗ ██║██║  ██║
  ██║     ██║     ██╔══██║██║███╗██║██║  ██║
  ╚██████╗███████╗██║  ██║╚███╔███╔╝██████╔╝
   ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝ ╚═════╝ 
"""

TAGLINE = "🦞 Offensive Security AI • Local LLM • Hack the Planet"


def print_banner():
    """Print the Clawd startup banner."""
    banner_text = Text(BANNER, style="bold red")
    console.print(
        Panel(
            banner_text,
            subtitle=f"[dim]{TAGLINE}[/dim]",
            border_style="bright_red",
            padding=(0, 2),
        )
    )


def print_status(connected: bool, model: str, url: str):
    """Print connection status info."""
    if connected:
        status = "[bold green]● CONNECTED[/bold green]"
    else:
        status = "[bold red]● DISCONNECTED[/bold red]"
    
    console.print(f"  {status}  │  Model: [cyan]{model}[/cyan]  │  Server: [dim]{url}[/dim]")
    console.print()


def print_help():
    """Print available slash commands."""
    commands = [
        ("/save <name>", "Save current conversation as a box/CTF note"),
        ("/append <name>", "Append current conversation to an existing note"),
        ("/load <name>", "Load a note into context"),
        ("/notes", "List all saved notes"),
        ("/search <keyword>", "Search notes by keyword"),
        ("/delete <name>", "Delete a saved note"),
        ("/clear", "Clear conversation history"),
        ("/help", "Show this help message"),
        ("/exit", "Quit Clawd"),
    ]

    console.print("\n[bold bright_red]⚡ Commands[/bold bright_red]\n")
    for cmd, desc in commands:
        console.print(f"  [bold yellow]{cmd:<22}[/bold yellow] {desc}")
    console.print()


# ──────────────────────────────────────────────
# Quick Reference Tables
# ──────────────────────────────────────────────

TOOL_CATEGORIES = {
    "recon": {
        "title": "🔍 Reconnaissance",
        "tools": {
            "nmap": "Port scanning, service/version detection, NSE scripts",
            "masscan": "Fast port scanning for large ranges",
            "rustscan": "Speed scanning with nmap integration",
            "whatweb": "Web technology fingerprinting",
            "dig": "DNS enumeration",
            "theHarvester": "OSINT email/subdomain harvesting",
            "amass": "Subdomain enumeration",
        }
    },
    "web": {
        "title": "🌐 Web Application Testing",
        "tools": {
            "gobuster": "Directory and file brute forcing",
            "ffuf": "Fuzzing (dirs, params, vhosts, subdomains)",
            "nikto": "Web server vulnerability scanner",
            "burpsuite": "HTTP proxy, repeater, intruder",
            "sqlmap": "SQL injection automation",
            "hydra": "Brute force login forms",
        }
    },
    "privesc": {
        "title": "🔓 Privilege Escalation",
        "tools": {
            "linpeas": "Linux privilege escalation enumeration",
            "winpeas": "Windows privilege escalation enumeration",
            "pspy": "Process snooping without root",
            "bloodhound": "Active Directory attack paths",
            "mimikatz": "Credential extraction (Windows)",
        }
    },
    "exploit": {
        "title": "💥 Exploitation",
        "tools": {
            "searchsploit": "Exploit-DB local search",
            "metasploit": "Exploit framework",
            "msfvenom": "Payload generation",
            "pwntools": "Binary exploitation (Python)",
        }
    },
}


def print_tools(category: str | None = None):
    """Print tool reference table."""
    from rich.table import Table

    if category and category in TOOL_CATEGORIES:
        cats = {category: TOOL_CATEGORIES[category]}
    else:
        cats = TOOL_CATEGORIES

    for cat_key, cat_data in cats.items():
        table = Table(
            title=cat_data["title"],
            border_style="bright_red",
            show_header=True,
            header_style="bold yellow",
        )
        table.add_column("Tool", style="bold cyan", width=16)
        table.add_column("Purpose", style="white")

        for tool, purpose in cat_data["tools"].items():
            table.add_row(tool, purpose)

        console.print(table)
        console.print()
