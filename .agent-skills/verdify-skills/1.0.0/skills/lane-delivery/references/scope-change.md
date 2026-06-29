# Scope change

Pause before changing:

- public API or persisted schema;
- paths owned by another lane/module;
- architecture/security boundaries;
- deployment topology or privileged dependencies;
- acceptance intent or non-goals;
- baseline/dependency assumptions that invalidate another lane.

Record the proposed change, evidence, affected issues/contracts/lanes, alternatives, and risk. The orchestrator routes it to planning, architecture, or a human gate. Resume only from a versioned approved contract.
