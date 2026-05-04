# Clawd 🦞 — Autonomous Offensive Security AI

**Clawd** is an autonomous offensive security agent powered by local Large Language Models (LLMs). It combines natural language reasoning with deterministic command execution to perform autonomous penetration testing, reconnaissance, and exploitation against target systems.

## Features

- **Autonomous Execution**: Runs commands, analyzes results, and adapts strategy without human intervention
- **Persistent Memory**: Multi-bucket memory system (Facts, Failures, Hypotheses) to maintain context across long engagements
- **Truth Gates**: Built-in verification layer that prevents LLM hallucinations by enforcing real terminal feedback
- **Stateful Reasoning**: Tracks target state, prevents retry loops, and logs verified findings
- **Multi-Platform Support**: Runs on Windows with transparent routing to WSL (Windows Subsystem for Linux) for native Linux tools
- **Telegram Integration**: Control the agent remotely via Telegram bot interface
- **Reinforcement Learning Ready**: Infrastructure for policy-based decision-making (experimental)

## Architecture

The system consists of four primary modules:

1. **Engine** (`engine.py`): Central orchestrator that manages LLM function calls and enforces Truth Gates
2. **Memory Systems** (`target_memory.py`, `notes_index.py`): Persistent storage for target state, findings, and indexed command outputs
3. **Executor** (`executor.py`): Routes commands to WSL, handles timeouts, and captures output
4. **Interfaces**: CLI (`clawd.py`) and Telegram Bot (`telegram_bot.py`)

For detailed architecture documentation, see [Clawd_Whitepaper.md](Clawd_Whitepaper.md).

## Prerequisites

- **Windows 10/11** with Windows Subsystem for Linux (WSL)
- **WSL Ubuntu 22.04 LTS** (with `root` access for network operations)
- **LM Studio** v0.3+ running locally
- **Python 3.8+**
- **Telegram Bot Token** (optional, for Telegram integration)

## Installation

### 1. Clone the Repository
```bash
git clone https://github.com/mike5t/hacker-ai.git
cd hacker-ai
```

### 2. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 3. Set Up Environment Variables

Create a `.env` file in the project root (do not commit this):
```bash
# LM Studio Configuration
LM_STUDIO_URL=http://localhost:1234/v1
MODEL_NAME=meta-llama-3.1-8b-instruct

# Telegram Bot (optional)
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

Or set environment variables directly:
```bash
export LM_STUDIO_URL="http://localhost:1234/v1"
export MODEL_NAME="meta-llama-3.1-8b-instruct"
export TELEGRAM_BOT_TOKEN="your_bot_token_here"
```

### 4. Set Up LM Studio

1. Download and install [LM Studio](https://lmstudio.ai)
2. Load a compatible model (e.g., `meta-llama-3.1-8b-instruct`)
3. Start the local server on port 1234
4. Verify connectivity: `curl http://localhost:1234/v1/models`

### 5. Verify WSL Setup

```bash
# Check WSL is installed
wsl --list --verbose

# Verify root access
wsl -u root whoami  # Should return 'root'
```

## Usage

### Interactive CLI

```bash
python clawd.py
```

Available commands:
- `/save <name>` — Save current conversation to memory
- `/load <name>` — Load a saved conversation
- `/target <IP>` — Set target for scanning/exploitation
- `/recall` — View current target state
- `/help` — Show all commands

### Telegram Bot

```bash
python telegram_bot.py
```

Send commands via Telegram to control the agent remotely:
```
/start           — Initialize bot
scan 10.10.10.5  — Scan target
exploit 10.10.10.5 — Attempt exploitation
status           — Show current status
```

### Test Suite

Run the test suite to validate functionality:
```bash
pytest test_agent.py -v
pytest test_rl.py -v
pytest test_web_recon.py -v
```

## Project Structure

```
.
├── clawd.py                    # Main CLI entry point
├── telegram_bot.py             # Telegram interface
├── config.py                   # Configuration & secrets (env vars)
├── engine.py                   # LLM orchestrator & Truth Gates
├── executor.py                 # Command execution (WSL routing)
├── memory.py                   # Memory system
├── target_memory.py            # Target state tracking
├── notes_index.py              # Output indexing & chunking
├── web_recon.py                # Web reconnaissance tools
├── utils.py                    # Utility functions
├── rl/                         # Reinforcement Learning modules
│   ├── action_space.py         # Action definitions
│   ├── policy_model.py         # Policy network
│   ├── training_loop.py        # Training logic
│   └── ...
├── memory/                     # Persistent storage (git-ignored)
│   ├── notes_index/            # Indexed command outputs
│   ├── targets/                # Target state files
│   └── rl_episodes/            # RL training data
├── workspace/                  # Working directory (git-ignored)
├── tests/                      # Test files
└── requirements.txt            # Python dependencies
```

## Configuration

### Key Settings (in `config.py`)

```python
# LM Model Parameters
TEMPERATURE = 0.7              # Model creativity (0.0 = deterministic, 1.0 = creative)
MAX_TOKENS = 2048              # Maximum response length
MAX_CONTEXT_MESSAGES = 40      # Keep last N messages in context

# Execution Limits
CMD_TIMEOUT = 120              # Default command timeout (seconds)
CMD_TIMEOUT_MAX = 600          # Maximum timeout (10 minutes)
MAX_TOOL_CALLS = 8             # Max tool calls per turn

# Reinforcement Learning
RL_ENABLED = False             # Set to True to enable policy layer
RL_POLICY_WEIGHT = 0.0         # 0.0 = LLM only, 1.0 = policy only
```

## Security Considerations

⚠️ **Warning**: This tool is designed for authorized security testing only.

### Sensitive Files (Git-Ignored)
- `.env` — Environment variables with secrets
- `memory/` — Target state and findings
- `workspace/` — Loot, captures, and scan outputs
- `*.ovpn` — VPN configuration files
- `__pycache__/` — Compiled Python

**Never commit these files.** Always use `.env` for secrets.

### Best Practices

1. **Use Environment Variables**: Never hardcode secrets in source code
2. **Restrict Permissions**: Ensure `workspace/` and `memory/` directories are readable only by the owner
3. **Rotate Tokens**: Regularly rotate Telegram bot tokens
4. **Audit Logs**: Review execution logs in `workspace/` regularly
5. **Network Isolation**: Run against lab environments only (e.g., Hack The Box, HackTheBox instances)

## How It Works

### Truth Gates

Clawd implements "Truth Gates" to prevent LLM hallucinations:

1. LLM issues a command via function call
2. Executor runs the command in WSL
3. If execution fails, output is prefixed with `⛔ TRUTH GATE: COMMAND FAILED`
4. LLM receives the failure signal and adapts strategy
5. Prevents retry loops and phantom successes

### Memory System

- **Bucket A (Facts)**: Irrefutable data (e.g., confirmed open ports)
- **Bucket B (Failures)**: Failed attempts and errors
- **Bucket C (Hypotheses)**: Unverified theories awaiting confirmation

This structure helps the LLM reason about what it knows vs. what it has tried.

### Execution Flow

```
User Input
    ↓
Engine (LLM)
    ↓
Function Call (e.g., run_command)
    ↓
Executor (WSL)
    ↓
Command Execution
    ↓
Output Analysis
    ↓
Truth Gate Injection
    ↓
Memory Update
    ↓
LLM Response
```

## Limitations & Known Issues

- **Context Window**: Limited by LLM context window (typically 2048-4096 tokens)
- **Tool Hallucination**: Despite Truth Gates, LLM may invent non-existent tools
- **Long Engagements**: Memory system may not scale beyond 8-12 hour sessions
- **False Positives**: Vulnerability detection relies on command output parsing
- **RL**: Reinforcement Learning module is experimental and disabled by default

## Troubleshooting

### LM Studio Connection Failed
```
Error: Failed to connect to LM Studio at http://localhost:1234/v1
```
**Solution**: Ensure LM Studio is running and listening on port 1234.

### WSL Not Found
```
Error: 'wsl' is not recognized as an internal or external command
```
**Solution**: Install WSL using `wsl --install` (Windows 10/11 only).

### Telegram Bot Not Responding
```
Error: TELEGRAM_BOT_TOKEN not set
```
**Solution**: Set the `TELEGRAM_BOT_TOKEN` environment variable.

### Command Timeout
```
Error: Command execution timed out after 120 seconds
```
**Solution**: Increase `CMD_TIMEOUT` or `CMD_TIMEOUT_MAX` in `config.py`.

## Contributing

Pull requests welcome! Please:

1. Follow PEP 8 code style
2. Add tests for new features
3. Update documentation
4. Do not commit sensitive data

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

## Disclaimer

This tool is for **authorized security testing only**. Unauthorized access to computer systems is illegal. Users are responsible for ensuring they have proper authorization before using this tool.

## References

- [Clawd Whitepaper](Clawd_Whitepaper.md) — Detailed architecture and design
- [LM Studio Documentation](https://lmstudio.ai/docs)
- [Windows Subsystem for Linux](https://learn.microsoft.com/en-us/windows/wsl/)
- [python-telegram-bot](https://python-telegram-bot.readthedocs.io/)

## Contact

For questions, issues, or feedback:
- Open an issue on GitHub
- Create a discussion thread

---

**Last Updated**: May 2026  
**Maintainer**: Mike5t  
**Status**: Active Development
