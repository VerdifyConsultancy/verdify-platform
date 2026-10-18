# Secretless compiler evidence — 2026-10-18

Both maintained firmware targets compiled successfully for exact source
`bdef62c1d640636411995c23aedeee85f342303f`. Workflow
`verdify-ready-firmware-compile-bdef62c1-01` completed at 08:07:29 UTC; its one
check Pod exited 0 without a restart. [The structured receipt](secretless-compile-result.json)
binds the workflow and Pod UIDs, full log, exact declared command, source inputs,
generated C++, output hashes and downloaded toolchain provenance.

| Target | OTA-format bytes | SHA256 |
| --- | ---: | --- |
| greenhouse | 1,106,512 | `ee10257c63fb2223011c11f67c5f9016ff6598717fa2d2781d07ef2989832dbe` |
| greenhouse-recovery | 1,101,376 | `95bdefea408e1ae265278ee70875459f92f449719eab90dc3116116937750e26` |

The actual run used the unchanged registered `firmware-secretless-compile` check,
ESPHome 2026.6.5 and its pinned image, on the admitted k3s worker. The complete
43-file tracked firmware input manifest was independently recomputed and matches
both target receipts. The generated-service negative inserted a deliberately
undefined symbol into a config-valid service and its actual compilation exited 1;
the registered check required both nonzero exit and the symbol in the diagnostic.
This expected failure is retained as evidence and is not a failed main build.

The actual Linux x86_64 receipt records ten package manifests and all sixteen
required compiler filenames, sizes and hashes before private scratch removal.
These are native observations of downloaded bytes, not ARM64 image assumptions.
The complete original main log is retained in private custody and in [the public compressed capsule](firmware-secretless-main.log.gz): 1,858,510 bytes, SHA256
`3527d76fa938f65c3ff012da44a885cd0a21c355fc5c5e13417ba1d51b6a397d`.
The negative compiler output was consumed by the registered check and is not
separately retained; its final result and exit code are retained. Temporary
firmware and compiler binaries were removed by the check, so their custody is
hash-only, not an independently rehashed retained binary copy.

All compile credentials were explicit public dummy values. Both outputs are
**NONDEPLOYABLE**, never last-good firmware. No OTA, production change, live
sensor-health readback, physical chatter/recovery or whole-issue acceptance is
claimed. This evidence applies to the named source, not later combined PR edits.
The earlier [strict native evidence](../../../../docs/handoffs/firmware-validity-dwell-20261018/evidence/README.md), its original causal failures and
submission/collector history remain unchanged. #367 and #368 remain open for
remaining acceptance.
