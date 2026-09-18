#!/usr/bin/env bash
# Regenerate docs/cv.md from docs/Academic CV.pdf (needs poppler's pdftotext).
set -euo pipefail
cd "$(dirname "$0")/.."
{
  echo "# Academic CV"
  echo
  echo "_Text extraction of \`docs/Academic CV.pdf\`. Regenerate with \`scripts/extract-cv.sh\` after updating the PDF._"
  echo
  pdftotext -layout "docs/Academic CV.pdf" - \
    | sed 's/\xe2\x80\x8b//g; s/[ \t]*$//; s/●/-/' \
    | sed '/^$/N;/^\n$/D'
} > docs/cv.md
echo "wrote docs/cv.md"
