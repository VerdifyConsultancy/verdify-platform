#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(cat "$ROOT/VERSION")"
OUT="${1:-$ROOT/dist}"
NAME="verdify-lifecycle-skills-v${VERSION}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

for command in ruby rsync zip; do
  command -v "$command" >/dev/null || { echo "$command is required to package Verdify" >&2; exit 1; }
done

make -C "$ROOT" test
mkdir -p "$OUT" "$STAGE/$NAME"
rsync -a \
  --exclude '/.git' \
  --exclude '/.agent-skills' \
  --exclude '/.agent-workflow' \
  --exclude '/dist' \
  --exclude '/MANIFEST.sha256' \
  --exclude '/node_modules' \
  "$ROOT/" "$STAGE/$NAME/"
(
  cd "$STAGE/$NAME"
  ruby -rdigest -e '
    paths = Dir.glob("**/*", File::FNM_DOTMATCH).select do |path|
      File.file?(path) && !File.symlink?(path) && path != "MANIFEST.sha256"
    end.sort
    File.open("MANIFEST.sha256", "w") do |manifest|
      paths.each { |path| manifest.puts "#{Digest::SHA256.file(path).hexdigest}  #{path}" }
    end
  '
)
(
  cd "$STAGE"
  zip -qry "$OUT/$NAME.zip" "$NAME"
)
ruby -rdigest -e 'path=ARGV.fetch(0); File.write(path + ".sha256", "#{Digest::SHA256.file(path).hexdigest}  #{File.basename(path)}\n")' "$OUT/$NAME.zip"
printf '%s\n' "$OUT/$NAME.zip"
