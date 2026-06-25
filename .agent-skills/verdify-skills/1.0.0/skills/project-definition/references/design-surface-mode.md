# Design surface mode

A surface is any contract through which a human, agent, service, or operator interacts with the product.

For each surface record actor, goal, entry condition, inputs, outputs, happy path, alternate states, error states, permissions, evidence/provenance, and human approval points. Include inaccessible or forbidden actions where security depends on them.

Include operational and delivery surfaces when they are intentional parts of the system: deployment controls, configuration, health checks, logs/metrics/traces, admin/support queues, review queues, approval gates, import/export, migration, and rollback surfaces.

Do not design backend components here. Define the behavior architecture must support.
