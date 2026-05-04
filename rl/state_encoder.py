"""
Clawd 🦞 — State Encoder
Converts current Clawd context into structured features for the RL policy.
Produces both a human-readable state dict and a normalized tensor for the model.
"""

import torch

# ──────────────────────────────────────────────
# Common ports bitmap (top 20 most common CTF ports)
# ──────────────────────────────────────────────

COMMON_PORTS = [
    21, 22, 23, 25, 53, 80, 110, 111, 135, 139,
    443, 445, 993, 995, 1433, 1521, 3306, 3389, 5432, 8080,
]
_PORT_SET = set(COMMON_PORTS)

# Fixed feature vector width
# 20 (port bitmap) + 8 (service flags) + 15 (scalar features) + 15 (action index) = 58
STATE_DIM = 58


def _hash_target(target: str) -> float:
    """Produce a stable float in [0, 1] from target string."""
    h = hash(target) & 0xFFFFFFFF
    return h / 0xFFFFFFFF


def _port_bitmap(ports: list[int]) -> list[float]:
    """Binary bitmap for the 20 common ports."""
    port_set = set(ports)
    return [1.0 if p in port_set else 0.0 for p in COMMON_PORTS]


# Service flags we track
SERVICE_FLAGS = ["ssh", "http", "https", "ftp", "smb", "mysql", "rdp", "dns"]


def _service_flags(services: list[str]) -> list[float]:
    """Binary flags for known service types."""
    svc_lower = {s.lower() for s in services}
    return [1.0 if svc in svc_lower else 0.0 for svc in SERVICE_FLAGS]


class StateEncoder:
    """Encode Clawd's current context into a structured state representation."""

    def __init__(self, action_space=None):
        """
        Args:
            action_space: Optional ActionSpace instance for action indexing.
        """
        self._action_space = action_space

    def encode(
        self,
        target: str = "",
        memory_data: dict | None = None,
        notes_data: dict | None = None,
        history_summary: dict | None = None,
    ) -> dict:
        """
        Build a structured state dict from current Clawd context.

        Args:
            target: Active target IP/hostname.
            memory_data: Raw target memory dict (facts, failed, hypotheses).
            notes_data: Notes index summary (chunk count, tags).
            history_summary: Summary of current episode history
                             (steps, last_action, last_success, etc.)

        Returns:
            Structured state dict with all feature fields.
        """
        mem = memory_data or {}
        notes = notes_data or {}
        hist = history_summary or {}

        # Extract ports from facts
        ports = hist.get("known_ports", [])
        services = hist.get("known_services", [])

        facts = mem.get("facts", [])
        failed = mem.get("failed", [])
        hypotheses = mem.get("hypotheses", [])

        state = {
            # Target identity
            "target": target,

            # Discovery state
            "ports": ports,
            "services": services,
            "has_web": any(
                s in [svc.lower() for svc in services]
                for s in ("http", "https")
            ) or any(p in ports for p in (80, 443, 8080, 8443)),

            # Memory counts
            "facts_count": len(facts),
            "failed_count": len(failed),
            "hypotheses_count": len(hypotheses),

            # History / episode progress
            "last_action": hist.get("last_action", ""),
            "last_action_success": hist.get("last_action_success", False),
            "web_recon_ran": hist.get("web_recon_ran", False),
            "steps": hist.get("steps", 0),
            "timeouts": hist.get("timeouts", 0),
            "empty_outputs": hist.get("empty_outputs", 0),
            "repeated_failures": hist.get("repeated_failures", 0),

            # Notes state
            "notes_count": notes.get("total_chunks", 0),

            # Whether target memory exists at all
            "has_memory": len(facts) + len(failed) + len(hypotheses) > 0,
        }

        return state

    def to_tensor(self, state: dict) -> torch.Tensor:
        """
        Flatten a state dict into a fixed-size float tensor for the policy net.

        Returns:
            1-D tensor of shape (STATE_DIM,).
        """
        features = []

        # Target hash (1)
        features.append(_hash_target(state.get("target", "")))

        # Port bitmap (20)
        features.extend(_port_bitmap(state.get("ports", [])))

        # Service flags (8)
        features.extend(_service_flags(state.get("services", [])))

        # Boolean flags (3)
        features.append(1.0 if state.get("has_web", False) else 0.0)
        features.append(1.0 if state.get("web_recon_ran", False) else 0.0)
        features.append(1.0 if state.get("has_memory", False) else 0.0)

        # Counts — normalized with soft caps (12)
        features.append(min(state.get("facts_count", 0) / 20.0, 1.0))
        features.append(min(state.get("failed_count", 0) / 20.0, 1.0))
        features.append(min(state.get("hypotheses_count", 0) / 10.0, 1.0))
        features.append(min(state.get("notes_count", 0) / 30.0, 1.0))
        features.append(min(state.get("steps", 0) / 50.0, 1.0))
        features.append(min(state.get("timeouts", 0) / 5.0, 1.0))
        features.append(min(state.get("empty_outputs", 0) / 10.0, 1.0))
        features.append(min(state.get("repeated_failures", 0) / 10.0, 1.0))
        features.append(1.0 if state.get("last_action_success", False) else 0.0)

        # Last action as one-hot (action_space.size features, padded to 15)
        last_action = state.get("last_action", "")
        action_onehot = [0.0] * 15
        if self._action_space and last_action:
            try:
                idx = self._action_space.action_to_index(last_action)
                if idx < 15:
                    action_onehot[idx] = 1.0
            except ValueError:
                pass
        features.extend(action_onehot)

        # Pad or truncate to STATE_DIM
        if len(features) < STATE_DIM:
            features.extend([0.0] * (STATE_DIM - len(features)))
        features = features[:STATE_DIM]

        return torch.tensor(features, dtype=torch.float32)

    def encode_updated(
        self,
        prev_state: dict,
        action: str,
        result: dict,
        target: str = "",
        memory_data: dict | None = None,
        notes_data: dict | None = None,
    ) -> dict:
        """
        Build the next state after executing an action.
        Merges the previous state with new information from the result.

        Args:
            prev_state: The state before the action.
            action: The action that was taken.
            result: The result dict from executing the action.
            target: Active target (may be same as prev).
            memory_data: Updated memory data.
            notes_data: Updated notes data.

        Returns:
            Updated state dict.
        """
        mem = memory_data or {}
        notes = notes_data or {}

        # Start from previous state
        new_state = dict(prev_state)

        # Update target if changed
        if target:
            new_state["target"] = target

        # Update memory counts
        new_state["facts_count"] = len(mem.get("facts", []))
        new_state["failed_count"] = len(mem.get("failed", []))
        new_state["hypotheses_count"] = len(mem.get("hypotheses", []))
        new_state["has_memory"] = (
            new_state["facts_count"]
            + new_state["failed_count"]
            + new_state["hypotheses_count"]
        ) > 0

        # Update notes count
        new_state["notes_count"] = notes.get("total_chunks", prev_state.get("notes_count", 0))

        # Update history
        new_state["last_action"] = action
        new_state["steps"] = prev_state.get("steps", 0) + 1

        # Determine success from result
        success = True
        timed_out = result.get("timed_out", False)
        exit_code = result.get("exit_code")
        if timed_out:
            success = False
            new_state["timeouts"] = prev_state.get("timeouts", 0) + 1
        elif exit_code is not None and exit_code != 0:
            success = False

        stdout = result.get("stdout", "")
        if success and not stdout.strip():
            new_state["empty_outputs"] = prev_state.get("empty_outputs", 0) + 1

        new_state["last_action_success"] = success

        # Track web recon
        if action in ("web_recon", "read_webpage"):
            new_state["web_recon_ran"] = True

        # Try to extract new ports/services from results
        # (Lightweight heuristic — main parsing happens in the LLM)
        if action in ("run_nmap_basic", "run_nmap_service", "run_nmap_full"):
            new_ports = _extract_ports_from_output(stdout)
            if new_ports:
                existing = set(prev_state.get("ports", []))
                existing.update(new_ports)
                new_state["ports"] = sorted(existing)

            new_services = _extract_services_from_output(stdout)
            if new_services:
                existing_svc = set(prev_state.get("services", []))
                existing_svc.update(new_services)
                new_state["services"] = sorted(existing_svc)

            # Update has_web based on new data
            new_state["has_web"] = any(
                s in [svc.lower() for svc in new_state.get("services", [])]
                for s in ("http", "https")
            ) or any(
                p in new_state.get("ports", [])
                for p in (80, 443, 8080, 8443)
            )

        return new_state


def _extract_ports_from_output(output: str) -> list[int]:
    """Extract port numbers from nmap-like output."""
    import re
    ports = []
    for match in re.finditer(r"(\d{1,5})/tcp\s+open", output):
        try:
            ports.append(int(match.group(1)))
        except ValueError:
            pass
    return ports


def _extract_services_from_output(output: str) -> list[str]:
    """Extract service names from nmap-like output."""
    import re
    services = []
    for match in re.finditer(r"\d+/tcp\s+open\s+(\S+)", output):
        svc = match.group(1).lower()
        if svc and svc != "unknown":
            services.append(svc)
    return services
