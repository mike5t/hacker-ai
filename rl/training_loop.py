"""
Clawd 🦞 — Training Loop
Offline training from logged episodes and replay buffer.
Supports supervised action ranking (Stage B) and placeholder for RL (Stage C).

Usage (standalone):
    python -m rl.training_loop --episodes-dir memory/rl_episodes --models-dir memory/rl_models
"""

import os
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader


class EpisodeDataset(Dataset):
    """PyTorch dataset from logged episode transitions."""

    def __init__(self, transitions: list[dict], state_encoder, action_space):
        """
        Args:
            transitions: List of transition dicts with state, action, reward.
            state_encoder: StateEncoder instance for tensor conversion.
            action_space: ActionSpace instance for action indexing.
        """
        self._data = []
        for t in transitions:
            state = t.get("state", {})
            action = t.get("selected_action", t.get("action", ""))
            reward = t.get("reward", 0.0)

            if not action or not action_space.is_valid_action(action):
                continue

            state_tensor = state_encoder.to_tensor(state)
            action_idx = action_space.action_to_index(action)

            self._data.append((state_tensor, action_idx, reward))

    def __len__(self):
        return len(self._data)

    def __getitem__(self, idx):
        state, action, reward = self._data[idx]
        return state, torch.tensor(action, dtype=torch.long), torch.tensor(reward, dtype=torch.float32)


def train_supervised(
    episodes: list[list[dict]],
    policy_model,
    state_encoder,
    action_space,
    epochs: int = 50,
    lr: float = 1e-3,
    batch_size: int = 32,
    min_reward: float = 0.0,
    verbose: bool = True,
) -> dict:
    """
    Stage B: Supervised training on successful trajectories.
    Trains the policy to predict the best action given a state,
    using only steps that achieved positive reward.

    Args:
        episodes: List of episodes (each a list of step dicts).
        policy_model: PolicyModel instance to train.
        state_encoder: StateEncoder instance.
        action_space: ActionSpace instance.
        epochs: Number of training epochs.
        lr: Learning rate.
        batch_size: Training batch size.
        min_reward: Only train on steps with reward >= this threshold.
        verbose: Print progress.

    Returns:
        Training metrics dict (losses, accuracy).
    """
    # Flatten episodes to transitions, filter by reward
    transitions = []
    for episode in episodes:
        for step in episode:
            if step.get("reward", 0.0) >= min_reward:
                transitions.append(step)

    if not transitions:
        return {"error": "No qualifying transitions found", "count": 0}

    dataset = EpisodeDataset(transitions, state_encoder, action_space)
    if len(dataset) == 0:
        return {"error": "No valid transitions after encoding", "count": 0}

    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    # Switch to training mode
    network = policy_model.network
    network.train()

    optimizer = optim.Adam(network.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()

    metrics = {"losses": [], "accuracies": [], "total_samples": len(dataset)}

    for epoch in range(epochs):
        total_loss = 0.0
        correct = 0
        total = 0

        for states, actions, rewards in loader:
            optimizer.zero_grad()

            # Forward pass
            scores = network(states)
            loss = criterion(scores, actions)

            # Weight loss by reward magnitude (reward-weighted supervised)
            reward_weights = torch.clamp(rewards, min=0.1)
            weighted_loss = (loss * reward_weights).mean()

            weighted_loss.backward()
            optimizer.step()

            total_loss += weighted_loss.item() * len(states)

            # Accuracy
            predicted = scores.argmax(dim=1)
            correct += (predicted == actions).sum().item()
            total += len(states)

        avg_loss = total_loss / total if total > 0 else 0.0
        accuracy = correct / total if total > 0 else 0.0

        metrics["losses"].append(round(avg_loss, 6))
        metrics["accuracies"].append(round(accuracy, 4))

        if verbose and (epoch + 1) % 10 == 0:
            print(
                f"  Epoch {epoch+1}/{epochs} — "
                f"loss: {avg_loss:.4f}, acc: {accuracy:.2%}"
            )

    # Switch back to eval mode
    network.eval()

    metrics["final_loss"] = metrics["losses"][-1] if metrics["losses"] else 0.0
    metrics["final_accuracy"] = metrics["accuracies"][-1] if metrics["accuracies"] else 0.0

    return metrics


def train_from_buffer(
    buffer,
    policy_model,
    state_encoder,
    action_space,
    epochs: int = 50,
    lr: float = 1e-3,
    batch_size: int = 32,
    gamma: float = 0.99,
    verbose: bool = True,
) -> dict:
    """
    Stage C placeholder: RL training from replay buffer.
    Currently implements simple DQN-style Q-learning.

    Args:
        buffer: ReplayBuffer instance with stored transitions.
        policy_model: PolicyModel instance to train.
        state_encoder: StateEncoder instance.
        action_space: ActionSpace instance.
        epochs: Number of training epochs.
        lr: Learning rate.
        batch_size: Training batch size.
        gamma: Discount factor.
        verbose: Print progress.

    Returns:
        Training metrics dict.
    """
    if buffer.size < batch_size:
        return {
            "error": f"Buffer too small ({buffer.size} < {batch_size})",
            "count": buffer.size,
        }

    network = policy_model.network
    network.train()

    optimizer = optim.Adam(network.parameters(), lr=lr)
    criterion = nn.MSELoss()

    metrics = {"losses": [], "total_samples": buffer.size}

    for epoch in range(epochs):
        batch = buffer.sample(batch_size)

        # Build tensors from batch
        states = torch.stack([
            state_encoder.to_tensor(t["state"]) for t in batch
        ])
        actions = torch.tensor(
            [t.get("action_index", 0) for t in batch], dtype=torch.long
        )
        rewards = torch.tensor(
            [t.get("reward", 0.0) for t in batch], dtype=torch.float32
        )
        next_states = torch.stack([
            state_encoder.to_tensor(t.get("next_state", {})) for t in batch
        ])
        dones = torch.tensor(
            [1.0 if t.get("done", False) else 0.0 for t in batch],
            dtype=torch.float32,
        )

        # Current Q-values for taken actions
        q_values = network(states)
        q_taken = q_values.gather(1, actions.unsqueeze(1)).squeeze(1)

        # Target Q-values (simple DQN, no target network yet)
        with torch.no_grad():
            next_q = network(next_states)
            next_q_max = next_q.max(dim=1).values
            targets = rewards + gamma * next_q_max * (1 - dones)

        loss = criterion(q_taken, targets)

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        metrics["losses"].append(round(loss.item(), 6))

        if verbose and (epoch + 1) % 10 == 0:
            print(f"  Epoch {epoch+1}/{epochs} — loss: {loss.item():.4f}")

    network.eval()
    metrics["final_loss"] = metrics["losses"][-1] if metrics["losses"] else 0.0

    return metrics


# ──────────────────────────────────────────────
# Standalone CLI entry point
# ──────────────────────────────────────────────

def main():
    """CLI entry point for offline training."""
    import argparse

    parser = argparse.ArgumentParser(description="Clawd RL Training Loop")
    parser.add_argument(
        "--episodes-dir", default="memory/rl_episodes",
        help="Directory containing episode JSONL files",
    )
    parser.add_argument(
        "--models-dir", default="memory/rl_models",
        help="Directory to save trained model",
    )
    parser.add_argument(
        "--epochs", type=int, default=50,
        help="Number of training epochs",
    )
    parser.add_argument(
        "--lr", type=float, default=1e-3,
        help="Learning rate",
    )
    parser.add_argument(
        "--batch-size", type=int, default=32,
        help="Training batch size",
    )
    parser.add_argument(
        "--min-reward", type=float, default=0.0,
        help="Minimum reward threshold for supervised training",
    )
    parser.add_argument(
        "--mode", choices=["supervised", "dqn"], default="supervised",
        help="Training mode",
    )
    args = parser.parse_args()

    from .action_space import ActionSpace
    from .state_encoder import StateEncoder
    from .episode_logger import EpisodeLogger
    from .policy_model import PolicyModel
    from .replay_buffer import ReplayBuffer

    print("🦞 Clawd RL Training Loop")
    print(f"   Mode: {args.mode}")
    print(f"   Episodes: {args.episodes_dir}")
    print(f"   Models: {args.models_dir}")
    print()

    action_space = ActionSpace()
    state_encoder = StateEncoder(action_space)
    episode_logger = EpisodeLogger(args.episodes_dir)
    policy_model = PolicyModel(action_space, args.models_dir)

    # Try to load existing model
    if policy_model.load():
        print("   ✅ Loaded existing model checkpoint")
    else:
        print("   🆕 Starting from scratch")

    # Load episodes
    episodes = episode_logger.load_episodes()
    print(f"   📊 Found {len(episodes)} episodes")

    if not episodes:
        print("   ⚠️  No episodes found. Run Clawd first to collect data.")
        return

    total_steps = sum(len(ep) for ep in episodes)
    print(f"   📈 Total steps: {total_steps}")
    print()

    if args.mode == "supervised":
        print("Training (supervised)...")
        metrics = train_supervised(
            episodes=episodes,
            policy_model=policy_model,
            state_encoder=state_encoder,
            action_space=action_space,
            epochs=args.epochs,
            lr=args.lr,
            batch_size=min(args.batch_size, total_steps),
            min_reward=args.min_reward,
        )
    else:
        # DQN mode — load into replay buffer first
        buffer = ReplayBuffer(capacity=total_steps * 2, buffer_dir=args.models_dir)
        buffer.load_from_episodes(episodes)
        print(f"   Buffer loaded: {buffer.size} transitions")
        print("Training (DQN)...")
        metrics = train_from_buffer(
            buffer=buffer,
            policy_model=policy_model,
            state_encoder=state_encoder,
            action_space=action_space,
            epochs=args.epochs,
            lr=args.lr,
            batch_size=min(args.batch_size, buffer.size),
        )

    print()
    if "error" in metrics:
        print(f"   ⚠️  {metrics['error']}")
    else:
        print(f"   ✅ Final loss: {metrics.get('final_loss', 'N/A')}")
        if "final_accuracy" in metrics:
            print(f"   ✅ Final accuracy: {metrics['final_accuracy']:.2%}")

    # Save model
    save_path = policy_model.save()
    print(f"   💾 Model saved to {save_path}")


if __name__ == "__main__":
    main()
