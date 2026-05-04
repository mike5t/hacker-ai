"""
Clawd 🦞 — Core Engine
Handles LM Studio communication, tool calling, agent loop, and context management.
"""

import json
import re
from openai import OpenAI
import config
import executor
import target_memory
import notes_index

# RL integration (lazy-loaded to avoid import errors if torch missing)
_rl_available = False
try:
    from rl.action_space import ActionSpace
    from rl.state_encoder import StateEncoder
    from rl.episode_logger import EpisodeLogger
    from rl.reward_tracker import RewardTracker
    from rl.policy_model import PolicyModel
    _rl_available = True
except ImportError:
    pass


# ──────────────────────────────────────────────
# Tool Definitions (OpenAI function calling format)
# ──────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Execute a shell command on the local system. "
                "Use this for running security tools (nmap, gobuster, ffuf, etc.), "
                "executing scripts, installing packages, and any terminal operation. "
                "The command runs in the workspace directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute, e.g. 'nmap -sC -sV 10.10.10.5'"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 120, max 600 for long scans)"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write content to a file in the workspace directory. "
                "Use this to create scripts (Python, Bash, etc.), "
                "save scan results, write exploits, or create config files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Filename or relative path, e.g. 'exploit.py' or 'scans/nmap_results.txt'"
                    },
                    "content": {
                        "type": "string",
                        "description": "The file content to write"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the contents of a file from the workspace directory. "
                "Use this to review scripts, read scan output files, or check configs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Filename or relative path to read"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "log_fact",
            "description": (
                "Record a CONFIRMED fact about the current target. "
                "Use this after running a scan or command that reveals real data. "
                "Examples: open ports, service versions, usernames, file listings, config values. "
                "Only log facts backed by actual tool output — never guesses."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Target IP or hostname, e.g. '10.129.5.190'"
                    },
                    "fact": {
                        "type": "string",
                        "description": "The confirmed fact, e.g. '22/tcp open ssh OpenSSH 9.2p1 Debian'"
                    },
                    "evidence": {
                        "type": "string",
                        "description": "How this was confirmed, e.g. 'nmap -sC -sV output'"
                    }
                },
                "required": ["target", "fact"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "log_failed",
            "description": (
                "Record a FAILED attempt so it is NEVER retried. "
                "Use this after any command that fails: wrong credentials, denied access, "
                "timeouts, 404s, etc. This prevents wasting time re-trying the same thing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Target IP or hostname"
                    },
                    "attempt": {
                        "type": "string",
                        "description": "What was tried, e.g. 'ssh user:password'"
                    },
                    "result": {
                        "type": "string",
                        "description": "What happened, e.g. 'Permission denied'"
                    },
                    "exit_code": {
                        "type": "integer",
                        "description": "Exit code if applicable"
                    }
                },
                "required": ["target", "attempt", "result"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "log_hypothesis",
            "description": (
                "Add a new UNVERIFIED hypothesis, or update an existing one's status. "
                "Hypotheses are theories that need testing. "
                "To add new: provide target + hypothesis text. "
                "To update: provide target + hypothesis_id + status (testing/confirmed/disproved). "
                "Confirmed hypotheses auto-promote to facts. Disproved ones move to failed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Target IP or hostname"
                    },
                    "hypothesis": {
                        "type": "string",
                        "description": "The hypothesis text (for adding new)"
                    },
                    "hypothesis_id": {
                        "type": "string",
                        "description": "ID of hypothesis to update (e.g. 'H1')"
                    },
                    "status": {
                        "type": "string",
                        "description": "New status: testing, confirmed, or disproved"
                    }
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recall_target",
            "description": (
                "Load ALL known intel for a target: confirmed facts, failed attempts, "
                "and hypotheses. ALWAYS call this before starting work on a target "
                "to avoid repeating failed attempts and to see what's already known."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Target IP or hostname"
                    }
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "store_note",
            "description": (
                "Store important output or findings as a tagged note for later retrieval. "
                "Use this to save scan results, service banners, directory listings, "
                "credential findings, or any output you might need to reference later. "
                "Tags help you retrieve the right info fast."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Target IP or hostname"
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to store (scan output, findings, etc.)"
                    },
                    "tags": {
                        "type": "string",
                        "description": "Comma-separated tags, e.g. 'nmap,recon,ports' or 'web,dirs' or 'creds,ssh'"
                    },
                    "title": {
                        "type": "string",
                        "description": "Short title for the note, e.g. 'nmap full port scan'"
                    }
                },
                "required": ["target", "content", "tags"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_notes",
            "description": (
                "Search your stored notes for a target by keyword and/or tags. "
                "Use this to recall previous scan results, findings, or output "
                "without re-running commands. Tags: nmap, web, dirs, smb, ftp, ssh, "
                "creds, brute, privesc, enum, exploit, recon, http, sqli, misc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Target IP or hostname"
                    },
                    "query": {
                        "type": "string",
                        "description": "Keyword to search in note content (optional)"
                    },
                    "tags": {
                        "type": "string",
                        "description": "Comma-separated tags to filter by, e.g. 'nmap,recon'"
                    }
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_webpage",
            "description": (
                "Fetch a webpage and extract readable text, links, and hidden HTML comments. "
                "ALWAYS use this instead of running `curl` or `wget` when you want to read "
                "the text content of a webpage or look for sensitive info/comments. "
                "It automatically handles HTML parsing and SSL errors."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch, e.g. '10.129.5.190' or 'http://10.129.5.190/admin'"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_recon",
            "description": (
                "Run a FULL browser-driven web reconnaissance scan against a target URL. "
                "Uses a real Chromium browser to crawl the site, discover pages/forms/scripts, "
                "check security headers, extract JS endpoints, and generate a prioritised list "
                "of vulnerability hypotheses. Only call this when the user explicitly asks "
                "for web enumeration or recon — do NOT auto-trigger after a port scan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Target base URL, e.g. 'http://10.129.5.190' or 'http://10.129.5.190:8080'"
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "Maximum pages to crawl (default 30)"
                    },
                    "headless": {
                        "type": "boolean",
                        "description": "Run browser invisibly (default true)"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "idor_enum",
            "description": (
                "Enumerate IDOR (Insecure Direct Object Reference) by fuzzing a numeric "
                "parameter in a URL. Uses a real browser to visit each URL, so JS-rendered "
                "pages are handled. Provide a URL template with {FUZZ} as the placeholder "
                "for the number. Automatically filters out error pages. Use this when you "
                "find URLs with numeric IDs like /profile?id=1, /user/1, /order/1, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url_template": {
                        "type": "string",
                        "description": "URL with {FUZZ} placeholder, e.g. 'http://target.htb/profile?id={FUZZ}'"
                    },
                    "start": {
                        "type": "integer",
                        "description": "First number to try (default 1)"
                    },
                    "end": {
                        "type": "integer",
                        "description": "Last number to try (default 20, max 100)"
                    },
                    "match_codes": {
                        "type": "string",
                        "description": "Comma-separated HTTP status codes to keep (default '200,301,302')"
                    }
                },
                "required": ["url_template"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "download_file",
            "description": (
                "Download a file from a URL and save it to workspace/loot/. "
                "Uses curl via VPN so it works on HTB targets. "
                "Only use when the user asks to download a specific file or you have "
                "confirmed a downloadable file exists (e.g. from web_recon results)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full URL to download, e.g. 'http://10.10.10.5/files/backup.pcap'"
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional filename to save as (default: derived from URL)"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_pcap",
            "description": (
                "Analyze a .pcap or .pcapng network capture file using tshark (Wireshark CLI). "
                "Extracts credentials (FTP usernames/passwords, HTTP Basic Auth, HTTP POST data, "
                "Telnet sessions), shows protocol hierarchy, and TCP connection summaries. "
                "Only use when the user asks to analyze a pcap file that has already been downloaded."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to pcap file (relative to workspace, e.g. 'loot/capture.pcap')"
                    },
                    "display_filter": {
                        "type": "string",
                        "description": "Optional tshark display filter (e.g. 'http.authbasic' or 'ftp'). If empty, uses default credential extraction filter."
                    }
                },
                "required": ["filepath"]
            }
        }
    }
]

# Map tool names to executor functions
TOOL_FUNCTIONS = {
    "run_command": executor.run_command,
    "write_file": executor.write_file,
    "read_file": executor.read_file,
    "log_fact": executor.log_fact,
    "log_failed": executor.log_failed,
    "log_hypothesis": executor.log_hypothesis,
    "recall_target": executor.recall_target,
    "store_note": executor.store_note,
    "search_notes": executor.search_notes,
    "read_webpage": executor.read_webpage,
    "web_recon": executor.web_recon,
    "idor_enum": executor.idor_enum,
    "download_file": executor.download_file,
    "analyze_pcap": executor.analyze_pcap,
}


class ClawdEngine:
    """Offensive security AI engine with tool execution capability."""

    def __init__(self):
        self.client = OpenAI(
            base_url=config.LM_STUDIO_URL,
            api_key="lm-studio",
        )
        self.system_prompt = self._load_mind()
        self.history: list[dict] = []
        self.on_tool_call = None  # Callback: fn(tool_name, args, result)
        self.active_target: str | None = None  # Currently targeted IP/host
        self._target_last_used: float = 0  # Timestamp of last tool call referencing the target
        self._TARGET_TIMEOUT = 600  # 10 min — auto-clear stale targets

        # ── RL layer (observe + log, does not control unless RL_ENABLED) ──
        self._rl_ready = False
        if _rl_available:
            try:
                import os
                os.makedirs(config.RL_EPISODES_DIR, exist_ok=True)
                os.makedirs(config.RL_MODELS_DIR, exist_ok=True)
                self._action_space = ActionSpace()
                self._state_encoder = StateEncoder(self._action_space)
                self._episode_logger = EpisodeLogger(config.RL_EPISODES_DIR)
                self._reward_tracker = RewardTracker()
                self._policy_model = PolicyModel(
                    self._action_space, config.RL_MODELS_DIR
                )
                self._policy_model.load()  # Load checkpoint if exists
                self._rl_state: dict = {}  # Current step state
                self._rl_history_summary: dict = {}  # Running step counters
                self._rl_ready = True
            except Exception:
                self._rl_ready = False

    # Pattern to strip <think>...</think> blocks from Thinking models (e.g. Qwen3)
    _THINK_PATTERN = re.compile(r'<think>[\s\S]*?</think>\s*', re.IGNORECASE)

    # IP pattern for auto-detection from user messages
    _IP_PATTERN = re.compile(
        r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b'
    )

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Remove <think>...</think> blocks from model output.
        
        Thinking models (Qwen3, etc.) wrap chain-of-thought reasoning
        in <think> tags. This strips them so only the final answer
        reaches the user.
        """
        if not text:
            return text
        cleaned = ClawdEngine._THINK_PATTERN.sub('', text)
        return cleaned.strip()

    # ──────────────────────────────────────────
    # System Prompt
    # ──────────────────────────────────────────

    def _load_mind(self) -> str:
        """Load mind.md as the system prompt."""
        try:
            with open(config.MIND_FILE, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return (
                "You are Clawd 🦞, an offensive security specialist. "
                "Think like an attacker. Enumerate everything. Trust nothing."
            )

    # ──────────────────────────────────────────
    # Context Management
    # ──────────────────────────────────────────

    def _build_messages(self) -> list[dict]:
        """Build the messages array with system prompt + conversation history.
        
        Ensures the first message after system is always a user message,
        which is required by Llama 3.1's Jinja template when tools are present.
        Auto-injects active target memory into the system context.
        """
        # Auto-expire stale targets (no tool calls referencing them for 10 min)
        import time
        if (self.active_target
                and self._target_last_used
                and time.time() - self._target_last_used > self._TARGET_TIMEOUT):
            self.active_target = None
            self._target_last_used = 0

        # Build system prompt with target memory injection
        system_content = self.system_prompt
        if self.active_target:
            intel = target_memory.get_summary(self.active_target)
            system_content += f"\n\n---\n\n{intel}"

        messages = [{"role": "system", "content": system_content}]

        # Llama 3.1 template requires a user message before tool definitions.
        # If history is empty or doesn't start with a user message, inject one.
        if not self.history or self.history[0].get("role") != "user":
            messages.append({"role": "user", "content": "Hello, I'm ready to start."})
            messages.append({"role": "assistant", "content": "Clawd online. What's the target?"})

        messages.extend(self.history)
        return messages

    def _trim_history(self):
        """Keep history within the configured context window."""
        if len(self.history) > config.MAX_CONTEXT_MESSAGES:
            overflow = len(self.history) - config.MAX_CONTEXT_MESSAGES
            self.history = self.history[overflow:]

    def add_user_message(self, content: str):
        self.history.append({"role": "user", "content": content})
        self._trim_history()

    def add_assistant_message(self, content: str):
        self.history.append({"role": "assistant", "content": content})
        self._trim_history()

    def inject_context(self, context: str):
        self.history.append({
            "role": "user",
            "content": f"[CONTEXT LOADED]\n{context}"
        })
        self._trim_history()

    def clear_history(self):
        # End any active RL episode before clearing
        if self._rl_ready and self._episode_logger.is_recording:
            self._episode_logger.end_episode()
            self._rl_state = {}
            self._rl_history_summary = {}
        self.history.clear()
        self.active_target = None
        self._target_last_used = 0

    # ──────────────────────────────────────────
    # RL Transition Recording
    # ──────────────────────────────────────────

    def _rl_record_transition(self, tool_name: str, args: dict, result: dict):
        """
        Record a single RL transition after a tool execution.
        Called automatically from the agent loop — must never raise.
        """
        target = self.active_target or ""
        if not target:
            return

        # Start a new episode if needed
        if not self._episode_logger.is_recording:
            self._episode_logger.start_episode(target)
            self._rl_history_summary = {
                "steps": 0,
                "timeouts": 0,
                "empty_outputs": 0,
                "repeated_failures": 0,
                "web_recon_ran": False,
                "last_action": "",
                "last_action_success": False,
                "known_ports": [],
                "known_services": [],
            }

        # Map the tool call to an RL action name
        rl_action = self._rl_map_tool_to_action(tool_name, args)

        # Build current state
        mem_data = target_memory._load(target)
        notes_data = notes_index.list_chunks(target)
        state = self._state_encoder.encode(
            target, mem_data, notes_data, self._rl_history_summary
        )

        # Candidate actions = all valid actions (simple for now)
        candidates = self._action_space.get_all_actions()

        # Score with policy model (observation only, does not affect execution)
        try:
            ranked = self._policy_model.rank(state, candidates, self._state_encoder)
        except Exception:
            ranked = []

        # Build next state after this action
        next_state = self._state_encoder.encode_updated(
            state, rl_action, result, target, mem_data, notes_data
        )

        # Compute reward
        reward = self._reward_tracker.compute(state, rl_action, result, next_state)

        # Record the step
        self._episode_logger.record_step(
            state=state,
            candidate_actions=candidates,
            selected_action=rl_action,
            result=result,
            reward=reward,
            next_state=next_state,
            tool_args=args,
        )

        # Update running history summary for subsequent states
        self._rl_history_summary["steps"] = next_state.get("steps", 0)
        self._rl_history_summary["timeouts"] = next_state.get("timeouts", 0)
        self._rl_history_summary["empty_outputs"] = next_state.get("empty_outputs", 0)
        self._rl_history_summary["last_action"] = rl_action
        self._rl_history_summary["last_action_success"] = next_state.get("last_action_success", False)
        self._rl_history_summary["web_recon_ran"] = next_state.get("web_recon_ran", False)
        self._rl_history_summary["known_ports"] = next_state.get("ports", [])
        self._rl_history_summary["known_services"] = next_state.get("services", [])

        # Store current state for next iteration
        self._rl_state = next_state

    def _rl_map_tool_to_action(self, tool_name: str, args: dict) -> str:
        """Map a raw tool call to the closest RL action name."""
        # Direct tool name matches
        if tool_name in ("recall_target", "search_notes", "read_webpage",
                         "web_recon", "download_file", "read_file",
                         "analyze_pcap", "log_fact", "log_failed",
                         "log_hypothesis", "idor_enum"):
            return tool_name

        if tool_name == "store_note":
            return "search_notes"  # Closest memory action

        if tool_name == "run_command":
            cmd = args.get("command", "").lower()
            if "nmap" in cmd:
                if "-sc" in cmd or "-sv" in cmd:
                    return "run_nmap_service"
                if "-p-" in cmd:
                    return "run_nmap_full"
                return "run_nmap_basic"
            if any(t in cmd for t in ("gobuster", "dirb", "ffuf", "feroxbuster")):
                return "run_dir_enum"
            return "run_nmap_basic"  # Fallback for other commands

        return "recall_target"  # Safe fallback

    def _detect_and_switch_target(self, user_input: str):
        """
        Scan user message for an IP address. If a NEW target IP is found,
        auto-switch to it and clear conversation history for a fresh start.
        """
        import time as _time
        matches = self._IP_PATTERN.findall(user_input)
        if not matches:
            return

        new_ip = matches[0]
        # Ignore localhost and LM Studio's own IP
        if new_ip in ("127.0.0.1", "169.254.49.150"):
            return

        if new_ip != self.active_target:
            # Switching targets — clean slate
            self.history.clear()
            self.active_target = new_ip
            self._target_last_used = _time.time()

    # ──────────────────────────────────────────
    # Tool Execution
    # ──────────────────────────────────────────

    def _execute_tool(self, name: str, arguments: dict) -> str:
        """Execute a tool and return the result as a string."""
        func = TOOL_FUNCTIONS.get(name)
        if not func:
            return json.dumps({"error": f"Unknown tool: {name}"})

        result = func(**arguments)

        # Notify callback if set (for UI updates)
        if self.on_tool_call:
            self.on_tool_call(name, arguments, result)

        raw_json = json.dumps(result, indent=2)

        # ── Truth Gates ──────────────────────────
        # Inject forced annotations the LLM cannot override.
        # These go BEFORE the raw JSON so the LLM reads them first.
        return self._apply_truth_gates(name, result, raw_json)

    def _apply_truth_gates(self, tool_name: str, result: dict,
                           raw_json: str) -> str:
        """
        Post-process tool results with truth-gate annotations.
        These force the LLM to acknowledge failures and empty output.
        """
        gates = []

        if tool_name == "run_command":
            exit_code = result.get("exit_code")
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            timed_out = result.get("timed_out", False)
            cmd = result.get("command", "?")

            if timed_out:
                gates.append(
                    f"⏱️ TRUTH GATE: COMMAND TIMED OUT.\n"
                    f"Command `{cmd}` did not complete — probably interactive or hung.\n"
                    f"You MUST NOT claim it succeeded. Try a non-interactive alternative."
                )
            elif exit_code is not None and exit_code != 0:
                snippet = (stderr[:300] or stdout[:300] or "no error details")
                gates.append(
                    f"⛔ TRUTH GATE: COMMAND FAILED (exit {exit_code}).\n"
                    f"Command `{cmd}` returned non-zero. This means it DID NOT WORK.\n"
                    f"Error: {snippet}\n"
                    f"You MUST report this failure honestly. Do NOT claim success.\n"
                    f"Suggest 1-2 alternative approaches."
                )
            else:
                # Success — but check for empty output
                if not stdout.strip():
                    gates.append(
                        f"⚠️ TRUTH GATE: COMMAND SUCCEEDED BUT PRODUCED NO OUTPUT.\n"
                        f"Command `{cmd}` exited 0 but stdout is empty.\n"
                        f"You MUST NOT invent or fabricate output. State that there was no output."
                    )

        elif tool_name == "read_file":
            success = result.get("success", False)
            content = result.get("content", "")
            error = result.get("error", "")
            path = result.get("path", "?")

            if not success:
                gates.append(
                    f"⛔ TRUTH GATE: FILE READ FAILED.\n"
                    f"Could not read `{path}`: {error}\n"
                    f"You MUST NOT fabricate file contents. The file was NOT read."
                )
            elif not content.strip():
                gates.append(
                    f"⚠️ TRUTH GATE: FILE IS EMPTY.\n"
                    f"File `{path}` exists but contains no data.\n"
                    f"You MUST NOT invent contents."
                )
            else:
                # File read succeeded with content — add proof
                gates.append(
                    f"✅ TRUTH GATE: FILE READ OK ({len(content)} bytes from `{path}`).\n"
                    f"Content below is REAL. You may reference it."
                )

        elif tool_name == "read_webpage":
            error_msg = result.get("error", "")
            status_code = result.get("status_code", 0)
            url = result.get("url", "?")
            visible_text = result.get("visible_text", "")

            if error_msg:
                gates.append(
                    f"⛔ TRUTH GATE: WEBPAGE FETCH FAILED.\n"
                    f"Could not load `{url}`: {error_msg}\n"
                    f"You MUST report this connection failure. DO NOT claim you read the page."
                )
            elif status_code in (404, 401, 403, 500):
                gates.append(
                    f"⛔ TRUTH GATE: HTTP ERROR {status_code}.\n"
                    f"The server returned an error for `{url}`.\n"
                    f"You MUST NOT invent contents. State the HTTP error."
                )
            elif not visible_text.strip() and not result.get("hidden_comments"):
                gates.append(
                    f"⚠️ TRUTH GATE: WEBPAGE IS EMPTY.\n"
                    f"Page `{url}` loaded but has no readable text or comments.\n"
                    f"You MUST NOT fabricate data."
                )

        if gates:
            gate_block = "\n".join(gates)
            return f"{gate_block}\n\n{raw_json}"
        return raw_json

    # ──────────────────────────────────────────
    # Agent Loop — Tool Calling
    # ──────────────────────────────────────────

    def chat(self, user_input: str) -> str:
        """
        Send a message and run the agent loop.
        The LLM can call tools, get results, and call more tools
        until it produces a final text response.
        
        Includes deduplication and retry prevention to avoid wasting
        tool calls on repeated or failed commands.
        """
        self._detect_and_switch_target(user_input)
        self.add_user_message(user_input)

        # Track what we've done THIS turn to prevent duplicates
        _executed_this_turn: set[str] = set()   # "tool_name:arg_hash"
        _failed_this_turn: set[str] = set()     # commands that failed

        for iteration in range(config.MAX_TOOL_CALLS + 1):
            messages = self._build_messages()

            try:
                response = self.client.chat.completions.create(
                    model=config.MODEL_NAME,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                    temperature=config.TEMPERATURE,
                    max_tokens=config.MAX_TOKENS,
                )
            except Exception as e:
                error_msg = f"⚠️ Connection error: {e}"
                self.add_assistant_message(error_msg)
                return error_msg

            choice = response.choices[0]
            message = choice.message

            # ── Case 1: LLM wants to call tools ──
            if message.tool_calls:
                # Add the assistant message with tool calls to history
                self.history.append({
                    "role": "assistant",
                    "content": self._strip_thinking(message.content or ""),
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            }
                        }
                        for tc in message.tool_calls
                    ]
                })

                # Execute each tool call and add results
                for tool_call in message.tool_calls:
                    try:
                        args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        args = {"command": tool_call.function.arguments}

                    tool_name = tool_call.function.name

                    # ── Intercept: LLM trying to run a tool name as a shell command ──
                    if tool_name == "run_command":
                        cmd_str = args.get("command", "")
                        # Catch cases like `run_command("read_webpage http://...")`
                        _intercepted = False
                        for real_tool in ["read_webpage", "web_recon", "recall_target",
                                          "log_fact", "log_failure", "store_note",
                                          "search_notes", "idor_enum",
                                          "download_file", "analyze_pcap"]:
                            if cmd_str.strip().startswith(real_tool):
                                # Try to parse arguments from the command string
                                # e.g. "web_recon url=http://... max_pages=30" → {"url": "http://...", "max_pages": 30}
                                _parsed_args = {}
                                _parts = cmd_str.strip().split(None, 1)
                                if len(_parts) > 1:
                                    import shlex
                                    try:
                                        _tokens = shlex.split(_parts[1])
                                    except ValueError:
                                        _tokens = _parts[1].split()
                                    for _tok in _tokens:
                                        if "=" in _tok:
                                            _k, _v = _tok.split("=", 1)
                                            # Try to parse as int/bool
                                            if _v.lower() == "true":
                                                _parsed_args[_k] = True
                                            elif _v.lower() == "false":
                                                _parsed_args[_k] = False
                                            else:
                                                try:
                                                    _parsed_args[_k] = int(_v)
                                                except ValueError:
                                                    _parsed_args[_k] = _v
                                        else:
                                            # Positional arg — assume it's a URL or primary param
                                            if "url" not in _parsed_args:
                                                _parsed_args["url"] = _tok

                                if _parsed_args and real_tool in TOOL_FUNCTIONS:
                                    # Actually call the real tool with parsed args
                                    result_str = (
                                        f"⚡ AUTO-REDIRECT: '{real_tool}' is a TOOL, not a shell command. "
                                        f"Calling it directly with args: {_parsed_args}\n\n"
                                    )
                                    try:
                                        actual_result = self._execute_tool(real_tool, _parsed_args)
                                        result_str += actual_result
                                        _executed_this_turn.add(f"{real_tool}:{json.dumps(_parsed_args, sort_keys=True)}")
                                    except Exception as _e:
                                        result_str += f"⛔ Tool call failed: {_e}"
                                else:
                                    result_str = (
                                        f"⛔ TRUTH GATE: '{real_tool}' is NOT a shell command. "
                                        f"It is a TOOL. Call it directly as a tool function, "
                                        f"not via run_command. Try again using the "
                                        f"'{real_tool}' tool instead."
                                    )
                                self.history.append({
                                    "role": "tool",
                                    "tool_call_id": tool_call.id,
                                    "content": result_str,
                                })
                                if self.on_tool_call:
                                    self.on_tool_call(real_tool, _parsed_args or args, result_str)
                                _intercepted = True
                                break  # break out of for-real_tool loop
                        if _intercepted:
                            continue  # continue the for-tool_call loop

                        # ── Intercept: Force web tools before gobuster/curl/python ──
                        # Only block simple BROWSING commands, NOT form submissions or attacks
                        browse_tools = ["gobuster", "dirb", "nikto"]
                        is_browse_cli = any(cmd_str.strip().startswith(t) for t in browse_tools)
                        # Curl/wget are only blocked if doing simple GET (no POST data)
                        is_curl_get = (
                            cmd_str.strip().startswith(("curl", "wget"))
                            and not any(flag in cmd_str for flag in ["-d ", "--data", "-X POST", "-X PUT", "--post"])
                        )
                        # Python one-liners that just fetch (not post)
                        is_python_http = (
                            cmd_str.strip().startswith(("python", "python3"))
                            and any(kw in cmd_str for kw in ["requests.get", "urllib", "httpx", "http.client"])
                            and "requests.post" not in cmd_str
                        )
                        is_web_cli = is_browse_cli or is_curl_get
                        has_http = "http" in cmd_str.lower()
                        used_web_tools = any(
                            s.startswith("read_webpage:") or s.startswith("web_recon:")
                            for s in _executed_this_turn
                        )

                        if (is_web_cli or is_python_http) and has_http:
                            import re as _re
                            url_match = _re.search(r'https?://[^\s"\']+', cmd_str)
                            target_url = url_match.group(0) if url_match else None

                            if not used_web_tools and target_url:
                                # Auto-run read_webpage instead
                                result_str = self._execute_tool("read_webpage", {"url": target_url})
                                _executed_this_turn.add(f"read_webpage:{json.dumps({'url': target_url})}")
                                self.history.append({
                                    "role": "tool",
                                    "tool_call_id": tool_call.id,
                                    "content": (
                                        f"⚡ AUTO-REDIRECT: Instead of {cmd_str.split()[0]}, "
                                        f"I used the built-in `read_webpage` tool.\n\n"
                                        f"{result_str}\n\n"
                                        f"💡 Report these findings to the user and WAIT "
                                        f"for their next instruction. Do NOT auto-chain."
                                    ),
                                })
                                if self.on_tool_call:
                                    self.on_tool_call("read_webpage", {"url": target_url}, result_str)
                                continue
                            elif used_web_tools and is_python_http:
                                # Python requests is ALWAYS redundant when we have read_webpage
                                result_str = (
                                    "⛔ BLOCKED: Do NOT use `python requests` to fetch web "
                                    "pages. You already have data from `read_webpage` and/or "
                                    "`web_recon`. Use THAT data to decide your next attack. "
                                    "Check for login forms, registration pages, file uploads, "
                                    "or interesting paths in the results you already have."
                                )
                                self.history.append({
                                    "role": "tool",
                                    "tool_call_id": tool_call.id,
                                    "content": result_str,
                                })
                                if self.on_tool_call:
                                    self.on_tool_call(tool_name, args, result_str)
                                continue

                    # ── Deduplication: skip if same tool+args already ran this turn ──
                    call_sig = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
                    if call_sig in _executed_this_turn:
                        result_str = (
                            f"⚠️ DUPLICATE BLOCKED: You already called "
                            f"{tool_name} with these exact arguments this turn. "
                            f"Use the previous result instead of re-running."
                        )
                        self.history.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_str,
                        })
                        if self.on_tool_call:
                            self.on_tool_call(tool_name, args, result_str)
                        continue

                    # ── Retry prevention: skip if same command already failed ──
                    if call_sig in _failed_this_turn:
                        result_str = (
                            f"⛔ RETRY BLOCKED: This exact command already failed "
                            f"this turn. Retrying will produce the same error. "
                            f"Try a DIFFERENT approach or tool instead."
                        )
                        self.history.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_str,
                        })
                        if self.on_tool_call:
                            self.on_tool_call(tool_name, args, result_str)
                        continue

                    # ── Execute the tool ──
                    result_str = self._execute_tool(tool_name, args)
                    _executed_this_turn.add(call_sig)

                    # Track failures for retry prevention
                    _result_data = None
                    try:
                        _result_data = json.loads(result_str.split("\n\n", 1)[-1])
                        if isinstance(_result_data, dict) and (_result_data.get("exit_code", 0) != 0 or _result_data.get("timed_out")):
                            _failed_this_turn.add(call_sig)
                    except (json.JSONDecodeError, AttributeError, TypeError):
                        pass

                    # Auto-detect active target from tool calls
                    import time as _time
                    if "target" in args and args["target"]:
                        new_target = args["target"]
                        if self.active_target and new_target != self.active_target:
                            self.history.clear()
                            self.history.append({"role": "user", "content": user_input})
                        self.active_target = new_target
                        self._target_last_used = _time.time()
                    elif self.active_target:
                        self._target_last_used = _time.time()

                    self.history.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_str,
                    })

                    # Auto-capture significant command output as a note
                    if (tool_call.function.name == "run_command"
                            and self.active_target):
                        try:
                            result_data = json.loads(result_str.split("\n\n", 1)[-1])  # skip truth gate prefix
                            cmd = result_data.get("command", "")
                            stdout = result_data.get("stdout", "")
                            exit_code = result_data.get("exit_code", -1)
                            if notes_index.should_auto_capture(cmd, stdout, exit_code):
                                notes_index.auto_capture(
                                    self.active_target, cmd, stdout
                                )
                        except (json.JSONDecodeError, KeyError):
                            pass  # Skip auto-capture on parse errors

                    # ── RL: Record transition ──────────────────
                    if self._rl_ready and self.active_target:
                        try:
                            self._rl_record_transition(
                                tool_name, args, _result_data or {}
                            )
                        except Exception:
                            pass  # RL logging must never break the agent

                # ── Scope enforcement: nudge LLM to stop after primary action ──
                # After the first batch of "action" tools (nmap, web_recon, etc.),
                # inject a reminder to report findings and wait for the user.
                # BUT skip this if the user explicitly asked for a broad workflow.
                _action_tools = {"run_command", "read_webpage", "web_recon",
                                 "idor_enum", "download_file", "analyze_pcap"}
                _action_ran = any(
                    s.split(":", 1)[0] in _action_tools
                    for s in _executed_this_turn
                )
                # Detect broad requests where the user wants multi-step execution
                _broad_keywords = [
                    "do all", "do everything", "full recon", "enumerate everything",
                    "go ham", "run everything", "complete workflow", "all steps",
                    "proceed with everything", "yes do all", "yes do everything"
                ]
                _user_wants_broad = any(
                    kw in user_input.lower() for kw in _broad_keywords
                )
                if iteration >= 1 and _action_ran and not _user_wants_broad:
                    self.history.append({
                        "role": "user",
                        "content": (
                            "[SYSTEM] You have completed the requested action. "
                            "Report ONLY what the tools ACTUALLY returned — do NOT "
                            "write fake command output, do NOT invent scan results, "
                            "do NOT fabricate terminal sessions. If you did not run "
                            "a command with run_command, you DO NOT have its output. "
                            "Summarize the REAL tool results and suggest next steps, "
                            "but WAIT for the user's instruction before proceeding."
                        )
                    })

                # Continue the loop — LLM will see tool results and decide next
                continue

            # ── Case 2: LLM gives a text response (done) ──
            final_text = self._strip_thinking(message.content or "")

            # ── Fabrication Detector ──────────────────────
            # Check if the response contains fake command output.
            # If the agent wrote code blocks that look like terminal output
            # but never actually ran those commands, flag it.
            _ran_commands = {
                sig.split(":", 1)[1] for sig in _executed_this_turn
                if sig.startswith("run_command:")
            }
            if final_text.strip():
                import re as _re
                # Patterns that indicate fabricated command output
                _fake_output_markers = [
                    r"Starting Nmap",
                    r"Nmap scan report for",
                    r"Gobuster v",
                    r"ffuf v",
                    r"LinPeas",
                    r"linpeas\.sh",
                    r"\$ whoami",
                    r"root@\w+:",
                    r"user@\w+:",
                    r"Last login:",
                    r"sshpass ",
                    r"WhatWeb scan report",
                ]
                # Only check inside code blocks (``` ... ```)
                _code_blocks = _re.findall(r'```[\s\S]*?```', final_text)
                _has_fake_output = False
                for block in _code_blocks:
                    for marker in _fake_output_markers:
                        if _re.search(marker, block):
                            # Check if we actually ran a command that could produce this
                            if not _ran_commands:
                                _has_fake_output = True
                                break
                    if _has_fake_output:
                        break

                if _has_fake_output:
                    # Strip all code blocks that contain fake output
                    cleaned = _re.sub(
                        r'```[\s\S]*?```',
                        '\n*[Output removed — command was not actually executed]*\n',
                        final_text
                    )
                    final_text = (
                        cleaned.strip()
                        + "\n\n---\n⚠️ **Some output above was fabricated.** "
                        "I did not actually run those commands. "
                        "Tell me which commands to run and I'll execute them for real."
                    )

            # ── Lite Verifier Pass ──────────────────────
            # Run a quick verification check on the response.
            # Only verify when tool calls happened (there's work to check).
            if _executed_this_turn and final_text.strip():
                verified = self._verify_response(final_text, _executed_this_turn)
                if verified:
                    final_text = verified

            self.add_assistant_message(final_text)
            return final_text

        # If we exhausted the loop, return what we have
        return "⚠️ Reached maximum tool calls. Here's what I found so far."

    # ──────────────────────────────────────────
    # Lite Verifier Pass
    # ──────────────────────────────────────────

    # System prompt for the verifier — conservative, high-confidence only
    _VERIFIER_SYSTEM = (
        "You are a VERIFIER for a hacking agent called Clawd. "
        "Your job is to check the agent's response against the ACTUAL tool output below.\n\n"
        "RULES:\n"
        "1. DEFAULT TO LGTM. Only flag issues you are CERTAIN about based on the tool output you can see.\n"
        "2. You may NOT have the full tool output (it may be truncated). "
        "If you cannot see the relevant data in the tool results below, ASSUME the agent is correct and say LGTM.\n"
        "3. Only flag these CLEAR-CUT issues:\n"
        "   - Agent claims success but tool output shows an explicit FAILURE (exit code != 0, HTTP 404/500, error message)\n"
        "   - Agent claims a file was read/downloaded but tool output shows it FAILED\n"
        "   - Agent invents data not present in ANY tool output\n"
        "4. Do NOT flag:\n"
        "   - Minor wording differences (e.g. 'port 80' vs '80/tcp')\n"
        "   - Agent summarizing or paraphrasing tool output\n"
        "   - Things implied by standard tool behavior (e.g. nmap -sC -sV always shows port+service+version)\n"
        "   - Reasonable inferences from partial output\n\n"
        "If everything looks correct OR you are unsure, respond with EXACTLY: LGTM\n"
        "If there is a CLEAR factual error, respond with a brief correction starting with "
        "'⚠️ Verifier correction:' in under 50 words."
    )

    def _verify_response(self, agent_response: str,
                         executed: set) -> str | None:
        """
        Run a single lightweight LLM call to verify the agent's final response.
        Returns corrected text if issues found, or None if LGTM.
        """
        # Build a compact summary of what tools ran this turn
        tool_summary = "\n".join(
            f"- {sig.split(':', 1)[0]}({sig.split(':', 1)[1][:200]})"
            for sig in list(executed)[:8]
        )

        # Collect tool results from history — give verifier MORE context
        recent_results = []
        for msg in reversed(self.history[-16:]):
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                # Give verifier enough context to actually verify (1500 chars)
                recent_results.append(content[:1500])
                if len(recent_results) >= 6:
                    break
        recent_results.reverse()

        verifier_input = (
            f"## Tools executed this turn:\n{tool_summary}\n\n"
            f"## Tool results (may be truncated — do NOT correct based on missing data):\n"
            + "\n---\n".join(recent_results)
            + f"\n\n## Agent's response to verify:\n{agent_response[:2000]}"
        )

        try:
            response = self.client.chat.completions.create(
                model=config.MODEL_NAME,
                messages=[
                    {"role": "system", "content": self._VERIFIER_SYSTEM},
                    {"role": "user", "content": verifier_input},
                ],
                temperature=0.1,  # Low temp for factual checking
                max_tokens=300,   # Keep it brief
            )

            verdict = response.choices[0].message.content or ""
            verdict = verdict.strip()

            # If the verifier says LGTM, no changes needed
            if "LGTM" in verdict.upper():
                return None

            # Verifier found issues — append correction to original response
            if verdict and "verifier" in verdict.lower():
                return f"{agent_response}\n\n---\n{verdict}"

            # Unclear verdict — don't modify the response
            return None

        except Exception:
            # If verifier fails (connection issue, etc.), don't block the response
            return None

    # ──────────────────────────────────────────
    # Streaming (for terminal CLI, no tools)
    # ──────────────────────────────────────────

    def chat_stream(self, user_input: str):
        """
        Simple streaming chat WITHOUT tool calling.
        Used by the terminal CLI for live-typing.
        """
        self.add_user_message(user_input)
        messages = self._build_messages()

        try:
            stream = self.client.chat.completions.create(
                model=config.MODEL_NAME,
                messages=messages,
                temperature=config.TEMPERATURE,
                max_tokens=config.MAX_TOKENS,
                stream=True,
            )

            full_response = ""
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    full_response += token
                    yield token

            self.add_assistant_message(full_response)

        except Exception as e:
            error_msg = f"⚠️ Connection error: {e}"
            yield error_msg
            self.add_assistant_message(error_msg)

    # ──────────────────────────────────────────
    # Health Check
    # ──────────────────────────────────────────

    def test_connection(self) -> bool:
        try:
            self.client.models.list()
            return True
        except Exception:
            return False
