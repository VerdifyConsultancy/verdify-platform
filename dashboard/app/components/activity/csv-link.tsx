// SPDX-License-Identifier: MIT
import { useEffect, useRef, useState } from "react";

/**
 * A CSV the browser builds from what the page holds and saves under `filename` (redesign §5.9 "CSV of these entries"):
 * a Blob URL, made again whenever `version` changes and revoked when it is replaced. Nothing is fetched. Where Blob URLs
 * are unavailable the link stays without a target rather than breaking the page.
 */
export function CsvLink({ filename, build, version, label }: { filename: string; build: () => string; version: unknown; label: string }) {
  const latest = useRef(build);
  latest.current = build;
  const [href, setHref] = useState<string | undefined>(undefined);
  useEffect(() => {
    let url: string;
    try {
      url = URL.createObjectURL(new Blob([latest.current()], { type: "text/csv;charset=utf-8" }));
    } catch {
      setHref(undefined);
      return;
    }
    setHref(url);
    return () => URL.revokeObjectURL(url);
  }, [version]);
  return <a className="button-link act-csv" href={href} download={filename}>{label}</a>;
}
