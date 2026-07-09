Interior/crop DLI is not currently measurable: the sensor is broken, firmware publishes an exterior/proxy-derived value with a cadence error, and downstream context double-counts it. #435 now owns the schema-first unavailable/provenance contract.

Until #435 is live, DLI must be excluded from planner outcome objectives, grading, homepage composites, and acceptance claims. Time-in-band, DIF, qualified light minutes, and energy/water evidence may continue independently. Re-enable DLI only after the replacement sensor passes an explicit validity/calibration contract.
