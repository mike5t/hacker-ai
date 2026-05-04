"""
Clawd 🦞 — Reward Tracker
Computes shaped rewards based on progress, not just final success.
Uses state diffs to detect new discoveries, repeated failures, and wasted actions.
"""


class RewardTracker:
    """Compute shaped rewards from step outcomes."""

    # ── Reward weights ──────────────────────────
    REWARDS = {
        "new_port": 1.0,
        "new_service": 1.0,
        "new_web_path": 1.5,
        "confirmed_hypothesis": 2.0,
        "new_fact": 0.5,
        "new_note": 0.3,
        "successful_action": 0.1,
    }

    PENALTIES = {
        "repeated_failed_action": -2.0,
        "timeout": -1.0,
        "no_new_information": -0.5,
        "empty_output": -0.3,
        "scope_violation": -5.0,
        "invalid_action": -3.0,
        "duplicate_action": -1.5,
    }

    def compute(
        self,
        state: dict,
        action: str,
        result: dict,
        next_state: dict,
    ) -> float:
        """
        Compute the shaped reward for a single step.

        Args:
            state: State before the action.
            action: The action that was taken.
            result: Result dict from execution.
            next_state: State after the action.

        Returns:
            Float reward value.
        """
        reward = 0.0

        # ── Positive rewards ──

        # New ports discovered
        old_ports = set(state.get("ports", []))
        new_ports = set(next_state.get("ports", [])) - old_ports
        reward += len(new_ports) * self.REWARDS["new_port"]

        # New services discovered
        old_services = set(state.get("services", []))
        new_services = set(next_state.get("services", [])) - old_services
        reward += len(new_services) * self.REWARDS["new_service"]

        # New facts added
        old_facts = state.get("facts_count", 0)
        new_facts = next_state.get("facts_count", 0)
        if new_facts > old_facts:
            reward += (new_facts - old_facts) * self.REWARDS["new_fact"]

        # Hypothesis confirmed (facts went up AND hypotheses went down)
        old_hyp = state.get("hypotheses_count", 0)
        new_hyp = next_state.get("hypotheses_count", 0)
        if new_hyp < old_hyp and new_facts > old_facts:
            reward += self.REWARDS["confirmed_hypothesis"]

        # New notes stored
        old_notes = state.get("notes_count", 0)
        new_notes = next_state.get("notes_count", 0)
        if new_notes > old_notes:
            reward += self.REWARDS["new_note"]

        # Web discovery
        if not state.get("has_web", False) and next_state.get("has_web", False):
            reward += self.REWARDS["new_web_path"]

        # Basic success bonus
        if next_state.get("last_action_success", False):
            reward += self.REWARDS["successful_action"]

        # ── Negative rewards (penalties) ──

        # Timeout
        if result.get("timed_out", False):
            reward += self.PENALTIES["timeout"]

        # Empty output from action
        exit_code = result.get("exit_code")
        stdout = result.get("stdout", "")
        if (exit_code == 0 and not stdout.strip()
                and action not in ("log_fact", "log_failed", "log_hypothesis",
                                   "recall_target", "search_notes", "store_note")):
            reward += self.PENALTIES["empty_output"]

        # No new information at all (nothing changed in state)
        if (new_facts == old_facts
                and len(new_ports) == 0
                and len(new_services) == 0
                and new_notes == old_notes
                and new_hyp == old_hyp
                and not result.get("timed_out")
                and action not in ("recall_target", "search_notes")):
            reward += self.PENALTIES["no_new_information"]

        # Command failed (non-zero exit code, not a timeout)
        if (exit_code is not None and exit_code != 0
                and not result.get("timed_out")):
            # Check if this is a repeated failure
            old_failed = state.get("failed_count", 0)
            new_failed = next_state.get("failed_count", 0)
            if new_failed > old_failed:
                # The failure itself was logged — moderate penalty
                reward += self.PENALTIES["no_new_information"]
            else:
                # Unlogged failure — heavier penalty
                reward += self.PENALTIES["repeated_failed_action"]

        # Repeated failures tracked by state
        if next_state.get("repeated_failures", 0) > state.get("repeated_failures", 0):
            reward += self.PENALTIES["duplicate_action"]

        return round(reward, 3)

    def compute_episode_return(
        self, episode: list[dict], gamma: float = 0.99
    ) -> float:
        """
        Compute discounted return for a full episode.

        Args:
            episode: List of step dicts (each must have 'reward' key).
            gamma: Discount factor.

        Returns:
            Total discounted return.
        """
        total = 0.0
        discount = 1.0
        for step in episode:
            total += discount * step.get("reward", 0.0)
            discount *= gamma
        return round(total, 4)
