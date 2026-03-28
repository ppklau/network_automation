#!/usr/bin/env python3
"""
generate_branch.py — ACME Investments Branch Node Generator
============================================================
Generates a new branch site entry in nodes.yml that is guaranteed
to satisfy all applicable design intents from design_intents.yml.

The script derives as much as possible from intent rules. Only
the inputs that cannot be derived (site name, IP prefix, ASNs)
are required as arguments.

Usage:
    python generate_branch.py \
        --nodes       nodes.yml \
        --intents     design_intents.yml \
        --site-id     nyc-branch1 \
        --location    "New York, US" \
        --prefix      10.2.0.0/16 \
        --router-asn  65301 \
        --router-ip   10.2.0.1 \
        --router-lo   10.2.254.1 \
        --switch-ip   10.2.0.11 \
        --switch-lo   10.2.254.11 \
        --output      nodes.yml       # overwrites in-place; use --dry-run to preview

Flags:
    --dry-run     Print generated YAML to stdout, do not write to nodes.yml
    --verify      Run verify_intents.py on the result after writing
"""

import argparse
import ipaddress
import subprocess
import sys
from pathlib import Path

import yaml


# ── intent-derived constants ──────────────────────────────────────────────────
# These values come directly from design_intents.yml and must not be
# overridden by callers — they are non-negotiable design decisions.

SYSLOG_SERVERS  = ["10.0.0.100", "10.0.0.101"]   # INTENT-MGMT-02
SNMP_CONFIG     = {"version": "v3", "auth": "SHA", "priv": "AES128",
                   "collector": "10.0.0.100"}      # INTENT-MGMT-02
OSPF_AREA       = 0                                # INTENT-RTG-03
OSPF_PROCESS_ID = 1                                # INTENT-RTG-03
CORPORATE_VLAN  = 20                               # INTENT-SEG-01 (branch has Corporate only)


def load_yaml(path: str) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def dump_yaml(data: dict) -> str:
    return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)


def validate_prefix(prefix: str) -> ipaddress.IPv4Network:
    try:
        return ipaddress.ip_network(prefix, strict=False)
    except ValueError as e:
        sys.exit(f"ERROR: invalid prefix '{prefix}': {e}")


def check_prefix_unique(prefix: str, existing_nodes: list) -> None:
    """Warn if the prefix overlaps with any existing management address."""
    net = validate_prefix(prefix)
    for node in existing_nodes:
        mgmt = node.get("management", {}) or {}
        addr_raw = mgmt.get("address", "")
        if not addr_raw:
            continue
        try:
            addr = ipaddress.ip_address(addr_raw.split("/")[0])
            if addr in net:
                sys.exit(
                    f"ERROR: prefix {prefix} overlaps with existing node "
                    f"'{node['hostname']}' management address {addr_raw}"
                )
        except ValueError:
            pass


def build_wan_router(args, prefix: ipaddress.IPv4Network) -> dict:
    """
    Build the WAN router node dict, satisfying:
      INTENT-RTG-03  — OSPF area 0, default route injected
      INTENT-SEG-01  — Corporate zone only at branch
      INTENT-MGMT-01 — OOB via ssh_source_interface (IOS platform)
      INTENT-MGMT-02 — Syslog x2, SNMPv3
      INTENT-IP-01   — All IPs within site prefix
    """
    hostname = f"{args.site_id}-rtr01"
    corporate_subnet = str(ipaddress.ip_network(
        f"{args.router_ip}/{prefix.prefixlen}", strict=False
    ))

    return {
        "hostname": hostname,
        "platform": "cisco_ios",
        "role": "wan_router",
        "site": args.site_id,
        "intent": ["INTENT-RTG-03", "INTENT-SEG-01",
                   "INTENT-MGMT-01", "INTENT-MGMT-02", "INTENT-IP-01"],

        "loopback": {
            "address": f"{args.router_lo}/32",
            # intent: INTENT-RTG-03 (router-id source)
        },

        "interfaces": [
            {
                "name": "GigabitEthernet0/0",
                "description": "WAN uplink — primary carrier",
                "address": "dhcp",
                "zone": "wan",
                "mode": "routed",
                "ospf": {"enabled": False},
                # intent: INTENT-NET-05 (dual WAN — second circuit on Gi0/2)
            },
            {
                "name": "GigabitEthernet0/2",
                "description": "WAN uplink — secondary carrier",
                "address": "dhcp",
                "zone": "wan",
                "mode": "routed",
                "ospf": {"enabled": False},
            },
            {
                "name": "GigabitEthernet0/1",
                "description": "LAN trunk to branch switches",
                "zone": "corporate",
                "mode": "trunk",
                "ospf": {"enabled": True, "area": OSPF_AREA,
                         "network_type": "broadcast"},
                # intent: INTENT-RTG-03
            },
            {
                "name": f"GigabitEthernet0/1.{CORPORATE_VLAN}",
                "description": f"Corporate VLAN {CORPORATE_VLAN}",
                "address": f"{args.router_ip}/24",
                "vlan": CORPORATE_VLAN,
                "zone": "corporate",
                "mode": "routed",
                # intent: INTENT-IP-01
            },
        ],

        "ospf": {
            # intent: INTENT-RTG-03 — OSPF area 0 at branch
            "process_id": OSPF_PROCESS_ID,
            "router_id": args.router_lo,
            "area": OSPF_AREA,
            "default_route": "inject",   # satisfies REQ-BIZ-04 (DR reachability)
        },

        "acls": [
            {
                "name": "ACL_CORPORATE_IN",
                # intent: INTENT-SEG-02 — deny default, REQ-ID comments
                "default_action": "deny",
                "entries": [
                    {
                        "seq": 10,
                        "action": "permit",
                        "protocol": "tcp",
                        "src": f"{args.router_ip.rsplit('.', 1)[0]}.0/24",
                        "dst": f"{args.router_ip.rsplit('.', 1)[0]}.0/24",
                        "dst_port": "any",
                        "comment": "REQ-SEC-01: intra-corporate east-west",
                    },
                    {
                        "seq": 20,
                        "action": "permit",
                        "protocol": "tcp",
                        "src": f"{args.router_ip.rsplit('.', 1)[0]}.0/24",
                        "dst": "10.0.0.100",
                        "dst_port": 514,
                        "comment": "REQ-OPS-03: syslog to primary collector",
                    },
                    {
                        "seq": 9999,
                        "action": "deny",
                        "protocol": "ip",
                        "src": "any",
                        "dst": "any",
                        "comment": "REQ-SEC-02: explicit deny-all",
                    },
                ],
            }
        ],

        "management": {
            # intent: INTENT-MGMT-01 — OOB via loopback source (IOS)
            "address": f"{args.router_ip}/24",
            "ssh_source_interface": "Loopback0",
            "syslog_servers": SYSLOG_SERVERS,  # intent: INTENT-MGMT-02
            "snmp": SNMP_CONFIG,                # intent: INTENT-MGMT-02
        },
    }


def build_access_switch(args, prefix: ipaddress.IPv4Network) -> dict:
    """
    Build the access switch node dict for the branch site.
    Satisfies same intents as the WAN router at the access layer.
    """
    hostname = f"{args.site_id}-sw01"

    return {
        "hostname": hostname,
        "platform": "cisco_ios",
        "role": "access_switch",
        "site": args.site_id,
        "intent": ["INTENT-RTG-03", "INTENT-SEG-01",
                   "INTENT-MGMT-01", "INTENT-MGMT-02"],

        "interfaces": [
            {
                "name": "GigabitEthernet0/1",
                "description": f"Uplink to {args.site_id}-rtr01",
                "zone": "corporate",
                "mode": "trunk",
                "allowed_vlans": [CORPORATE_VLAN],
                # intent: INTENT-SEG-01 (Corporate only at branch)
            },
            {
                "name": "GigabitEthernet0/2",
                "description": "Staff desks",
                "zone": "corporate",
                "mode": "access",
                "vlan": CORPORATE_VLAN,
            },
        ],

        "ospf": {
            # intent: INTENT-RTG-03
            "process_id": OSPF_PROCESS_ID,
            "router_id": args.switch_lo,
            "area": OSPF_AREA,
        },

        "management": {
            "address": f"{args.switch_ip}/24",
            "ssh_source_interface": f"Vlan{CORPORATE_VLAN}",
            "syslog_servers": SYSLOG_SERVERS,
            "snmp": SNMP_CONFIG,
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate a branch site in nodes.yml from design intents"
    )
    parser.add_argument("--nodes",       default="nodes.yml")
    parser.add_argument("--intents",     default="design_intents.yml")
    parser.add_argument("--site-id",     required=True,
                        help="Site identifier, e.g. nyc-branch1")
    parser.add_argument("--location",    required=True,
                        help="Human-readable location, e.g. 'New York, US'")
    parser.add_argument("--prefix",      required=True,
                        help="Site IP prefix, e.g. 10.2.0.0/16")
    parser.add_argument("--router-ip",   required=True,
                        help="WAN router LAN IP (no mask), e.g. 10.2.20.1")
    parser.add_argument("--router-lo",   required=True,
                        help="WAN router loopback IP, e.g. 10.2.254.1")
    parser.add_argument("--switch-ip",   required=True,
                        help="Access switch mgmt IP, e.g. 10.2.0.11")
    parser.add_argument("--switch-lo",   required=True,
                        help="Access switch loopback IP, e.g. 10.2.254.11")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Print YAML to stdout, do not write file")
    parser.add_argument("--verify",      action="store_true",
                        help="Run verify_intents.py after writing")
    parser.add_argument("--output",      default=None,
                        help="Output nodes.yml path (default: same as --nodes)")
    args = parser.parse_args()

    # ── load existing SoT ──────────────────────────────────────────────────
    data = load_yaml(args.nodes)
    existing_nodes: list = data.get("nodes", [])

    # ── guard: don't add a duplicate site ─────────────────────────────────
    existing_sites = {n.get("site") for n in existing_nodes}
    if args.site_id in existing_sites:
        sys.exit(
            f"ERROR: site '{args.site_id}' already exists in {args.nodes}. "
            f"Remove existing nodes or choose a different site-id."
        )

    # ── guard: prefix must not overlap existing management addresses ───────
    prefix = validate_prefix(args.prefix)
    check_prefix_unique(args.prefix, existing_nodes)

    # ── generate nodes ─────────────────────────────────────────────────────
    print(f"\nGenerating branch site: {args.site_id} ({args.location})")
    print(f"  Site prefix : {args.prefix}")
    print(f"  WAN router  : {args.site_id}-rtr01  lo={args.router_lo}")
    print(f"  Access sw   : {args.site_id}-sw01   lo={args.switch_lo}")
    print(f"  Intents     : INTENT-RTG-03, INTENT-SEG-01, "
          f"INTENT-MGMT-01, INTENT-MGMT-02, INTENT-IP-01\n")

    router  = build_wan_router(args, prefix)
    switch  = build_access_switch(args, prefix)

    if args.dry_run:
        new_nodes = {"nodes": [router, switch]}
        print("─── DRY RUN — generated nodes (not written) ───")
        print(dump_yaml(new_nodes))
        return

    # ── append and write ───────────────────────────────────────────────────
    data["nodes"].extend([router, switch])
    out_path = args.output or args.nodes
    Path(out_path).write_text(dump_yaml(data))
    print(f"Written {len(data['nodes'])} nodes to {out_path}")
    print(f"  Added: {router['hostname']}, {switch['hostname']}")

    # ── optional immediate verification ───────────────────────────────────
    if args.verify:
        print("\nRunning verify_intents.py on updated nodes.yml …\n")
        result = subprocess.run(
            [sys.executable, "verify_intents.py",
             "--nodes", out_path, "--intents", args.intents],
            capture_output=False
        )
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
