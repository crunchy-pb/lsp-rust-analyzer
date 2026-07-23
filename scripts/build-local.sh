#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
runtime=${CONTAINER_RUNTIME:-docker}
image=${IMAGE_NAME:-pb/lsp-rust-analyzer:dev}

python3 "$repo_dir/scripts/validate-package.py"

manifest=$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1], encoding="utf-8")), separators=(",", ":")))' "$repo_dir/pb-lsp.json")
schema=$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1], encoding="utf-8")), separators=(",", ":")))' "$repo_dir/config.schema.json")

"$runtime" build \
  --build-arg "PB_LSP_MANIFEST=$manifest" \
  --build-arg "INTEGRATION_CONFIG_SCHEMA=$schema" \
  --tag "$image" \
  "$repo_dir"

python3 "$repo_dir/scripts/validate-package.py" --runtime "$runtime" --image "$image"
