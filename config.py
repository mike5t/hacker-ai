"""
Clawd 🦞 — Configuration
"""

import os

# ──────────────────────────────────────────────
# LM Studio Connection
# ──────────────────────────────────────────────
LM_STUDIO_URL = os.getenv("LM_STUDIO_URL", "http://169.254.49.150:5000/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen/qwen3-4b-thinking-2507")

# ──────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MIND_FILE = os.path.join(BASE_DIR, "mind.md")
MEMORY_DIR = os.path.join(BASE_DIR, "memory")

# ──────────────────────────────────────────────
# Model Parameters
# ──────────────────────────────────────────────
TEMPERATURE = 0.7
MAX_TOKENS = 2048
MAX_CONTEXT_MESSAGES = 40  # Keep last N messages to avoid overflowing context

# ──────────────────────────────────────────────
# Telegram
# ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "8261353318:AAGXlsb-MaE8ugUEb7LFu5yP7V6aW9nGoDw"
)

# ──────────────────────────────────────────────
# Command Execution
# ──────────────────────────────────────────────
WORKSPACE_DIR = os.path.join(BASE_DIR, "workspace")
CMD_TIMEOUT = 120       # Default command timeout in seconds
CMD_TIMEOUT_MAX = 600   # Max allowed timeout (10 min, for long scans)
MAX_TOOL_CALLS = 8      # Max tool calls per single turn (lowered to prevent over-execution)

# ──────────────────────────────────────────────
# Reinforcement Learning / Policy Layer
# ──────────────────────────────────────────────
RL_ENABLED = False              # Master switch — logging only until True
RL_EPISODES_DIR = os.path.join(MEMORY_DIR, "rl_episodes")
RL_MODELS_DIR = os.path.join(MEMORY_DIR, "rl_models")
RL_BUFFER_DIR = os.path.join(MEMORY_DIR, "rl_buffer")
RL_POLICY_WEIGHT = 0.0          # Blend: 0.0 = LLM only, 1.0 = policy only
