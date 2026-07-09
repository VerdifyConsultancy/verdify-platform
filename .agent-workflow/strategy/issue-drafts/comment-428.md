July 9 recovery scope treats chronic heap exhaustion as an OTA blocker, not merely a watch item. The proposed control-neutral fix must address allocation pressure before adding the combined irrigation/DLI/cycling firmware changes.

Acceptance:

- measure entity/API publish and allocation pressure by category on the current binary;
- remove or coalesce high-frequency diagnostic/text publication and other avoidable churn without hiding safety signals;
- add loop-duration, minimum-free-heap, largest-block, and reset-reason evidence sufficient to identify a future WDT;
- use a controlled restart only as a last-resort bounded safety net, never as the primary memory-management strategy;
- establish a conservative pre-OTA heap floor and soak criterion from observed healthy operation;
- prove firmware tests/invariants/replay/check plus a runtime soak with no Task WDT and no regression in control cadence.

The last-good promotion remains blocked until the acceptance packet distinguishes chronic baseline pressure from the new firmware delta.
