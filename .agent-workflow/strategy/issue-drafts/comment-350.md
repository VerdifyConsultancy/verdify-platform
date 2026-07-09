Jason's July 9 product correction resolves the software intent that this epic left open:

- VPD climate mist is **center-only**.
- South/west mist is explicit intentional irrigation only and default-disabled while unplanted.
- Center drip is unconnected and disabled.
- Fertilizer is **wall-drip-only**; dormant center/south/west infrastructure stays represented but disabled.
- Automatic wall feed is a once-weekly pilot, commissioning-gated, liters-based, and immediately clean-flushed; software must not guess the shared lime/cannabis recipe.
- New sensors and physical work are out of this recovery.

Executable child: #434. It supersedes #37's center-start change and #323's climate fairness router. Do not move the existing 10:30 wall schedule into the feed window before #434 fixes routing, or current code will also fertilize south/west misters.
