#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
test_dir=$(mktemp -d /tmp/gameportal-store-test.XXXXXX)
trap 'rm -rf "$test_dir"' EXIT
# Compile the production store unchanged, without the app's iOS-only views.
python3 - "$test_dir/Scenario.swift" <<'PY'
from pathlib import Path
import sys
source = Path('GamePortal/GamePortalApp.swift').read_text()
store = source[source.index('private struct OnlineIdentity:'):source.index('struct OnlinePortalView:')]
Path(sys.argv[1]).write_text('import Foundation\nimport SwiftUI\nimport Security\n' + store + Path('GamePortalTests/OnlineStore/scenario.swift').read_text())
PY
swiftc -parse-as-library -module-cache-path "$test_dir/modules" GamePortal/OnlineCache.swift "$test_dir/Scenario.swift" -o "$test_dir/tests"
"$test_dir/tests"
