# Batfish Network Testing Demo

An example showing how to use [Batfish](https://www.batfish.org/) for network configuration validation interactively via Jupyter.

## Project Structure

```
batfish-demo/
├── snapshot/                 # Snapshot directory
│   └── configs/              # Router/switch configuration files
│       ├── router1.cfg
│       └── router2.cfg
│   └── hosts/                # Host configuration files
│       ├── host1.json
│       └── host2.json
├── batfish_testing.ipynb     # Interactive Jupyter notebook
└── README.md
```

## Lab Topology

```
[192.168.1.0/24] -- router1 (10.0.12.1/30) ------- (10.0.12.2/30) router2 -- [192.168.2.0/24]
```

Both routers run OSPF and have ACLs blocking Telnet (TCP/23) inbound.

## Quick Start – Jupyter Notebook

### 1. Start Batfish

```bash
docker run -d -p 9997:9997 -p 9996:9996 batfish/batfish
```

### 2. Install Python dependencies

```bash
pip install pybatfish pandas jupyter
```

### 3. Launch the notebook

```bash
jupyter notebook batfish_testing.ipynb
```

Run all cells top-to-bottom. The notebook will:
- Parse your configs and report any syntax issues
- Display the routing tables
- Simulate traceroutes and assert reachability
- Verify that Telnet is blocked by ACLs
- Check for undefined/unused config structures
- Print a pass/fail test summary
