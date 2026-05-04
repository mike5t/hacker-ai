"""
Clawd 🦞 — RL Module Unit Tests
Tests all RL components in isolation with mock data.
No LLM calls, no network access, no WSL required.

Run:  python -m pytest test_rl.py -v
"""

import os
import sys
import json
import tempfile
import shutil
import pytest

# Make sure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ──────────────────────────────────────────────
# ActionSpace Tests
# ──────────────────────────────────────────────

class TestActionSpace:
    def setup_method(self):
        from rl.action_space import ActionSpace
        self.space = ActionSpace()

    def test_action_count(self):
        """Registry should have 15 actions."""
        assert self.space.size == 15

    def test_all_actions_returned(self):
        actions = self.space.get_all_actions()
        assert len(actions) == 15
        assert "run_nmap_basic" in actions
        assert "web_recon" in actions
        assert "log_fact" in actions

    def test_index_roundtrip(self):
        """action_to_index and index_to_action should be inverse."""
        for action in self.space.get_all_actions():
            idx = self.space.action_to_index(action)
            assert self.space.index_to_action(idx) == action

    def test_invalid_action_raises(self):
        with pytest.raises(ValueError):
            self.space.action_to_index("nonexistent_action")

    def test_expand_action_nmap(self):
        tool, args = self.space.expand_action("run_nmap_service", target="10.10.10.5")
        assert tool == "run_command"
        assert "10.10.10.5" in args["command"]
        assert "-sC" in args["command"] or "-sV" in args["command"]

    def test_expand_action_memory(self):
        tool, args = self.space.expand_action("recall_target", target="10.10.10.5")
        assert tool == "recall_target"
        assert args["target"] == "10.10.10.5"

    def test_is_valid_action(self):
        assert self.space.is_valid_action("run_nmap_basic") is True
        assert self.space.is_valid_action("fake_action") is False

    def test_categories(self):
        recon = self.space.get_actions_by_category("recon")
        assert "run_nmap_basic" in recon
        memory = self.space.get_actions_by_category("memory")
        assert "recall_target" in memory

    def test_metadata(self):
        meta = self.space.get_action_metadata("web_recon")
        assert meta is not None
        assert meta["tool"] == "web_recon"
        assert "description" in meta


# ──────────────────────────────────────────────
# StateEncoder Tests
# ──────────────────────────────────────────────

class TestStateEncoder:
    def setup_method(self):
        from rl.action_space import ActionSpace
        from rl.state_encoder import StateEncoder, STATE_DIM
        self.action_space = ActionSpace()
        self.encoder = StateEncoder(self.action_space)
        self.STATE_DIM = STATE_DIM

    def test_encode_empty(self):
        state = self.encoder.encode()
        assert isinstance(state, dict)
        assert state["target"] == ""
        assert state["facts_count"] == 0
        assert state["has_web"] is False

    def test_encode_with_data(self):
        mem = {
            "facts": [{"fact": "port 22 open"}],
            "failed": [],
            "hypotheses": [{"text": "ssh key auth"}],
        }
        state = self.encoder.encode(
            target="10.10.10.5",
            memory_data=mem,
            history_summary={"known_ports": [22, 80], "known_services": ["ssh", "http"]},
        )
        assert state["target"] == "10.10.10.5"
        assert state["facts_count"] == 1
        assert state["hypotheses_count"] == 1
        assert state["has_web"] is True
        assert 22 in state["ports"]
        assert 80 in state["ports"]

    def test_to_tensor_shape(self):
        state = self.encoder.encode()
        tensor = self.encoder.to_tensor(state)
        assert tensor.shape == (self.STATE_DIM,)
        assert tensor.dtype.is_floating_point

    def test_to_tensor_deterministic(self):
        state = self.encoder.encode(target="10.10.10.5")
        t1 = self.encoder.to_tensor(state)
        t2 = self.encoder.to_tensor(state)
        assert (t1 == t2).all()

    def test_encode_updated(self):
        state = self.encoder.encode(target="10.10.10.5")
        result = {"exit_code": 0, "stdout": "22/tcp open ssh\n80/tcp open http"}
        next_state = self.encoder.encode_updated(
            state, "run_nmap_service", result, target="10.10.10.5"
        )
        assert next_state["steps"] == 1
        assert next_state["last_action"] == "run_nmap_service"
        assert next_state["last_action_success"] is True


# ──────────────────────────────────────────────
# EpisodeLogger Tests
# ──────────────────────────────────────────────

class TestEpisodeLogger:
    def setup_method(self):
        from rl.episode_logger import EpisodeLogger
        self.tmpdir = tempfile.mkdtemp()
        self.logger = EpisodeLogger(self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_start_end_episode(self):
        assert not self.logger.is_recording
        self.logger.start_episode("10.10.10.5")
        assert self.logger.is_recording
        summary = self.logger.end_episode()
        assert summary is not None
        assert summary["target"] == "10.10.10.5"
        assert summary["total_steps"] == 0
        assert not self.logger.is_recording

    def test_record_step(self):
        self.logger.start_episode("10.10.10.5")
        step_num = self.logger.record_step(
            state={"target": "10.10.10.5"},
            candidate_actions=["run_nmap_basic", "web_recon"],
            selected_action="run_nmap_basic",
            result={"exit_code": 0, "stdout": "22/tcp open ssh"},
            reward=1.0,
            next_state={"target": "10.10.10.5", "ports": [22]},
        )
        assert step_num == 0
        summary = self.logger.end_episode()
        assert summary["total_steps"] == 1
        assert summary["total_reward"] == 1.0

    def test_file_written(self):
        self.logger.start_episode("10.10.10.5")
        self.logger.record_step(
            state={}, candidate_actions=["a"], selected_action="a",
            result={}, reward=0.5, next_state={},
        )
        self.logger.end_episode()
        files = os.listdir(self.tmpdir)
        assert len(files) == 1
        assert files[0].endswith(".jsonl")

    def test_load_episodes_roundtrip(self):
        self.logger.start_episode("10.10.10.5")
        self.logger.record_step(
            state={"target": "10.10.10.5"},
            candidate_actions=["run_nmap_basic"],
            selected_action="run_nmap_basic",
            result={"exit_code": 0},
            reward=1.5,
            next_state={"target": "10.10.10.5"},
        )
        self.logger.end_episode()

        episodes = self.logger.load_episodes()
        assert len(episodes) == 1
        assert len(episodes[0]) == 1
        assert episodes[0][0]["selected_action"] == "run_nmap_basic"
        assert episodes[0][0]["reward"] == 1.5

    def test_no_recording_raises(self):
        with pytest.raises(RuntimeError):
            self.logger.record_step(
                state={}, candidate_actions=[], selected_action="a",
                result={}, reward=0, next_state={},
            )


# ──────────────────────────────────────────────
# RewardTracker Tests
# ──────────────────────────────────────────────

class TestRewardTracker:
    def setup_method(self):
        from rl.reward_tracker import RewardTracker
        self.tracker = RewardTracker()

    def test_new_port_positive(self):
        state = {"ports": [], "services": [], "facts_count": 0,
                 "hypotheses_count": 0, "notes_count": 0}
        next_state = {"ports": [22, 80], "services": [], "facts_count": 0,
                      "hypotheses_count": 0, "notes_count": 0,
                      "last_action_success": True}
        result = {"exit_code": 0, "stdout": "data"}
        reward = self.tracker.compute(state, "run_nmap_basic", result, next_state)
        assert reward > 0, "New ports should give positive reward"

    def test_timeout_negative(self):
        state = {"ports": [], "services": [], "facts_count": 0,
                 "hypotheses_count": 0, "notes_count": 0}
        result = {"timed_out": True}
        next_state = dict(state)
        reward = self.tracker.compute(state, "run_nmap_basic", result, next_state)
        assert reward < 0, "Timeout should give negative reward"

    def test_no_new_info_penalty(self):
        state = {"ports": [22], "services": ["ssh"], "facts_count": 1,
                 "hypotheses_count": 0, "notes_count": 1}
        result = {"exit_code": 0, "stdout": "data"}
        next_state = dict(state)
        next_state["last_action_success"] = True
        reward = self.tracker.compute(state, "run_nmap_basic", result, next_state)
        # Should have no_new_info penalty
        assert reward < 1.0

    def test_episode_return(self):
        episode = [
            {"reward": 1.0},
            {"reward": 2.0},
            {"reward": -1.0},
        ]
        ret = self.tracker.compute_episode_return(episode, gamma=1.0)
        assert ret == 2.0


# ──────────────────────────────────────────────
# PolicyModel Tests
# ──────────────────────────────────────────────

class TestPolicyModel:
    def setup_method(self):
        from rl.action_space import ActionSpace
        from rl.policy_model import PolicyModel
        from rl.state_encoder import StateEncoder, STATE_DIM
        self.tmpdir = tempfile.mkdtemp()
        self.action_space = ActionSpace()
        self.model = PolicyModel(self.action_space, self.tmpdir)
        self.encoder = StateEncoder(self.action_space)
        self.STATE_DIM = STATE_DIM

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_forward_shape(self):
        import torch
        x = torch.randn(self.STATE_DIM)
        out = self.model.network(x)
        assert out.shape == (self.action_space.size,)

    def test_batch_forward(self):
        import torch
        x = torch.randn(4, self.STATE_DIM)
        out = self.model.network(x)
        assert out.shape == (4, self.action_space.size)

    def test_score_all(self):
        import torch
        state_t = torch.randn(self.STATE_DIM)
        scores = self.model.score(state_t)
        assert scores.shape == (self.action_space.size,)

    def test_score_candidates(self):
        import torch
        state_t = torch.randn(self.STATE_DIM)
        scores = self.model.score(state_t, candidate_indices=[0, 2, 5])
        assert scores.shape == (3,)

    def test_rank(self):
        state = self.encoder.encode(target="10.10.10.5")
        candidates = ["run_nmap_basic", "web_recon", "recall_target"]
        ranked = self.model.rank(state, candidates, self.encoder)
        assert len(ranked) == 3
        assert all(isinstance(r, tuple) and len(r) == 2 for r in ranked)

    def test_save_load_roundtrip(self):
        import torch
        path = self.model.save()
        assert os.path.isfile(path)

        # Create a new model and load
        from rl.policy_model import PolicyModel
        model2 = PolicyModel(self.action_space, self.tmpdir)
        assert model2.load(path) is True

        # Verify weights are same
        state_t = torch.randn(self.STATE_DIM)
        s1 = self.model.score(state_t)
        s2 = model2.score(state_t)
        assert torch.allclose(s1, s2)


# ──────────────────────────────────────────────
# ReplayBuffer Tests
# ──────────────────────────────────────────────

class TestReplayBuffer:
    def setup_method(self):
        from rl.replay_buffer import ReplayBuffer
        self.tmpdir = tempfile.mkdtemp()
        self.buffer = ReplayBuffer(capacity=100, buffer_dir=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_and_size(self):
        assert self.buffer.size == 0
        self.buffer.add({"state": {}, "action": "a", "reward": 1.0, "next_state": {}})
        assert self.buffer.size == 1

    def test_capacity_limit(self):
        buf = self._small_buffer(5)
        for i in range(10):
            buf.add({"state": {}, "action": f"a{i}", "reward": float(i), "next_state": {}})
        assert buf.size == 5  # Should not exceed capacity

    def test_sample(self):
        for i in range(10):
            self.buffer.add({"state": {}, "action": f"a{i}", "reward": float(i), "next_state": {}})
        batch = self.buffer.sample(5)
        assert len(batch) == 5

    def test_sample_too_large_raises(self):
        self.buffer.add({"state": {}, "action": "a", "reward": 1.0, "next_state": {}})
        with pytest.raises(ValueError):
            self.buffer.sample(10)

    def test_save_load_roundtrip(self):
        for i in range(5):
            self.buffer.add({"state": {"i": i}, "action": f"a{i}", "reward": float(i), "next_state": {}})
        path = self.buffer.save()
        assert os.path.isfile(path)

        from rl.replay_buffer import ReplayBuffer
        buf2 = ReplayBuffer(buffer_dir=self.tmpdir)
        assert buf2.load(path) is True
        assert buf2.size == 5

    def _small_buffer(self, cap):
        from rl.replay_buffer import ReplayBuffer
        return ReplayBuffer(capacity=cap, buffer_dir=self.tmpdir)


# ──────────────────────────────────────────────
# Integration Smoke Test
# ──────────────────────────────────────────────

class TestRLIntegration:
    """Test that all RL components work together."""

    def test_full_pipeline(self):
        """Simulate a mini episode through the full RL pipeline."""
        from rl.action_space import ActionSpace
        from rl.state_encoder import StateEncoder
        from rl.episode_logger import EpisodeLogger
        from rl.reward_tracker import RewardTracker
        from rl.policy_model import PolicyModel
        from rl.replay_buffer import ReplayBuffer

        tmpdir = tempfile.mkdtemp()
        try:
            action_space = ActionSpace()
            encoder = StateEncoder(action_space)
            logger = EpisodeLogger(os.path.join(tmpdir, "episodes"))
            tracker = RewardTracker()
            model = PolicyModel(action_space, os.path.join(tmpdir, "models"))
            buffer = ReplayBuffer(capacity=100, buffer_dir=os.path.join(tmpdir, "buffer"))

            # 1. Start episode
            logger.start_episode("10.10.10.5")

            # 2. Encode initial state
            state = encoder.encode(
                target="10.10.10.5",
                memory_data={"facts": [], "failed": [], "hypotheses": []},
            )

            # 3. Get candidate actions and rank
            candidates = action_space.get_all_actions()
            ranked = model.rank(state, candidates, encoder)
            assert len(ranked) > 0

            # 4. Simulate tool execution
            selected = ranked[0][0]
            result = {"exit_code": 0, "stdout": "22/tcp open ssh\n80/tcp open http"}

            # 5. Build next state
            next_state = encoder.encode_updated(
                state, selected, result, target="10.10.10.5"
            )

            # 6. Compute reward
            reward = tracker.compute(state, selected, result, next_state)
            assert isinstance(reward, float)

            # 7. Record transition
            logger.record_step(
                state=state, candidate_actions=candidates,
                selected_action=selected, result=result,
                reward=reward, next_state=next_state,
            )

            # 8. Add to buffer
            buffer.add({
                "state": state, "action": selected,
                "action_index": action_space.action_to_index(selected),
                "reward": reward, "next_state": next_state, "done": False,
            })

            # 9. End episode
            summary = logger.end_episode()
            assert summary["total_steps"] == 1

            # 10. Load back and verify
            episodes = logger.load_episodes()
            assert len(episodes) == 1

            # 11. Save/load model
            model.save()
            assert model.load()

            # 12. Save/load buffer
            buffer.save()
            assert buffer.load()
            assert buffer.size == 1

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
