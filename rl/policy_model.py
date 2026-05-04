"""
Clawd 🦞 — Policy Model
PyTorch neural network for action scoring.
Starts as a supervised ranker, upgradeable to full RL policy/value model.
"""

import os
import torch
import torch.nn as nn
import torch.nn.functional as F

from .state_encoder import STATE_DIM


class PolicyNetwork(nn.Module):
    """
    MLP policy network: state → action scores.

    Architecture: state_dim → 128 → 64 → action_dim
    """

    def __init__(self, state_dim: int = STATE_DIM, action_dim: int = 15,
                 dropout: float = 0.2):
        super().__init__()
        self.fc1 = nn.Linear(state_dim, 128)
        self.fc2 = nn.Linear(128, 64)
        self.fc3 = nn.Linear(64, action_dim)
        self.dropout = nn.Dropout(dropout)

        # Initialize weights with small values for stable start
        nn.init.xavier_uniform_(self.fc1.weight)
        nn.init.xavier_uniform_(self.fc2.weight)
        nn.init.xavier_uniform_(self.fc3.weight)
        nn.init.zeros_(self.fc1.bias)
        nn.init.zeros_(self.fc2.bias)
        nn.init.zeros_(self.fc3.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass.

        Args:
            x: State tensor of shape (batch, state_dim) or (state_dim,).

        Returns:
            Action scores of shape (batch, action_dim) or (action_dim,).
        """
        x = F.relu(self.fc1(x))
        x = self.dropout(x)
        x = F.relu(self.fc2(x))
        x = self.dropout(x)
        x = self.fc3(x)
        return x


class PolicyModel:
    """
    High-level wrapper around PolicyNetwork.
    Handles scoring, ranking, saving, and loading.
    """

    def __init__(self, action_space=None, models_dir: str = ""):
        """
        Args:
            action_space: ActionSpace instance for name↔index conversion.
            models_dir: Directory for saving/loading model checkpoints.
        """
        self._action_space = action_space
        self._models_dir = models_dir

        action_dim = action_space.size if action_space else 15
        self._network = PolicyNetwork(
            state_dim=STATE_DIM, action_dim=action_dim
        )
        self._network.eval()  # Start in eval mode (no training yet)

        if models_dir:
            os.makedirs(models_dir, exist_ok=True)

    @property
    def network(self) -> PolicyNetwork:
        """Access the underlying PyTorch module."""
        return self._network

    def score(
        self,
        state_tensor: torch.Tensor,
        candidate_indices: list[int] | None = None,
    ) -> torch.Tensor:
        """
        Score actions given a state tensor.

        Args:
            state_tensor: State vector of shape (state_dim,).
            candidate_indices: Optional list of action indices to score.
                              If None, scores all actions.

        Returns:
            Scores tensor. If candidate_indices is provided, shape is
            (len(candidates),); otherwise (action_dim,).
        """
        with torch.no_grad():
            all_scores = self._network(state_tensor)

        if candidate_indices is not None:
            idx = torch.tensor(candidate_indices, dtype=torch.long)
            return all_scores[idx]

        return all_scores

    def rank(
        self,
        state_dict: dict,
        candidate_actions: list[str],
        state_encoder=None,
    ) -> list[tuple[str, float]]:
        """
        Rank candidate actions by predicted score.

        Args:
            state_dict: Structured state dict from StateEncoder.
            candidate_actions: List of action names to rank.
            state_encoder: StateEncoder instance for tensor conversion.

        Returns:
            List of (action_name, score) sorted by score descending.
        """
        if not candidate_actions:
            return []

        if state_encoder is None:
            # Import here to avoid circular dependency
            from .state_encoder import StateEncoder
            state_encoder = StateEncoder(self._action_space)

        state_tensor = state_encoder.to_tensor(state_dict)

        # Get indices for candidate actions
        candidate_indices = []
        valid_actions = []
        for action in candidate_actions:
            if self._action_space and self._action_space.is_valid_action(action):
                candidate_indices.append(
                    self._action_space.action_to_index(action)
                )
                valid_actions.append(action)

        if not candidate_indices:
            # If no valid actions, return uniform scores
            return [(a, 0.0) for a in candidate_actions]

        scores = self.score(state_tensor, candidate_indices)

        # Pair actions with scores and sort descending
        ranked = [
            (action, score.item())
            for action, score in zip(valid_actions, scores)
        ]
        ranked.sort(key=lambda x: x[1], reverse=True)

        return ranked

    def save(self, path: str = "") -> str:
        """
        Save model checkpoint to disk.

        Args:
            path: Full file path. If empty, uses default in models_dir.

        Returns:
            Path where model was saved.
        """
        if not path:
            path = os.path.join(self._models_dir, "policy_latest.pt")

        os.makedirs(os.path.dirname(path), exist_ok=True)
        torch.save({
            "model_state_dict": self._network.state_dict(),
            "state_dim": STATE_DIM,
            "action_dim": self._network.fc3.out_features,
        }, path)

        return path

    def load(self, path: str = "") -> bool:
        """
        Load model checkpoint from disk.

        Args:
            path: Full file path. If empty, tries default in models_dir.

        Returns:
            True if loaded successfully, False otherwise.
        """
        if not path:
            path = os.path.join(self._models_dir, "policy_latest.pt")

        if not os.path.isfile(path):
            return False

        try:
            checkpoint = torch.load(path, map_location="cpu", weights_only=True)
            self._network.load_state_dict(checkpoint["model_state_dict"])
            self._network.eval()
            return True
        except Exception:
            return False
