Removed from the July 9 executable recovery after raw-edge/control audit. The broad dwell-versus-hysteresis consolidation is not needed to deliver the approved fixes, overlaps the one-resolver work, and would add behavior risk while heap and outcome evidence are still being repaired.

The recovery preserves existing fan/fog safety/dwell behavior, fixes trustworthy counts in #389, and gates the OTA with #390. Re-scope this issue only after those surfaces demonstrate a repeatable residual transition defect and an isolated response test proves a smaller safe change.
