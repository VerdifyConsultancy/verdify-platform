"""Cortex planner: one deterministic call to the local Qwen route per plan trigger (#1350, #1366).

- `inputs`   the raw rows a pack is built from (JSON round-trippable, so packs are reproducible)
- `sections` the 16 fixed sections, their order and Qwen-token budgets
- `tokens`   the token counter (real tokenizer when present, calibrated estimate otherwise)
- `pack`     pure section builders and the budgeted pack
- `client`   the Cortex chat-completions request and response parsing
- `run`      one run per trigger: retry, repair, fail closed, store; the shadow service loop
- `db`       the database reads and writes, through the verdify_planner_runtime duty only
- `dry`      offline render of a fixture pack (`make cortex-planner-dry`)

Shadow mode (the only mode this release supports) stores packs and proposals and sends nothing
to the device: it never writes plan_journal, setpoint_plan or setpoint_changes.
"""

PACK_VERSION = "pack-v1"
