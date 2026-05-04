"""
Clawd 🦞 — Episode Logger
Logs full episode trajectories (state → action → reward → next_state)
as JSONL files for later supervised learning and RL training.
"""

import os
import json
from datetime import datetime


class EpisodeLogger:
    """
    Records agent episodes as JSON-lines files.
    Each episode = one target engagement session.
    Each step = one state → action → result → reward transition.
    """

    def __init__(self, episodes_dir: str):
        """
        Args:
            episodes_dir: Directory to store episode JSONL files.
        """
        self._episodes_dir = episodes_dir
        os.makedirs(episodes_dir, exist_ok=True)

        self._current_episode: list[dict] | None = None
        self._current_target: str = ""
        self._episode_start: str = ""
        self._episode_file: str = ""

    @property
    def is_recording(self) -> bool:
        """Whether an episode is currently being recorded."""
        return self._current_episode is not None

    def start_episode(self, target: str) -> str:
        """
        Begin recording a new episode.

        Args:
            target: The target IP/hostname for this episode.

        Returns:
            The episode file path.
        """
        # End any existing episode first
        if self._current_episode is not None:
            self.end_episode()

        self._current_target = target
        self._current_episode = []
        self._episode_start = datetime.now().isoformat()

        # Build filename: target_timestamp.jsonl
        safe_target = target.replace(".", "-").replace("/", "-").replace(":", "-")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._episode_file = os.path.join(
            self._episodes_dir, f"{safe_target}_{ts}.jsonl"
        )

        return self._episode_file

    def record_step(
        self,
        state: dict,
        candidate_actions: list[str],
        selected_action: str,
        result: dict,
        reward: float,
        next_state: dict,
        tool_args: dict | None = None,
        truth_gate_outcome: str = "",
    ) -> int:
        """
        Record a single transition step in the current episode.

        Args:
            state: Encoded state before the action.
            candidate_actions: All candidate actions considered.
            selected_action: The action that was chosen.
            result: Result dict from execution.
            reward: Computed reward for this step.
            next_state: State after the action.
            tool_args: Arguments passed to the tool.
            truth_gate_outcome: Truth gate result if any.

        Returns:
            Step number (0-indexed).
        """
        if self._current_episode is None:
            raise RuntimeError(
                "No episode in progress. Call start_episode() first."
            )

        # Build a compact result summary (avoid huge stdout in logs)
        result_summary = _compact_result(result)

        step = {
            "step": len(self._current_episode),
            "timestamp": datetime.now().isoformat(),
            "state": state,
            "candidate_actions": candidate_actions,
            "selected_action": selected_action,
            "tool_args": tool_args or {},
            "result": result_summary,
            "truth_gate": truth_gate_outcome,
            "reward": reward,
            "next_state": next_state,
        }

        self._current_episode.append(step)
        return step["step"]

    def end_episode(self) -> dict | None:
        """
        Finalize and write the current episode to disk.

        Returns:
            Episode summary dict, or None if no episode was in progress.
        """
        if self._current_episode is None:
            return None

        # Write all steps as JSONL
        with open(self._episode_file, "w", encoding="utf-8") as f:
            for step in self._current_episode:
                f.write(json.dumps(step, default=str) + "\n")

        summary = {
            "target": self._current_target,
            "file": self._episode_file,
            "start": self._episode_start,
            "end": datetime.now().isoformat(),
            "total_steps": len(self._current_episode),
            "total_reward": sum(s["reward"] for s in self._current_episode),
        }

        # Reset state
        self._current_episode = None
        self._current_target = ""
        self._episode_start = ""

        return summary

    def load_episodes(self, target: str | None = None) -> list[list[dict]]:
        """
        Load all stored episodes, optionally filtered by target.

        Args:
            target: If provided, only load episodes for this target.

        Returns:
            List of episodes, where each episode is a list of step dicts.
        """
        episodes = []
        safe_filter = ""
        if target:
            safe_filter = target.replace(".", "-").replace("/", "-").replace(":", "-")

        if not os.path.isdir(self._episodes_dir):
            return episodes

        for filename in sorted(os.listdir(self._episodes_dir)):
            if not filename.endswith(".jsonl"):
                continue
            if safe_filter and not filename.startswith(safe_filter):
                continue

            filepath = os.path.join(self._episodes_dir, filename)
            episode = []
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            episode.append(json.loads(line))
            except (json.JSONDecodeError, OSError):
                continue  # Skip corrupted files

            if episode:
                episodes.append(episode)

        return episodes

    def get_episode_count(self, target: str | None = None) -> int:
        """Count stored episodes, optionally filtered by target."""
        return len(self.load_episodes(target))


def _compact_result(result: dict) -> dict:
    """
    Create a compact version of a tool result for episode logging.
    Truncates long stdout/stderr to save disk space.
    """
    compact = {}
    for k, v in result.items():
        if k in ("stdout", "stderr", "content", "visible_text", "raw_output"):
            # Truncate long text fields
            if isinstance(v, str) and len(v) > 500:
                compact[k] = v[:250] + f"...[{len(v)} chars]..." + v[-250:]
            else:
                compact[k] = v
        elif k == "results" and isinstance(v, str) and len(v) > 500:
            compact[k] = v[:500] + f"...[truncated]"
        else:
            compact[k] = v

    # Add convenience flags
    compact["_success"] = (
        result.get("success", True)
        and result.get("exit_code", 0) == 0
        and not result.get("timed_out", False)
        and not result.get("error")
    )
    compact["_timed_out"] = result.get("timed_out", False)

    return compact
