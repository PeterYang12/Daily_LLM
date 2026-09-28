#!/usr/bin/env bash
set -euo pipefail

root="${E2B_LOCAL_ROOT:-/data2/yuhanya/e2b-local-20260916}"
source_dir="${E2B_RUNTIME_SOURCE_DIR:-$root/build/runtime-4k-20260920}"
output="${E2B_RUNTIME_OUTPUT_DIR:-$root/migrations/20260920-4k-512/bin}"
export PATH="$root/toolchain/go1.26.8/go/bin:$PATH"
export GOTOOLCHAIN=local
export GOMAXPROCS=16
export GOCACHE="$root/build/go-cache"
export GOMODCACHE="$root/build/go-mod-cache"
export TMPDIR="$root/build/tmp"
version="${E2B_RUNTIME_VERSION:-local-4k-512-20260920}"
commit=e7b22501988c826563bf1b3cf0d22a1183e7affe
test "$(git -C "$source_dir" rev-parse HEAD)" = "$commit"
mkdir -p "$output" "$GOCACHE" "$GOMODCACHE" "$TMPDIR"

case "${1:?choose api, orchestrator, or migrator}" in
  api)
    cd "$source_dir/packages/api"
    CGO_ENABLED=0 go build -p 12 -o "$output/api" \
      -ldflags "-X=main.commitSHA=$commit-local-4k -X=main.expectedMigrationTimestamp=20260915204740 -X=main.serviceVersion=$version" .
    ;;
  orchestrator)
    cd "$source_dir/packages/orchestrator"
    CGO_ENABLED=1 go build -p 12 -o "$output/orchestrator" \
      -ldflags "-X=main.commitSHA=$commit-local-4k -X=github.com/e2b-dev/infra/packages/orchestrator/pkg/version.Version=$version" .
    ;;
  migrator)
    cd "$source_dir/packages/db"
    CGO_ENABLED=0 go build -p 8 -o "$output/migrator" ./scripts/migrator.go
    ;;
  *) exit 2 ;;
esac
sha256sum "$output/$1"
