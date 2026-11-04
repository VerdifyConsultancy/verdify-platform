// SPDX-License-Identifier: MIT
import { useId, type ChangeEvent, type FormEvent } from "react";
import { useNavigate } from "react-router";

/**
 * "Find a setting" in the Plan and Equipment headers (redesign §5.11, §5.12): a search that lands on Settings filtered
 * by name or key (`/settings?q=`). It is a plain GET form, so it works before the page hydrates too; once it has, the
 * router navigates without reloading the page.
 */
export function FindSetting() {
  const id = useId();
  const navigate = useNavigate();
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const words = String(new FormData(event.currentTarget).get("q") ?? "").trim();
    navigate(words ? `/settings?${new URLSearchParams({ q: words })}` : "/settings");
  };
  return <form className="ctl-find" method="get" action="/settings" role="search" aria-label="Find a setting" onSubmit={submit}>
    <label className="screen-reader" htmlFor={id}>Find a setting</label>
    <input id={id} type="search" name="q" placeholder="Find a setting" autoComplete="off" />
    <button type="submit" className="btn">Find</button>
  </form>;
}

/** Settings' own search (redesign §5.10): it filters the catalog as it is typed, by plain label or key, kept in the URL as `q`. */
export function SettingsSearch({ value, onChange }: { value: string; onChange: (text: string) => void }) {
  const id = useId();
  return <div className="ctl-find ctl-search" role="search" aria-label="Settings">
    <label className="screen-reader" htmlFor={id}>Search settings</label>
    <input id={id} type="search" value={value} placeholder="Search settings: name or key" autoComplete="off"
      onChange={(event: ChangeEvent<HTMLInputElement>) => onChange(event.target.value)} />
  </div>;
}
