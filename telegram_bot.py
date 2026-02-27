"""
Clawd 🦞 — Telegram Bot Interface
Chat with your offensive security AI via Telegram.
Supports tool execution — Clawd can run commands, write scripts, and automate scans.

Usage:
    python telegram_bot.py
"""

import asyncio

import json
import logging
import target_memory
from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatAction, ParseMode

import config
from engine import ClawdEngine
from memory import Memory

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("clawd-telegram")

# ──────────────────────────────────────────────
# Per-user session management
# ──────────────────────────────────────────────
user_engines: dict[int, ClawdEngine] = {}
user_tool_logs: dict[int, list[str]] = {}
memory = Memory()


def get_engine(user_id: int) -> ClawdEngine:
    """Get or create a ClawdEngine for a specific user."""
    if user_id not in user_engines:
        engine = ClawdEngine()
        # Set up tool call logging for this user
        user_tool_logs[user_id] = []
        engine.on_tool_call = lambda name, args, result: _log_tool_call(user_id, name, args, result)
        user_engines[user_id] = engine
        logger.info(f"Created new engine for user {user_id}")
    return user_engines[user_id]


def _log_tool_call(user_id: int, name: str, args: dict, result: dict):
    """Log tool call for later display."""
    if user_id not in user_tool_logs:
        user_tool_logs[user_id] = []

    if name == "run_command":
        cmd = args.get("command", "?")
        exit_code = result.get("exit_code", "?")
        user_tool_logs[user_id].append(f"⚙️ `{cmd}`  →  exit {exit_code}")
    elif name == "write_file":
        path = args.get("path", "?")
        user_tool_logs[user_id].append(f"📝 Wrote `{path}`")
    elif name == "read_file":
        path = args.get("path", "?")
        user_tool_logs[user_id].append(f"📖 Read `{path}`")
    elif name == "log_fact":
        user_tool_logs[user_id].append(f"✅ Fact: {args.get('fact', '?')[:60]}")
    elif name == "log_failed":
        user_tool_logs[user_id].append(f"❌ Failed: {args.get('attempt', '?')[:60]}")
    elif name == "log_hypothesis":
        text = args.get('hypothesis', args.get('hypothesis_id', '?'))
        user_tool_logs[user_id].append(f"💡 Hypothesis: {str(text)[:60]}")
    elif name == "recall_target":
        user_tool_logs[user_id].append(f"🧠 Recalled intel for {args.get('target', '?')}")
    elif name == "store_note":
        user_tool_logs[user_id].append(f"💾 Stored note: {args.get('title', 'note')}")
    elif name == "search_notes":
        user_tool_logs[user_id].append(f"🔎 Searched notes for '{args.get('query', args.get('tags', '?'))}'")
    elif name == "read_webpage":
        user_tool_logs[user_id].append(f"🌐 Browsed: `html {args.get('url', '?')}`")


# ──────────────────────────────────────────────
# Command Handlers
# ──────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_engine(user.id)

    welcome = (
        "🦞 *Clawd — Offensive Security AI*\n\n"
        "I'm your hacking assistant with a live terminal.\n"
        "I can *run commands*, *write scripts*, and *automate scans*.\n\n"
        "*Try:*\n"
        '• "Run whoami"\n'
        '• "Scan 10.10.10.5 with nmap"\n'
        '• "Write a port scanner script and run it"\n\n'
        "*Commands:*\n"
        "/clear — Reset conversation\n"
        "/save `name` — Save conversation\n"
        "/load `name` — Load notes into context\n"
        "/notes — List saved notes\n"
        "/help — Show this message\n\n"
        "_Hack the planet. 🦞_"
    )
    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🦞 *Clawd Commands*\n\n"
        "Just type any message to chat.\n"
        "I can execute commands and write scripts.\n\n"
        "/clear — Reset conversation\n"
        "/save `name` — Save conversation\n"
        "/load `name` — Load notes\n"
        "/notes — List notes\n"
        "/search `keyword` — Search notes\n"
        "/delete `name` — Delete a note\n"
        "/help — This message"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = get_engine(update.effective_user.id)
    engine.clear_history()
    await update.message.reply_text("✅ Conversation cleared.")


async def cmd_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/save name`", parse_mode=ParseMode.MARKDOWN)
        return

    name = context.args[0]
    engine = get_engine(update.effective_user.id)

    conversation = []
    for msg in engine.history:
        if msg["role"] == "user":
            conversation.append(f"**🧑 User:**\n{msg['content']}\n")
        elif msg["role"] == "assistant" and msg.get("content"):
            conversation.append(f"**🦞 Clawd:**\n{msg['content']}\n")

    if not conversation:
        await update.message.reply_text("Nothing to save — conversation is empty.")
        return

    content = "\n---\n\n".join(conversation)
    memory.save(name, content)
    await update.message.reply_text(f"✅ Saved as `{name}`", parse_mode=ParseMode.MARKDOWN)


async def cmd_load(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/load name`", parse_mode=ParseMode.MARKDOWN)
        return

    name = context.args[0]
    content = memory.load(name)

    if content is None:
        matches = memory.search(name)
        if matches:
            suggestions = "\n".join(f"• `{m['name']}`" for m in matches)
            await update.message.reply_text(f"❌ Not found.\n\nDid you mean:\n{suggestions}", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"❌ Note `{name}` not found.", parse_mode=ParseMode.MARKDOWN)
        return

    engine = get_engine(update.effective_user.id)
    engine.inject_context(content)
    await update.message.reply_text(f"✅ Loaded `{name}` into context", parse_mode=ParseMode.MARKDOWN)


async def cmd_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    notes = memory.list_notes()
    if not notes:
        await update.message.reply_text("No notes yet. Use `/save name`", parse_mode=ParseMode.MARKDOWN)
        return

    lines = ["🗂️ *Notes*\n"]
    for note in notes:
        size = f"{note['size']:,} B" if note["size"] < 1024 else f"{note['size'] / 1024:.1f} KB"
        lines.append(f"• `{note['name']}` — {note['modified']} ({size})")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/search keyword`", parse_mode=ParseMode.MARKDOWN)
        return

    keyword = " ".join(context.args)
    results = memory.search(keyword)

    if not results:
        await update.message.reply_text(f"No notes matching `{keyword}`", parse_mode=ParseMode.MARKDOWN)
        return

    lines = [f"🔍 Found {len(results)}:\n"]
    for r in results:
        lines.append(f"• `{r['name']}`")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/delete name`", parse_mode=ParseMode.MARKDOWN)
        return

    name = context.args[0]
    if memory.delete(name):
        await update.message.reply_text(f"✅ Deleted `{name}`", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"❌ Not found: `{name}`", parse_mode=ParseMode.MARKDOWN)


async def cmd_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = " ".join(context.args) if context.args else ""
    if not args:
        # List all tracked targets
        targets = target_memory.list_targets()
        if not targets:
            await update.message.reply_text("📭 No targets tracked yet.")
            return
        msg = "🎯 Tracked Targets:\n\n" + "\n".join(f"• {t}" for t in targets)
        await update.message.reply_text(msg)
        return

    summary = target_memory.get_summary(args.strip())
    if len(summary) > 4000:
        for i in range(0, len(summary), 4000):
            await update.message.reply_text(summary[i:i+4000])
    else:
        await update.message.reply_text(summary)


# ──────────────────────────────────────────────
# Message Handler — Agent Chat
# ──────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle regular text messages — chat with Clawd (with tool execution)."""
    user_input = update.message.text
    if not user_input:
        return

    user_id = update.effective_user.id
    engine = get_engine(user_id)

    # Clear tool logs for this turn
    user_tool_logs[user_id] = []

    # Show typing indicator
    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        # Run the blocking agent loop in a thread so we don't freeze the event loop
        response = await asyncio.to_thread(engine.chat, user_input)

        # Build the reply with tool execution log
        tool_log = user_tool_logs.get(user_id, [])

        if tool_log:
            # Show what tools were executed
            log_text = "\n".join(tool_log)
            full_reply = f"{log_text}\n\n{'─' * 30}\n\n{response}"
        else:
            full_reply = response

        # Send response (split if too long)
        if len(full_reply) <= 4096:
            await update.message.reply_text(full_reply)
        else:
            for i in range(0, len(full_reply), 4096):
                chunk = full_reply[i : i + 4096]
                await update.message.reply_text(chunk)

    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"⚠️ Error: {e}")


# ──────────────────────────────────────────────
# Error Handler
# ──────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception: {context.error}")


# ──────────────────────────────────────────────
# Bot Setup & Launch
# ──────────────────────────────────────────────

async def post_init(application: Application):
    commands = [
        BotCommand("start", "Start Clawd"),
        BotCommand("help", "Show commands"),
        BotCommand("clear", "Reset conversation"),
        BotCommand("save", "Save conversation"),
        BotCommand("load", "Load a note"),
        BotCommand("notes", "List notes"),
        BotCommand("search", "Search notes"),
        BotCommand("delete", "Delete a note"),
        BotCommand("target", "View target intel (3-bucket memory)"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands registered")


def main():
    print("🦞 Clawd Telegram Bot starting...")
    print(f"   Model: {config.MODEL_NAME}")
    print(f"   LM Studio: {config.LM_STUDIO_URL}")
    print(f"   Workspace: {config.WORKSPACE_DIR}")
    print(f"   Tools: run_command, write_file, read_file")
    print(f"   Press Ctrl+C to stop\n")

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("save", cmd_save))
    app.add_handler(CommandHandler("load", cmd_load))
    app.add_handler(CommandHandler("notes", cmd_notes))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("target", cmd_target))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.add_error_handler(error_handler)

    print("🦞 Bot is live! Send a message on Telegram.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
