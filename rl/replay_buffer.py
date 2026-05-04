"""
Clawd 🦞 — Replay Buffer
In-memory + disk-backed transition storage for policy training.
Stores (state, action, reward, next_state) tuples.
"""

import os
import json
import random


class ReplayBuffer:
    """
    Fixed-capacity replay buffer for RL training.
    Supports in-memory storage with disk persistence.
    """

    def __init__(self, capacity: int = 10000, buffer_dir: str = ""):
        """
        Args:
            capacity: Maximum number of transitions to store.
            buffer_dir: Directory for disk persistence.
        """
        self._capacity = capacity
        self._buffer: list[dict] = []
        self._position = 0  # Circular write position
        self._buffer_dir = buffer_dir

        if buffer_dir:
            os.makedirs(buffer_dir, exist_ok=True)

    @property
    def size(self) -> int:
        """Current number of transitions stored."""
        return len(self._buffer)

    @property
    def capacity(self) -> int:
        """Maximum capacity."""
        return self._capacity

    def add(self, transition: dict):
        """
        Add a transition to the buffer.

        Expected transition keys:
            state: dict — encoded state before action
            action: str — action name
            action_index: int — action index for model
            reward: float — reward received
            next_state: dict — encoded state after action
            done: bool — whether episode ended

        Args:
            transition: Transition dict to store.
        """
        if len(self._buffer) < self._capacity:
            self._buffer.append(transition)
        else:
            # Circular overwrite
            self._buffer[self._position] = transition

        self._position = (self._position + 1) % self._capacity

    def sample(self, batch_size: int) -> list[dict]:
        """
        Sample a random batch of transitions.

        Args:
            batch_size: Number of transitions to sample.

        Returns:
            List of transition dicts.

        Raises:
            ValueError: If batch_size > buffer size.
        """
        if batch_size > len(self._buffer):
            raise ValueError(
                f"Cannot sample {batch_size} from buffer of size {len(self._buffer)}"
            )
        return random.sample(self._buffer, batch_size)

    def get_all(self) -> list[dict]:
        """Return all transitions in the buffer."""
        return list(self._buffer)

    def clear(self):
        """Clear all transitions."""
        self._buffer.clear()
        self._position = 0

    def save(self, path: str = "") -> str:
        """
        Save buffer to disk as JSON.

        Args:
            path: File path. If empty, uses default in buffer_dir.

        Returns:
            Path where buffer was saved.
        """
        if not path:
            path = os.path.join(self._buffer_dir, "buffer.json")

        os.makedirs(os.path.dirname(path), exist_ok=True)

        data = {
            "capacity": self._capacity,
            "position": self._position,
            "transitions": self._buffer,
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str)

        return path

    def load(self, path: str = "") -> bool:
        """
        Load buffer from disk.

        Args:
            path: File path. If empty, tries default in buffer_dir.

        Returns:
            True if loaded successfully, False otherwise.
        """
        if not path:
            path = os.path.join(self._buffer_dir, "buffer.json")

        if not os.path.isfile(path):
            return False

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._capacity = data.get("capacity", self._capacity)
            self._position = data.get("position", 0)
            self._buffer = data.get("transitions", [])
            return True
        except (json.JSONDecodeError, OSError):
            return False

    def load_from_episodes(self, episodes: list[list[dict]]):
        """
        Populate the buffer from logged episodes.

        Args:
            episodes: List of episodes, each a list of step dicts.
                     Steps must have: state, selected_action, reward, next_state.
        """
        for episode in episodes:
            for i, step in enumerate(episode):
                transition = {
                    "state": step.get("state", {}),
                    "action": step.get("selected_action", ""),
                    "action_index": step.get("action_index", 0),
                    "reward": step.get("reward", 0.0),
                    "next_state": step.get("next_state", {}),
                    "done": i == len(episode) - 1,
                }
                self.add(transition)
