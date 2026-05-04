# RL Integration Plan for Clawd

## Goal
Add reinforcement-learning readiness to Clawd **without replacing the current architecture**.

Clawd must continue using:
- the **LLM as planner/reasoner**
- the existing **memory systems**
- the existing **executor/tool layer**
- the existing **truth gates / verifier / scope controls**

The new RL-related layer must be used only for **action selection and policy improvement**.

---

## Main Rule

Do **not** make reinforcement learning control the whole agent from the start.

Instead:

1. Keep the **LLM** as the main planner.
2. Add an **RL-ready policy layer** that observes the current state.
3. Let the policy layer score or rank possible next actions.
4. Keep the final execution under existing guardrails and scope checks.
5. Train gradually from logged runs.

---

## What Clawd Should Do Now

### Phase 1: Build RL-ready infrastructure
Implement the following new modules:

- `state_encoder.py`
- `episode_logger.py`
- `reward_tracker.py`
- `policy_model.py`
- `action_space.py`
- `replay_buffer.py` (or dataset storage)
- `training_loop.py`

At this phase, the agent should **not rely on RL for control**.
The LLM + current heuristics still make the final decisions.

---

## Architecture Change

### Current behavior
Clawd currently works like this:

User -> Engine -> LLM -> Tools -> Memory -> Verifier -> Response

### New behavior
Change it to:

User -> Engine -> LLM -> Candidate Actions -> Policy Scorer -> Guardrails -> Executor -> Result Parser -> Memory Update -> Episode Logger -> Response

### Important
The **policy scorer** does not replace the LLM.
It only helps decide which allowed next action is best.

---

## Responsibilities of Each Layer

### 1. LLM layer
The LLM must:
- read current target state
- interpret memory
- understand tool output
- propose candidate next steps
- explain reasoning
- summarize findings

### 2. Policy layer
The policy layer must:
- take the encoded state
- score candidate actions
- rank actions by usefulness
- learn from previous runs over time

### 3. Guardrail layer
The guardrail layer must:
- enforce allowed scope
- block unsafe or repeated actions
- preserve truth-gating behavior
- prevent retry loops
- ensure only approved tools/actions are executed

### 4. Memory layer
Memory must remain separate from RL.
The RL system must **read from memory**, but memory must not be replaced by RL.

---

## State Definition

Create a structured state object for each step of an episode.

Example state fields:

- active target IP/host
- whether target memory exists
- discovered open ports
- identified services and versions
- whether HTTP/HTTPS exists
- known paths/endpoints
- known forms
- known credentials/usernames
- facts count
- failed attempts count
- hypotheses count
- notes tags
- previous action
- previous action success/failure
- number of steps used
- number of repeated failures
- whether loot/files were found
- whether web recon already ran
- whether command timed out
- whether command returned empty output

### Output format
Implement a normalized state dict, for example:

```python
state = {
    "target": "10.10.10.5",
    "ports": [22, 80, 443],
    "services": ["ssh", "http", "https"],
    "has_web": True,
    "facts_count": 8,
    "failed_count": 2,
    "hypotheses_count": 3,
    "last_action": "run_command:nmap -sV",
    "last_action_success": True,
    "web_recon_ran": False,
    "steps": 4,
    "timeouts": 0,
    "empty_outputs": 0
}
```

---

## Action Space

Do **not** let RL choose arbitrary shell strings at first.

Instead, define a **bounded action space** at the tool/tactic level.

Example action types:

* `recall_target`
* `search_notes`
* `run_nmap_basic`
* `run_nmap_service`
* `run_dir_enum`
* `read_webpage`
* `web_recon`
* `download_file`
* `read_file`
* `analyze_pcap`
* `log_fact`
* `log_failed`
* `log_hypothesis`

### Rule

Actions must be high-level and controlled.
The executor can later translate them into real commands.

Example:

* policy action: `run_nmap_service`
* executor expansion: `nmap -sC -sV <target>`

This is safer and easier to train than raw shell generation.

---

## Candidate Action Flow

At each turn:

1. LLM reads the state and memory.
2. LLM proposes a small set of candidate actions.
3. Policy model scores those actions.
4. Guardrails filter invalid actions.
5. Best allowed action is executed.
6. Result is parsed and logged.
7. State is updated.
8. Episode data is stored.

Pseudo-flow:

```python
state = build_state()
candidate_actions = llm_propose_actions(state, memory, history)
scored_actions = policy_model.score(state, candidate_actions)
filtered_actions = guardrails.filter(scored_actions)
selected_action = choose_best(filtered_actions)
result = executor.run(selected_action)
reward = reward_tracker.compute(state, selected_action, result)
episode_logger.record(state, selected_action, result, reward)
update_memory(result)
```

---

## Reward Design

Implement rewards based on **progress**, not just final success.

### Positive reward examples

* discovering a new open port
* identifying a service/version
* finding a valid web endpoint
* finding a form
* discovering useful files
* confirming a hypothesis
* storing a valuable fact
* making progress without repeating work

### Negative reward examples

* repeating a known failed action
* timing out repeatedly
* empty output from unhelpful action
* calling irrelevant tools
* wasting steps
* producing duplicate recon with no new information

### Strong penalties

* violating scope
* trying blocked actions
* retrying the same failed command in the same context
* unsupported or invalid action format

### Example reward logic

```python
reward = 0.0

if found_new_port:
    reward += 1.0
if found_service_version:
    reward += 1.0
if discovered_new_web_path:
    reward += 1.5
if confirmed_hypothesis:
    reward += 2.0
if repeated_failed_action:
    reward -= 2.0
if timed_out:
    reward -= 1.0
if no_new_information:
    reward -= 0.5
```

---

## Episode Logging

Every run must be stored as an episode.

Each episode step should contain:

* encoded state
* candidate actions
* selected action
* tool arguments used
* result summary
* truth-gate outcome
* success/failure
* reward
* next state

### Example step format

```json
{
  "state": {...},
  "candidate_actions": ["run_nmap_service", "web_recon", "search_notes"],
  "selected_action": "run_nmap_service",
  "result": {
    "success": true,
    "timed_out": false,
    "new_ports": [22, 80]
  },
  "reward": 2.0,
  "next_state": {...}
}
```

Store this in a format that can be reused for:

* supervised learning
* offline RL
* replay buffer training
* evaluation

---

## Training Strategy

### Stage A: Logging only

At first:

* use LLM + heuristics for control
* log every step
* collect trajectories
* do not trust the policy model yet

### Stage B: Supervised action ranking

Train `policy_model.py` on successful trajectories:

* input: state + candidate actions
* output: best next action

This is easier and more stable than full RL at the start.

### Stage C: RL fine-tuning

Once enough data exists:

* use PPO, DQN, or another suitable method
* optimize policy over repeated episodes
* compare policy choices against heuristic baseline

### Rule

Do not switch to full RL-first control until the policy beats the heuristic/LLM baseline consistently.

---

## Integration Rules for Clawd

### Rule 1

Keep:

* truth gates
* fabrication detector
* verifier pass
* deduplication
* retry prevention
* scope enforcement

RL must work **inside** these controls, not around them.

### Rule 2

The LLM still handles:

* reasoning
* explanation
* summarization
* interpreting tool output

### Rule 3

The policy model only affects:

* action ranking
* action prioritization
* long-term improvement from episodes

### Rule 4

Memory remains separate:

* `target_memory.py`
* `notes_index.py`
* conversation memory

The policy model may consume memory-derived state features but must not replace memory storage.

---

## Implemented Modules

### `rl/action_space.py`

* defines 15 allowed high-level actions
* maps actions to executor tool calls
* index ↔ name conversion for model I/O
* expand_action() for executor dispatch

### `rl/state_encoder.py`

* converts context to structured state dict
* normalizes to fixed-dim tensor (STATE_DIM = 58)
* port bitmap, service flags, memory counts, history features

### `rl/episode_logger.py`

* saves state/action/result/reward transitions as JSONL
* start_episode / record_step / end_episode lifecycle
* load_episodes for training

### `rl/reward_tracker.py`

* shaped reward from state diffs
* positive: new ports, services, facts, hypotheses confirmed
* negative: timeouts, empty output, repeated failures

### `rl/policy_model.py`

* PyTorch MLP (128 → 64 → action_dim) with dropout
* PolicyModel wrapper: score, rank, save/load
* Xavier initialization (uniform scorer until trained)

### `rl/replay_buffer.py`

* fixed-capacity circular buffer
* JSON disk persistence
* load_from_episodes helper

### `rl/training_loop.py`

* supervised training on reward-weighted cross-entropy
* DQN-style Q-learning placeholder
* standalone CLI: `python -m rl.training_loop`

---

## Suggested Implementation Order

1. ✅ Build `action_space.py`
2. ✅ Build `state_encoder.py`
3. ✅ Build `episode_logger.py`
4. ✅ Build `reward_tracker.py`
5. ✅ Build PyTorch `policy_model.py`
6. ✅ Add policy scoring into `engine.py`
7. ✅ Keep LLM + heuristics in charge initially
8. Train on collected episodes (Stage B)
9. Only later enable stronger policy control (Stage C)

---

## Final Instruction

Implement RL in Clawd as a **learning action-selection layer**.

Do not:

* replace the LLM
* replace memory
* remove guardrails
* allow unrestricted policy-generated shell commands
* expect useful RL performance before enough episodes are logged

Do:

* build the RL scaffolding now ✅
* collect episodes from real runs ✅
* start with supervised action ranking
* move to RL gradually
* keep all current safety and anti-hallucination mechanisms ✅
