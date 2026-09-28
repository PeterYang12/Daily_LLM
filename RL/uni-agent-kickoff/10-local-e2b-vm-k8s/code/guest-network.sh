#!/bin/bash
# Run only inside the dedicated VM. Route E2B's fixed DNS to its working uplink DNS.
set -euo pipefail
test "$(hostname)" = e2b-vm
for protocol in udp tcp; do
  for source in 10.11.0.0/16 10.12.0.0/16; do
    rule=(-s "$source" -d 8.8.8.8/32 -p "$protocol" --dport 53 -m comment --comment e2b-local-dns -j DNAT --to-destination 10.0.2.3:53)
    iptables -t nat -C PREROUTING "${rule[@]}" 2>/dev/null || iptables -t nat -I PREROUTING 1 "${rule[@]}"
  done
  rule=(-d 8.8.8.8/32 -p "$protocol" --dport 53 -m comment --comment e2b-local-dns -j DNAT --to-destination 10.0.2.3:53)
  iptables -t nat -C OUTPUT "${rule[@]}" 2>/dev/null || iptables -t nat -I OUTPUT 1 "${rule[@]}"
done
