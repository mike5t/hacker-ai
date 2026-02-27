"""
Clawd 🦞 — Core Engine
Handles LM Studio communication, tool calling, agent loop, and context management.
"""

import json
from openai import OpenAI
import config
import executor
import target_memory
import notes_index


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
        self.history.clear()

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
        
        Returns the final text response.
        """
        self.add_user_message(user_input)

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
                    "content": message.content or "",
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

                    result_str = self._execute_tool(tool_call.function.name, args)

                    # Auto-detect active target from tool calls
                    if "target" in args and args["target"]:
                        self.active_target = args["target"]

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

                # Continue the loop — LLM will see tool results and decide next
                continue

            # ── Case 2: LLM gives a text response (done) ──
            final_text = message.content or ""
            self.add_assistant_message(final_text)
            return final_text

        # If we exhausted the loop, return what we have
        return "⚠️ Reached maximum tool calls. Here's what I found so far."

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
