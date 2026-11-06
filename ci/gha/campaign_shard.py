#!/usr/bin/env python3
"""Run verdify-campaign-ci unchanged, optionally on one shard of its selection (D-55).

usage: campaign_shard.py /usr/local/bin/verdify-campaign-ci
CAMPAIGN_SHARD=i/n keeps every n-th file (from i, 1-based) of the tool's sorted
selection; unset or empty runs the tool's full selection exactly as before.
"""
import importlib.machinery
import importlib.util
import os
import sys

tool = sys.argv[1]
loader = importlib.machinery.SourceFileLoader("verdify_campaign_ci", tool)
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)

shard = os.environ.get("CAMPAIGN_SHARD", "")
if shard:
    index, count = (int(part) for part in shard.split("/"))
    if not 1 <= index <= count:
        raise SystemExit(f"invalid CAMPAIGN_SHARD={shard}")
    full_selection = module.selection

    def selection(select=None):
        selected, missing = full_selection(select)
        kept = selected[index - 1::count]
        print(f"campaign shard {index}/{count}: {len(kept)} of {len(selected)} files: "
              + ", ".join(kept), flush=True)
        if not kept:
            raise ValueError(f"campaign shard {shard} selected no files")
        return kept, missing

    module.selection = selection

raise SystemExit(module.main())
