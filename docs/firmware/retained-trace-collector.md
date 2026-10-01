# Source-only retained6ba collector

This diagnostic collector is based on preserved ee1. It does not instrument API drain, lwIP locks, queue waits or panic; it preserves ee1 allocator, controller and transport behavior. The upstream ESPHome panic wrapper remains stock.

The exact 32+128-byte NOINIT envelope keeps the6ba Record ABI at0x50000020..9f. Boot copies without modifying RTC. An existing native ERROR-or-higher subscription publishes only a record with exact6ba source token44884f32ef335759f7481daed6cece302cc22a10bc53f976d1a2de22116383a1, original nonzero boot nonce, magic and bounded discriminants. An unavailable record produces an explicit collector-unavailable line; no fields or nonce are fabricated. No log attempt consumes the record. Any recovered PCs must be decoded only with6ba ELF189df88a5f03de715471c60c555f235f13812f80fb26b010cdbe0c16296e2dd7. The actual failedboot nonce is unknown; nonzero validation is structural, not independent boot attribution.

Selected IDF startup clears only _rtc_bss_start..end. Actual ee1 bounds are empty at0x50000000. Its initialized image writes32bytes at0x50000000, leaving the payload outside that loaded segment. The bootloader loads exact image segments rather than clearing all RTC; reserved timer storage is at0x50001fe8. Thus selected software startup has no identified overwrite of the payload. Hardware/reset/power effects and whether6ba reached panic_capture remain unproven. Absence of a valid record cannot identify cause.

The two failed diagnostic OTAs do not prove wrappers caused the boot failure. This collector removes them to isolate collection from instrumentation. No firmware fix, adoption, stability or recovery acceptance is claimed. No push or OTA is authorized in this lane.
