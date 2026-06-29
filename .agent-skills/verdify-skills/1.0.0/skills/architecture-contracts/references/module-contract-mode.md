# Module contract mode

A strong module contract permits an independent implementation without permitting interface invention.

Inputs and outputs should name schemas and producers/consumers. Public interfaces should define compatibility expectations. Invariants should describe what must always remain true. Validation commands must be runnable, and contract tests should allow upstream/downstream work against fixtures or fakes.

Owned paths are exclusive by default. Shared paths require a named coordination rule or a dedicated shared module.
