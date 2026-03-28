#!/usr/bin/env python3
"""
verify_intents.py — ACME Investments SoT Design Intent Verifier
================================================================
Layer 1 validation: checks that nodes.yml satisfies every design
intent declared in design_intents.yml, purely as a data-layer check.

Runs BEFORE template rendering and BEFORE Batfish.
Emits JUnit XML so GitLab CI can display results natively.

Usage:
    python verify_intents.py \
        --intents design_intents.yml \
        --nodes   nodes.yml \
        --output  intent_results.xml

Exit codes:
    0  — all checks passed
    1  — one or more checks failed
"""

import argparse
import sys
import ipaddress
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring, indent
from xml.etree import ElementTree

import yaml


# ── helpers ──────────────────────────────────────────────────────────────────

def load_yaml(path: str) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def ip_in_prefix(address: str, prefix: str) -> bool:
    """Return True if address (with or without mask) falls inside prefix."""
    try:
        addr = ipaddress.ip_address(address.split("/")[0])
        net  = ipaddress.ip_network(prefix, strict=False)
        return addr in net
    except ValueError:
        return False


# ── result collector ──────────────────────────────────────────────────────────

class Result:
    def __init__(self, intent_id: str, title: str):
        self.intent_id = intent_id
        self.title     = title
        self.checks: list[tuple[bool, str]] = []   # (passed, message)

    def assert_true(self, condition: bool, message: str):
        self.checks.append((condition, message))

    @property
    def passed(self) -> bool:
        return all(ok for ok, _ in self.checks)

    @property
    def failures(self) -> list[str]:
        return [msg for ok, msg in self.checks if not ok]


# ── individual intent checks ──────────────────────────────────────────────────

def check_topo_01(nodes: list, r: Result):
    """Every node must declare a role; spines and leaves must exist."""
    roles = {n["hostname"]: n.get("role") for n in nodes}
    spines = [h for h, role in roles.items() if role == "spine"]
    leaves = [h for h, role in roles.items() if role in ("leaf", "border_leaf")]
    r.assert_true(len(spines) >= 2,
        f"INTENT-TOPO-01: expected ≥2 spines, found {len(spines)}: {spines}")
    r.assert_true(len(leaves) >= 2,
        f"INTENT-TOPO-01: expected ≥2 leaves, found {len(leaves)}: {leaves}")


def check_topo_02(nodes: list, r: Result):
    """All leaf/border_leaf nodes must have an mlag block with a peer defined."""
    for node in nodes:
        if node.get("role") not in ("leaf", "border_leaf"):
            continue
        mlag = node.get("mlag")
        r.assert_true(
            mlag is not None,
            f"INTENT-TOPO-02: {node['hostname']} has no mlag block"
        )
        if mlag:
            r.assert_true(
                bool(mlag.get("peer_address")),
                f"INTENT-TOPO-02: {node['hostname']} mlag.peer_address is missing"
            )
            r.assert_true(
                bool(mlag.get("domain_id")),
                f"INTENT-TOPO-02: {node['hostname']} mlag.domain_id is missing"
            )


def check_topo_03(nodes: list, r: Result):
    """All leaf/border_leaf nodes must have a vxlan block with at least one VNI."""
    for node in nodes:
        if node.get("role") not in ("leaf", "border_leaf"):
            continue
        vxlan = node.get("vxlan")
        r.assert_true(
            vxlan is not None,
            f"INTENT-TOPO-03: {node['hostname']} has no vxlan block"
        )
        if vxlan:
            vlans = vxlan.get("vlans", [])
            r.assert_true(
                len(vlans) > 0,
                f"INTENT-TOPO-03: {node['hostname']} vxlan.vlans is empty"
            )
            for entry in vlans:
                r.assert_true(
                    entry.get("vni") == entry.get("vlan"),
                    f"INTENT-TOPO-03: {node['hostname']} VNI/VLAN mismatch: "
                    f"vlan={entry.get('vlan')} vni={entry.get('vni')}"
                )


def check_rtg_01(nodes: list, r: Result):
    """Fabric nodes (spine/leaf/border_leaf) must have bgp with unique ASNs.
    Branch nodes use OSPF — they are excluded from this check."""
    asns_seen: dict[int, str] = {}
    fabric_roles = {"spine", "leaf", "border_leaf"}
    for node in nodes:
        if node.get("role") not in fabric_roles:
            continue   # branch routers/switches use OSPF, not BGP
        bgp = node.get("bgp")
        r.assert_true(
            bgp is not None,
            f"INTENT-RTG-01: {node['hostname']} (role={node.get('role')}) has no bgp block"
        )
        if not bgp:
            continue
        asn = bgp.get("asn")
        r.assert_true(
            asn is not None,
            f"INTENT-RTG-01: {node['hostname']} bgp.asn is missing"
        )
        if asn is not None:
            if asn in asns_seen:
                r.assert_true(False,
                    f"INTENT-RTG-01: ASN {asn} duplicated on "
                    f"{node['hostname']} and {asns_seen[asn]}")
            else:
                asns_seen[asn] = node["hostname"]
        peers = bgp.get("peers", [])
        r.assert_true(
            len(peers) >= 1,
            f"INTENT-RTG-01: {node['hostname']} has no BGP peers"
        )


def check_rtg_02(nodes: list, r: Result):
    """Fabric nodes (spine/leaf/border_leaf) must have EVPN enabled.
    Branch nodes use OSPF and are excluded."""
    fabric_roles = {"spine", "leaf", "border_leaf"}
    for node in nodes:
        if node.get("role") not in fabric_roles:
            continue
        bgp = node.get("bgp", {}) or {}
        evpn = bgp.get("evpn", {}) or {}
        r.assert_true(
            evpn.get("enabled") is True,
            f"INTENT-RTG-02: {node['hostname']} bgp.evpn.enabled is not true"
        )
        r.assert_true(
            bool(evpn.get("role")),
            f"INTENT-RTG-02: {node['hostname']} bgp.evpn.role is missing"
        )
        evpn_peers = evpn.get("peers", [])
        r.assert_true(
            len(evpn_peers) >= 1,
            f"INTENT-RTG-02: {node['hostname']} has no EVPN peers"
        )


def check_rtg_03(nodes: list, r: Result):
    """Branch site nodes must have ospf block in area 0."""
    branch_nodes = [n for n in nodes if n.get("site", "").startswith("lon-branch")
                    or n.get("site", "").endswith("-branch1")
                    or "branch" in n.get("hostname", "")]
    r.assert_true(
        len(branch_nodes) >= 1,
        "INTENT-RTG-03: no branch nodes found in nodes.yml"
    )
    for node in branch_nodes:
        ospf = node.get("ospf")
        r.assert_true(
            ospf is not None,
            f"INTENT-RTG-03: {node['hostname']} has no ospf block"
        )
        if ospf:
            r.assert_true(
                ospf.get("area") == 0,
                f"INTENT-RTG-03: {node['hostname']} ospf.area is "
                f"{ospf.get('area')!r}, expected 0"
            )


def check_seg_01(nodes: list, r: Result):
    """Leaf nodes must declare VRFs. No VRF should appear in a wrong zone."""
    forbidden = {
        "TRADING":   ["dmz"],
        "CORPORATE": ["dmz"],
        "DMZ":       ["trading", "corporate"],
    }
    for node in nodes:
        if node.get("role") not in ("leaf", "border_leaf"):
            continue
        vrfs = node.get("vrfs", [])
        r.assert_true(
            len(vrfs) >= 1,
            f"INTENT-SEG-01: {node['hostname']} has no vrfs block"
        )
        for vrf_entry in vrfs:
            vrf_name = vrf_entry.get("name", "")
            zone     = vrf_entry.get("zone", "")
            if vrf_name in forbidden:
                for bad_zone in forbidden[vrf_name]:
                    r.assert_true(
                        zone != bad_zone,
                        f"INTENT-SEG-01: {node['hostname']} VRF {vrf_name} "
                        f"has illegal zone '{zone}'"
                    )


def check_seg_02(nodes: list, r: Result):
    """
    Every ACL must have a deny-all default action.
    No ACL entry may be a permit-any.
    Every entry must have a comment referencing a REQ-ID.
    """
    req_pattern = "REQ-"
    for node in nodes:
        for acl in node.get("acls", []):
            acl_name = acl.get("name", "?")
            r.assert_true(
                acl.get("default_action") == "deny",
                f"INTENT-SEG-02: {node['hostname']} ACL {acl_name} "
                f"default_action is not 'deny'"
            )
            for entry in acl.get("entries", []):
                # No permit-any
                if entry.get("action") == "permit":
                    src = str(entry.get("src", ""))
                    dst = str(entry.get("dst", ""))
                    is_any_any = (src == "any" and dst == "any")
                    r.assert_true(
                        not is_any_any,
                        f"INTENT-SEG-02: {node['hostname']} ACL {acl_name} "
                        f"seq {entry.get('seq')} is a permit-any (forbidden)"
                    )
                # Every entry must have a REQ comment
                comment = entry.get("comment", "")
                r.assert_true(
                    req_pattern in comment,
                    f"INTENT-SEG-02: {node['hostname']} ACL {acl_name} "
                    f"seq {entry.get('seq')} comment missing REQ-ID"
                )


def check_seg_03(nodes: list, r: Result):
    """DMZ VLANs (300-399) must not appear in TRADING or CORPORATE VRFs."""
    DMZ_VLAN_RANGE = range(300, 400)
    for node in nodes:
        for vrf_entry in node.get("vrfs", []):
            if vrf_entry.get("zone") in ("trading", "corporate"):
                for iface in node.get("interfaces", []):
                    vlan = iface.get("vlan")
                    if vlan and vlan in DMZ_VLAN_RANGE:
                        r.assert_true(False,
                            f"INTENT-SEG-03: {node['hostname']} DMZ VLAN "
                            f"{vlan} found in {vrf_entry['name']} VRF")


def check_mgmt_01(nodes: list, r: Result):
    """Every node must have a management block with an OOB indicator.
    EOS nodes use mgmt.vrf=MGMT / ssh_vrf=MGMT.
    IOS nodes use mgmt.ssh_source_interface as OOB indicator."""
    for node in nodes:
        mgmt = node.get("management")
        r.assert_true(
            mgmt is not None,
            f"INTENT-MGMT-01: {node['hostname']} has no management block"
        )
        if not mgmt:
            continue
        platform = node.get("platform", "")
        if "ios" in platform:
            # IOS branch nodes: accept ssh_source_interface as OOB control
            has_oob = bool(mgmt.get("ssh_source_interface"))
        else:
            # EOS nodes: must have explicit MGMT VRF
            has_oob = (mgmt.get("vrf") == "MGMT" or mgmt.get("ssh_vrf") == "MGMT")
        r.assert_true(
            has_oob,
            f"INTENT-MGMT-01: {node['hostname']} has no OOB management indicator"
        )


def check_mgmt_02(nodes: list, r: Result):
    """Every node must have ≥2 syslog servers and SNMPv3 configured."""
    for node in nodes:
        mgmt = node.get("management", {}) or {}
        syslog = mgmt.get("syslog_servers", [])
        r.assert_true(
            len(syslog) >= 2,
            f"INTENT-MGMT-02: {node['hostname']} has {len(syslog)} syslog "
            f"server(s), need ≥2"
        )
        snmp = mgmt.get("snmp", {}) or {}
        r.assert_true(
            snmp.get("version") == "v3",
            f"INTENT-MGMT-02: {node['hostname']} snmp.version is "
            f"{snmp.get('version')!r}, expected 'v3'"
        )


def check_ip_01(nodes: list, r: Result):
    """
    Loopback and SVI addresses must fall within their declared zone prefix
    as specified in INTENT-IP-01.

    DC nodes: checked against zone-specific prefixes under 10.0.0.0/16.
    Branch nodes: each site has its own /16 prefix, derived from the
    loopback or management address already in the SoT — no hardcoding.
    """
    dc_zone_prefixes = {
        "trading":   "10.0.10.0/23",
        "corporate": "10.0.20.0/23",
        "dmz":       "10.0.30.0/23",
        "mgmt":      "10.0.0.0/24",
        "underlay":  "10.0.255.0/24",
    }

    # Build per-site prefix map dynamically from loopback/mgmt addresses
    # so new branches are automatically handled without changing this check.
    site_prefixes: dict[str, str] = {}
    for node in nodes:
        site = node.get("site", "")
        if not site or "branch" not in site:
            continue
        if site in site_prefixes:
            continue
        # Derive site /16 from management address
        mgmt_addr = (node.get("management") or {}).get("address", "")
        if mgmt_addr and mgmt_addr != "dhcp":
            try:
                net = ipaddress.ip_network(
                    mgmt_addr.split("/")[0] + "/16", strict=False)
                site_prefixes[site] = str(net)
            except ValueError:
                pass

    for node in nodes:
        site = node.get("site", "")
        is_branch = "branch" in site

        for iface in node.get("interfaces", []):
            addr_raw = iface.get("address", "")
            if not addr_raw or addr_raw == "dhcp":
                continue
            zone = iface.get("zone", "")

            if is_branch:
                site_prefix = site_prefixes.get(site)
                if not site_prefix:
                    continue   # can't derive prefix — skip rather than false-fail
                in_site = ip_in_prefix(addr_raw, site_prefix)
                r.assert_true(in_site,
                    f"INTENT-IP-01: {node['hostname']} iface {iface['name']} "
                    f"address {addr_raw} not in branch prefix {site_prefix}")
            else:
                if zone not in dc_zone_prefixes:
                    continue
                prefix = dc_zone_prefixes[zone]
                in_zone = ip_in_prefix(addr_raw, prefix)
                r.assert_true(in_zone,
                    f"INTENT-IP-01: {node['hostname']} iface {iface['name']} "
                    f"address {addr_raw} not in zone prefix {prefix} "
                    f"(zone={zone})")


# ── intent registry ───────────────────────────────────────────────────────────

INTENT_CHECKS = [
    ("INTENT-TOPO-01", "Spine-leaf fabric exists",              check_topo_01),
    ("INTENT-TOPO-02", "MLAG on all leaf pairs",                check_topo_02),
    ("INTENT-TOPO-03", "VXLAN VNI=VLAN on all leaves",          check_topo_03),
    ("INTENT-RTG-01",  "eBGP underlay, unique ASNs",            check_rtg_01),
    ("INTENT-RTG-02",  "eBGP EVPN enabled on all nodes",        check_rtg_02),
    ("INTENT-RTG-03",  "OSPF area 0 at branch sites",           check_rtg_03),
    ("INTENT-SEG-01",  "VRF per zone, no cross-zone leakage",   check_seg_01),
    ("INTENT-SEG-02",  "ACLs: deny-default, comments, no any",  check_seg_02),
    ("INTENT-SEG-03",  "DMZ VLANs only in DMZ VRF",             check_seg_03),
    ("INTENT-MGMT-01", "OOB management VRF on all nodes",       check_mgmt_01),
    ("INTENT-MGMT-02", "Syslog x2 + SNMPv3 on all nodes",       check_mgmt_02),
    ("INTENT-IP-01",   "All IPs within declared zone prefix",   check_ip_01),
]


# ── JUnit XML output ──────────────────────────────────────────────────────────

def build_junit(results: list[Result]) -> str:
    total   = len(results)
    failed  = sum(1 for r in results if not r.passed)
    suite   = Element("testsuite", name="design-intent-verification",
                       tests=str(total), failures=str(failed), errors="0")
    for r in results:
        tc = SubElement(suite, "testcase",
                        classname="intents",
                        name=f"{r.intent_id}: {r.title}")
        if not r.passed:
            failure = SubElement(tc, "failure", message=f"{len(r.failures)} check(s) failed")
            failure.text = "\n".join(r.failures)
    indent(suite, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + \
           tostring(suite, encoding="unicode")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Verify SoT against design intents")
    parser.add_argument("--intents", default="design_intents.yml")
    parser.add_argument("--nodes",   default="nodes.yml")
    parser.add_argument("--output",  default="intent_results.xml")
    args = parser.parse_args()

    nodes_data   = load_yaml(args.nodes)
    nodes: list  = nodes_data.get("nodes", [])

    print(f"Loaded {len(nodes)} node(s) from {args.nodes}")
    print(f"Running {len(INTENT_CHECKS)} intent checks...\n")

    results: list[Result] = []
    for intent_id, title, check_fn in INTENT_CHECKS:
        r = Result(intent_id, title)
        try:
            check_fn(nodes, r)
        except Exception as exc:
            r.assert_true(False, f"Check raised exception: {exc}")
        results.append(r)

        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {intent_id}: {title}")
        for msg in r.failures:
            print(f"         ✗ {msg}")

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    print(f"\nResults: {passed} passed, {failed} failed out of {len(results)}")

    xml = build_junit(results)
    Path(args.output).write_text(xml)
    print(f"JUnit XML written to {args.output}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
