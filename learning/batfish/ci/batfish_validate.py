#!/usr/bin/env python3
"""
ci/batfish_validate.py
Batfish pre-flight validation script for use in CI/CD pipelines.
Exits with code 0 on success, non-zero on failure.
Generates batfish_report.json and batfish_junit.xml as artifacts.
"""

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pandas as pd
from pybatfish.client.session import Session
from pybatfish.datamodel import HeaderConstraints

# ── CLI args ──────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description='Batfish CI Validation')
parser.add_argument('--snapshot-dir', required=True, help='Path to config directory')
parser.add_argument('--network',      required=True, help='Batfish network name')
parser.add_argument('--snapshot-name', default='ci-snapshot', help='Snapshot name')
args = parser.parse_args()

BATFISH_HOST  = os.getenv('BATFISH_HOST', 'localhost')
SNAPSHOT_NAME = f"{args.snapshot_name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

# ── Connect ───────────────────────────────────────────────────────────────────
print(f"[+] Connecting to Batfish at {BATFISH_HOST}...")
bf = Session(host=BATFISH_HOST)
bf.set_network(args.network)
bf.init_snapshot(args.snapshot_dir, name=SNAPSHOT_NAME, overwrite=True)
print(f"[+] Snapshot '{SNAPSHOT_NAME}' loaded.")

# ── Test definitions ──────────────────────────────────────────────────────────
test_results = []

def run_test(name, fn):
    """Run a test function, catch exceptions, record result."""
    print(f"\n[TEST] {name}")
    try:
        msg = fn()
        print(f"  PASS: {msg}")
        test_results.append({'name': name, 'status': 'PASS', 'message': msg})
        return True
    except AssertionError as e:
        print(f"  FAIL: {e}")
        test_results.append({'name': name, 'status': 'FAIL', 'message': str(e)})
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        test_results.append({'name': name, 'status': 'ERROR', 'message': str(e)})
        return False


# ── Individual Tests ──────────────────────────────────────────────────────────

def test_parse_status():
    df = bf.q.fileParseStatus().answer().frame()
    failed = df[df['Status'] == 'FAILED']
    assert len(failed) == 0, f"{len(failed)} file(s) failed to parse: {failed['Filename'].tolist()}"
    return f"All {len(df)} config file(s) parsed successfully."


def test_no_undefined_references():
    df = bf.q.undefinedReferences().answer().frame()
    assert len(df) == 0, f"{len(df)} undefined reference(s) found."
    return "No undefined references."


def test_http_reachability():
    result = bf.q.traceroute(
        startLocation='router1[GigabitEthernet0/1]',
        headers=HeaderConstraints(
            srcIps='192.168.1.100',
            dstIps='192.168.2.100',
            ipProtocols=['TCP'],
            dstPorts='80'
        )
    ).answer().frame()
    for _, row in result.iterrows():
        for trace in row['Traces']:
            assert trace.disposition == 'ACCEPTED', \
                f"HTTP traffic disposition was '{trace.disposition}', expected ACCEPTED."
    return "HTTP reachability 192.168.1.x -> 192.168.2.x confirmed."


def test_telnet_blocked():
    result = bf.q.traceroute(
        startLocation='router1[GigabitEthernet0/0]',
        headers=HeaderConstraints(
            srcIps='192.168.1.100',
            dstIps='192.168.2.0/24',
            ipProtocols=['TCP'],
            dstPorts='23'
        )
    ).answer().frame()
    for _, row in result.iterrows():
        for trace in row['Traces']:
            assert trace.disposition in ('DENIED_IN', 'DENIED_OUT'), \
                f"Telnet was NOT blocked (disposition: {trace.disposition})."
    return "Telnet (TCP/23) correctly blocked."


def test_routing_completeness():
    routes = bf.q.routes().answer().frame()
    r1 = routes[routes['Node'] == 'router1']
    r2 = routes[routes['Node'] == 'router2']
    assert len(r1[r1['Network'] == '192.168.2.0/24']) > 0, \
        "router1 missing route to 192.168.2.0/24"
    assert len(r2[r2['Network'] == '192.168.1.0/24']) > 0, \
        "router2 missing route to 192.168.1.0/24"
    return "Route table completeness verified on all nodes."


# ── Run all tests ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  BATFISH CI VALIDATION")
print("=" * 60)

run_test("Config parse – no failures",        test_parse_status)
run_test("No undefined references",            test_no_undefined_references)
run_test("HTTP reachability across network",   test_http_reachability)
run_test("Telnet blocked by ACL",              test_telnet_blocked)
run_test("Routing table completeness",         test_routing_completeness)

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
passed = [t for t in test_results if t['status'] == 'PASS']
failed = [t for t in test_results if t['status'] != 'PASS']
print(f"  Results: {len(passed)} passed / {len(failed)} failed")
print("=" * 60)

# ── Write JSON report ─────────────────────────────────────────────────────────
report = {
    'network':       args.network,
    'snapshot':      SNAPSHOT_NAME,
    'timestamp':     datetime.now(timezone.utc).isoformat(),
    'passed':        len(passed),
    'failed':        len(failed),
    'tests':         test_results,
}
with open('batfish_report.json', 'w') as f:
    json.dump(report, f, indent=2)
print("JSON report written to batfish_report.json")

# ── Write JUnit XML (for GitLab test reports) ─────────────────────────────────
suite = ET.Element('testsuite', name='Batfish', tests=str(len(test_results)),
                   failures=str(len(failed)), timestamp=datetime.now(timezone.utc).isoformat())
for t in test_results:
    tc = ET.SubElement(suite, 'testcase', classname='batfish', name=t['name'])
    if t['status'] != 'PASS':
        fail = ET.SubElement(tc, 'failure', message=t['message'])
        fail.text = t['message']

ET.ElementTree(suite).write('batfish_junit.xml', xml_declaration=True, encoding='utf-8')
print("JUnit XML written to batfish_junit.xml")

# ── Exit code ─────────────────────────────────────────────────────────────────
if failed:
    print("\nPIPELINE STATUS: FAILED")
    sys.exit(1)
else:
    print("\nPIPELINE STATUS: PASSED")
    sys.exit(0)
