# Cross-contract review

Reject or revise contracts when:

- two modules own the same mutable path or data;
- an output has no consumer or an input has no producer;
- dependency cycles lack an explicit strategy;
- public schemas are undefined;
- error handling or idempotency is ambiguous at a boundary;
- tests require implementation internals from another module;
- runtime resources cannot be isolated by lane;
- a module is merely a layer name without coherent responsibility.
