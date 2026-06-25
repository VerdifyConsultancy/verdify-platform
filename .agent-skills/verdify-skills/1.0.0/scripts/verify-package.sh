#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 1 ]] || { echo "Usage: scripts/verify-package.sh ARCHIVE.zip" >&2; exit 2; }
archive="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
[[ -f "$archive" ]] || { echo "archive not found: $archive" >&2; exit 2; }
for command in node ruby unzip; do
  command -v "$command" >/dev/null || { echo "$command is required to verify Verdify" >&2; exit 1; }
done
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
unzip -q "$archive" -d "$TMP"
mapfile -t roots < <(find "$TMP" -mindepth 1 -maxdepth 1 -type d | sort)
[[ ${#roots[@]} -eq 1 ]] || { echo "archive must contain exactly one root directory" >&2; exit 1; }
root="${roots[0]}"
[[ -f "$root/MANIFEST.sha256" ]] || { echo "MANIFEST.sha256 is missing" >&2; exit 1; }
(
  cd "$root"
  ruby -rdigest -e '
    File.foreach("MANIFEST.sha256", chomp: true) do |line|
      digest, path = line.split(/  /, 2)
      abort "invalid manifest line: #{line}" unless digest&.match?(/\A[0-9a-f]{64}\z/) && path && File.file?(path)
      actual = Digest::SHA256.file(path).hexdigest
      abort "checksum mismatch: #{path}" unless actual == digest
    end
  '
  ruby scripts/setup-agent-hosts.rb --check
  ruby scripts/validate-repo.rb
)
echo "Package verification passed: $archive"
