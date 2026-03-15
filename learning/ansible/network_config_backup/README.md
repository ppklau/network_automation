# Network Config Backup Playbook

Overview
--------
`backup_playbook.yml` pulls the running configuration from devices in the `routers` inventory group, saves a filtered copy locally, compares it to the previously saved "latest" config, and writes a diff report when changes are detected.

Requirements
------------
- Ansible 2.9+ (or newer)
- `cisco.ios` collection
	- Install with: `ansible-galaxy collection install cisco.ios`
- A working inventory that defines a `routers` group and provides connection/credential variables (for example `ansible_user`, `ansible_password`, `ansible_network_os: cisco.ios`, and `ansible_connection: network_cli`). See `inventory.ini` in this folder for an example.

What it produces
-----------------
- `./configs/<inventory_hostname>_latest.cfg` — the last saved canonical config
- `./reports/<inventory_hostname>_report.txt` — unified diff report when changes are detected

How it works (high level)
-------------------------
1. Creates `./configs` and `./reports` directories and a baseline `*_latest.cfg` file if missing.
2. Uses `cisco.ios.ios_command` to fetch `show running-config` from each device.
3. Saves a filtered copy as `./configs/<host>_new.cfg` on the controller.
4. Runs `diff -u` between the current `*_latest.cfg` and the new config; if different, writes a human-readable report to `./reports/<host>_report.txt` and replaces the `*_latest.cfg` with the new file.
5. Removes the transient `*_new.cfg` file.

Running the playbook
--------------------
From this directory run:

```bash
ansible-playbook -i inventory.ini backup_playbook.yml
```

If you need to specify credentials or escalate, pass the usual CLI flags (for example `-u`, `--ask-pass`, `--ask-become-pass`, or use an Ansible Vault file).

Example cron entry (runs nightly at 02:00):

```cron
0 2 * * * cd /Users/ppklau/stuff/ppklau/Development/github/network_automation/learning/ansible/network_config_backup && ansible-playbook -i inventory.ini backup_playbook.yml >> backup.log 2>&1
```

Troubleshooting
---------------
- If the playbook fails to connect, verify `inventory.ini` connection vars and that devices accept the chosen method (SSH/enable). 
- If the `cisco.ios` module is missing, install the collection with `ansible-galaxy collection install cisco.ios`.
- Check permissions on `./configs` and `./reports` if files cannot be created.

Notes
-----
- The playbook runs the diff and writes a report only when content changes — this keeps storage and noise low.
- The current implementation filters out trailing sections introduced by device prompts; adjust the regex in the playbook if your device output differs.

Files
-----
- `backup_playbook.yml` — main playbook (this repo)
- `inventory.ini` — sample inventory for the `routers` group

License
-------
See top-level LICENSE file in the repository.
