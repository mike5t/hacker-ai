"""
Clawd 🦞 — Action Space
Defines bounded, high-level actions for the RL policy layer.
Actions are tool/tactic-level, not raw shell strings.
The executor translates them into real commands.
"""


# ──────────────────────────────────────────────
# Action Registry
# ──────────────────────────────────────────────

ACTION_REGISTRY = {
    "recall_target": {
        "description": "Load all known intel for the active target",
        "tool": "recall_target",
        "category": "memory",
        "default_args": {},
    },
    "search_notes": {
        "description": "Search stored notes for the target",
        "tool": "search_notes",
        "category": "memory",
        "default_args": {},
    },
    "run_nmap_basic": {
        "description": "Quick TCP port scan (top 1000 ports)",
        "tool": "run_command",
        "category": "recon",
        "default_args": {"command": "nmap -T4 {target}"},
    },
    "run_nmap_service": {
        "description": "Service/version detection scan with scripts",
        "tool": "run_command",
        "category": "recon",
        "default_args": {"command": "nmap -sC -sV {target}"},
    },
    "run_nmap_full": {
        "description": "Full port scan (all 65535 ports)",
        "tool": "run_command",
        "category": "recon",
        "default_args": {"command": "nmap -p- -T4 {target}"},
    },
    "run_dir_enum": {
        "description": "Directory enumeration with gobuster",
        "tool": "run_command",
        "category": "web",
        "default_args": {
            "command": (
                "gobuster dir -u http://{target} "
                "-w /usr/share/wordlists/dirb/common.txt -t 30 -q"
            )
        },
    },
    "read_webpage": {
        "description": "Fetch and parse a webpage for text, links, comments",
        "tool": "read_webpage",
        "category": "web",
        "default_args": {"url": "http://{target}"},
    },
    "web_recon": {
        "description": "Full browser-driven web reconnaissance scan",
        "tool": "web_recon",
        "category": "web",
        "default_args": {"url": "http://{target}"},
    },
    "download_file": {
        "description": "Download a discovered file to loot/",
        "tool": "download_file",
        "category": "exploit",
        "default_args": {},
    },
    "read_file": {
        "description": "Read a file from the workspace",
        "tool": "read_file",
        "category": "util",
        "default_args": {},
    },
    "analyze_pcap": {
        "description": "Extract credentials from a pcap file",
        "tool": "analyze_pcap",
        "category": "exploit",
        "default_args": {},
    },
    "log_fact": {
        "description": "Record a confirmed fact about the target",
        "tool": "log_fact",
        "category": "memory",
        "default_args": {},
    },
    "log_failed": {
        "description": "Record a failed attempt to prevent retries",
        "tool": "log_failed",
        "category": "memory",
        "default_args": {},
    },
    "log_hypothesis": {
        "description": "Add an unverified hypothesis about the target",
        "tool": "log_hypothesis",
        "category": "memory",
        "default_args": {},
    },
    "idor_enum": {
        "description": "Enumerate IDOR by fuzzing numeric parameters",
        "tool": "idor_enum",
        "category": "exploit",
        "default_args": {},
    },
}


class ActionSpace:
    """Bounded action space for RL policy."""

    def __init__(self):
        self._actions = list(ACTION_REGISTRY.keys())
        self._name_to_idx = {name: i for i, name in enumerate(self._actions)}
        self._idx_to_name = {i: name for i, name in enumerate(self._actions)}

    @property
    def size(self) -> int:
        """Number of available actions."""
        return len(self._actions)

    def get_all_actions(self) -> list[str]:
        """Return all action names."""
        return list(self._actions)

    def get_action_metadata(self, name: str) -> dict | None:
        """Get metadata for a named action."""
        return ACTION_REGISTRY.get(name)

    def action_to_index(self, name: str) -> int:
        """Convert action name to numeric index for model I/O."""
        if name not in self._name_to_idx:
            raise ValueError(f"Unknown action: {name}")
        return self._name_to_idx[name]

    def index_to_action(self, idx: int) -> str:
        """Convert numeric index back to action name."""
        if idx not in self._idx_to_name:
            raise ValueError(f"Invalid action index: {idx}")
        return self._idx_to_name[idx]

    def expand_action(self, name: str, target: str = "", **ctx) -> tuple[str, dict]:
        """
        Expand a high-level action into (tool_name, args_dict) for the executor.

        Args:
            name: Action name from the registry.
            target: Active target IP/hostname for template substitution.
            **ctx: Additional context (url, path, etc.) to override defaults.

        Returns:
            (tool_name, args_dict) ready for executor dispatch.
        """
        meta = ACTION_REGISTRY.get(name)
        if not meta:
            raise ValueError(f"Unknown action: {name}")

        tool = meta["tool"]
        args = dict(meta["default_args"])

        # Template substitution: replace {target} in arg values
        for k, v in args.items():
            if isinstance(v, str) and "{target}" in v:
                args[k] = v.replace("{target}", target)

        # Override with context-provided args
        if "target" not in args and target:
            # Memory tools need the target param
            if meta["category"] == "memory":
                args["target"] = target

        # Apply any explicit overrides from ctx
        args.update(ctx)

        return tool, args

    def get_actions_by_category(self, category: str) -> list[str]:
        """Get all actions in a category (recon, web, memory, exploit, util)."""
        return [
            name for name, meta in ACTION_REGISTRY.items()
            if meta["category"] == category
        ]

    def is_valid_action(self, name: str) -> bool:
        """Check if an action name is in the registry."""
        return name in ACTION_REGISTRY
