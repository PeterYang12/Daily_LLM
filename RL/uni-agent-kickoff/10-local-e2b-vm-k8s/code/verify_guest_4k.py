#!/usr/bin/env python3
"""Read-only check of Firecracker guest RAM page configuration and Linux VMAs."""
import argparse
import http.client
import json
from pathlib import Path
import re
import socket
import sys


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=5)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def api_get(path, endpoint):
    conn = UnixHTTPConnection(path)
    try:
        conn.request("GET", endpoint)
        response = conn.getresponse()
        data = response.read(1 << 20)
        if response.status != 200:
            raise RuntimeError(f"GET {endpoint}: HTTP {response.status}")
        return json.loads(data)
    finally:
        conn.close()


def process_start(pid):
    # Linux /proc stat field 22, after '(comm)' which can contain spaces.
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def read_smaps(pid):
    regions = []
    for line in Path(f"/proc/{pid}/smaps").read_text().splitlines():
        match = re.match(r"^([0-9a-f]+)-([0-9a-f]+)\s", line)
        if match:
            regions.append({"start": int(match[1], 16), "end": int(match[2], 16),
                            "mapping": line})
        elif ":" in line and regions:
            key, value = line.split(":", 1)
            regions[-1][key] = value.strip()
    return regions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--socket")
    parser.add_argument("--sandbox-id")
    args = parser.parse_args()
    if args.sandbox_id:
        if not re.fullmatch(r"[a-z0-9]+", args.sandbox_id):
            parser.error("invalid sandbox ID")
        matches = []
        for proc in Path('/proc').glob('[0-9]*/cmdline'):
            try:
                argv = proc.read_bytes().decode(errors='replace').rstrip('\0').split('\0')
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
            if not argv or Path(argv[0]).name != 'firecracker' or '--api-sock' not in argv:
                continue
            socket_path = argv[argv.index('--api-sock') + 1]
            if Path(socket_path).name.startswith(f'fc-{args.sandbox_id}-'):
                matches.append((int(proc.parent.name), socket_path))
        if len(matches) != 1:
            raise RuntimeError(f'expected one Firecracker for sandbox, found {len(matches)}')
        args.pid, args.socket = matches[0]
    elif args.pid is None or args.socket is None:
        parser.error('provide --sandbox-id or both --pid and --socket')
    start = process_start(args.pid)
    machine = api_get(args.socket, "/machine-config")
    mappings = api_get(args.socket, "/memory/mappings")["mappings"]
    smaps = read_smaps(args.pid)
    if process_start(args.pid) != start:
        raise RuntimeError("process changed while collecting evidence")
    problems = []
    if not mappings:
        problems.append("FC reported no guest mappings")
    if machine.get("huge_pages") not in (None, "None"):
        problems.append(f"machine huge_pages={machine.get('huge_pages')!r}")
    output = []
    for guest in mappings:
        begin = guest["base_host_virt_addr"]
        end = begin + guest["size"]
        if guest["page_size"] != 4096:
            problems.append(f"FC guest page_size={guest['page_size']}")
        matched = [v for v in smaps if v["start"] < end and v["end"] > begin]
        covered = sum(min(end, v["end"]) - max(begin, v["start"]) for v in matched)
        if covered != guest["size"]:
            problems.append("guest mapping not fully covered in target PID smaps")
        fields = ("KernelPageSize", "MMUPageSize", "Rss", "AnonHugePages",
                  "ShmemPmdMapped", "FilePmdMapped", "Private_Hugetlb",
                  "Shared_Hugetlb", "VmFlags")
        for vma in matched:
            evidence = {key: vma.get(key) for key in fields}
            evidence["mapping"] = vma["mapping"]
            output.append(evidence)
            for key in ("KernelPageSize", "MMUPageSize"):
                if vma.get(key) != "4 kB":
                    problems.append(f"{key}={vma.get(key)!r}")
            for key in ("AnonHugePages", "ShmemPmdMapped", "FilePmdMapped",
                        "Private_Hugetlb", "Shared_Hugetlb"):
                value = vma.get(key)
                if value is not None and int(value.split()[0]) != 0:
                    problems.append(f"{key}={value}")
            if "ht" in vma.get("VmFlags", "").split():
                problems.append("guest VMA has hugetlb VmFlag ht")
    print(json.dumps({"pid": args.pid, "machine_config": machine,
                      "guest_mappings": mappings, "guest_smaps": output,
                      "four_kib_verified": not problems,
                      "problems": problems}, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"verification incomplete: {error}", file=sys.stderr)
        sys.exit(2)
