#!/bin/sh
# Build-time only. Runtime serves these files from the application itself.
set -eu
version=5.4.624
base=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl --fail --location --silent --show-error "https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-${version}.tgz" -o "$tmp/pdfjs.tgz"
tar -xzf "$tmp/pdfjs.tgz" -C "$tmp"
mkdir -p "$base/frontend/vendor/pdfjs"
cp "$tmp/package/build/pdf.mjs" "$tmp/package/build/pdf.worker.mjs" "$tmp/package/LICENSE" "$base/frontend/vendor/pdfjs/"
for dir in cmaps standard_fonts wasm; do
  cp -R "$tmp/package/$dir" "$base/frontend/vendor/pdfjs/"
done
