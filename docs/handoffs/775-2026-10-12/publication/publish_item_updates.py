"""Publication-only handoff writer. Run only in the coordinator's named lease."""

import datetime
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import time

R = pathlib.Path("/Users/jason/maintenance/verdify-paused-20261012")
PHASE = R / "publication-resumed-20261012"
PHASE.mkdir(exist_ok=True)
OUT = R / "item-update-readbacks"
OUT.mkdir(exist_ok=True)
items = json.loads((R / "all-goal-items.json").read_text())
ordered = sorted(items, key=lambda i: (i["number"] not in [775, 644, 949, 396, 382, 643, 973], i["number"]))
results = []
last_write = 0.0


def now():
    return datetime.datetime.now(datetime.UTC).isoformat()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def get(args):
    p = subprocess.run(["gh", "api", *args], capture_output=True, text=True, timeout=60)
    if p.returncode:
        save(PHASE / "read-error.json", {"observed_at": now(), "args": args, "exit": p.returncode, "stderr": p.stderr})
        raise SystemExit("GET failed; no next mutation; durable created-comment record retained")
    return json.loads(p.stdout)


def checkpoint(state):
    save(R / "item-update-verification.json", results)
    done = {x["number"] for x in results}
    save(
        R / "item-update-queue.json",
        {
            "checkpoint_at": now(),
            "state": state,
            "verified": len(done),
            "total": len(items),
            "remaining": [x["number"] for x in items if x["number"] not in done],
            "payload_directory": "item-update-payloads",
            "coverage_review_complete": True,
            "scope": "Issue/PR publication only; product implementation stays paused",
            "writer": "Named coordinator lease, >=10 seconds between POSTs, GET dedupe and exact readback",
        },
    )


try:
    for item in ordered:
        n = item["number"]
        payload = R / "item-comment-payloads" / f"{n}.json"
        body = json.loads(payload.read_text())["body"]
        prior = OUT / f"{n}.json"
        created_path = PHASE / f"{n}-created.json"
        if prior.exists():
            known = json.loads(prior.read_text())
            actual = get([f"repos/VerdifyConsultancy/verdify-platform/issues/comments/{known['id']}"])
        elif created_path.exists():
            known = json.loads(created_path.read_text())
            actual = get([f"repos/VerdifyConsultancy/verdify-platform/issues/comments/{known['id']}"])
        else:
            pages = get(
                ["--paginate", "--slurp", f"repos/VerdifyConsultancy/verdify-platform/issues/{n}/comments?per_page=100"]
            )
            comments = [c for page in pages for c in page]
            marker = f"<!-- verdify-paused-handoff-20261012:{n} -->"
            matches = [c for c in comments if marker in c.get("body", "")]
            if matches:
                exact = [c for c in matches if c["body"] == body]
                if not exact:
                    save(
                        PHASE / f"{n}-marker-conflict.json",
                        {"comments": matches, "expected_body_sha256": hashlib.sha256(body.encode()).hexdigest()},
                    )
                    raise SystemExit(f"Existing marker/body conflict for {n}; no duplicate POST")
                known = exact[0]
                actual = get([f"repos/VerdifyConsultancy/verdify-platform/issues/comments/{known['id']}"])
            else:
                remaining = max(0, 10.0 - (time.monotonic() - last_write))
                if remaining:
                    time.sleep(remaining)
                p = subprocess.run(
                    [
                        "gh",
                        "api",
                        "--include",
                        "--method",
                        "POST",
                        f"repos/VerdifyConsultancy/verdify-platform/issues/{n}/comments",
                        "--input",
                        str(payload),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                last_write = time.monotonic()
                chunks = re.split(r"\r?\n\r?\n", p.stdout, maxsplit=1)
                headers = chunks[0] if len(chunks) == 2 else ""
                raw_body = chunks[1] if len(chunks) == 2 else p.stdout
                (PHASE / f"{n}-response-headers.txt").write_text(headers + "\n")
                if p.returncode:
                    save(
                        PHASE / "mutation-stop.json",
                        {
                            "observed_at": now(),
                            "number": n,
                            "exit": p.returncode,
                            "headers": headers,
                            "stderr": p.stderr,
                            "body": raw_body,
                            "action": "STOP; no automatic POST retry/probe",
                        },
                    )
                    checkpoint("STOPPED ON GITHUB MUTATION ERROR; no retry/probe")
                    raise SystemExit(f"POST failed for {n}; STOP ALL, inspect saved response headers; no retry")
                known = json.loads(raw_body)
                save(created_path, known)
                actual = get([f"repos/VerdifyConsultancy/verdify-platform/issues/comments/{known['id']}"])
        assert actual["body"] == body, f"Remote body mismatch for {n}"
        save(prior, actual)
        results.append(
            {
                "number": n,
                "kind": "PR" if "pull_request" in item else "issue",
                "state_at_capture": item["state"],
                "comment_id": actual["id"],
                "url": actual["html_url"],
                "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "remote_body_exact": True,
                "verified_at": now(),
            }
        )
        checkpoint("PUBLISHING IN SOLE WRITER LEASE")
        print(f"GET-VERIFIED {len(results)}/{len(items)} item #{n}", flush=True)
    checkpoint("COMPLETE; ALL98 ITEM COMMENTS REMOTE-BODY VERIFIED")
    print(f"ALL ITEM UPDATES COMPLETE {len(results)}", flush=True)
except (subprocess.TimeoutExpired, AssertionError, ValueError) as error:
    save(
        PHASE / "unexpected-stop.json",
        {
            "observed_at": now(),
            "error": str(error),
            "action": "STOP; next run reconciles durable comment IDs/markers before POST",
        },
    )
    checkpoint("STOPPED; durable IDs retained, GET reconciliation required")
    sys.exit(str(error))
