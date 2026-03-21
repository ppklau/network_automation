# ============================================================
# ACME Investments – Config Build Manifest
# Generated: 2026-03-21T00:31:46Z
# ============================================================

## Datacenter Configs (Arista EOS)
- datacenter/border/dc-border-01.cfg  (7792B)
- datacenter/border/dc-border-02.cfg  (7796B)
- datacenter/leaf/dc-leaf-01.cfg  (6598B)
- datacenter/leaf/dc-leaf-02.cfg  (6598B)
- datacenter/leaf/dc-leaf-03.cfg  (6237B)
- datacenter/leaf/dc-leaf-04.cfg  (6237B)
- datacenter/spine/dc-spine-01.cfg  (4385B)
- datacenter/spine/dc-spine-02.cfg  (4390B)

## Branch Configs (Cisco IOS)
- branch/london/br-lon-rtr-01.cfg  (4341B)
- branch/london/br-lon-sw-01.cfg  (3851B)

## VLAN Summary
| VLAN | Name            | Zone      | VNI   | VRF           |
|------|-----------------|-----------|-------|---------------|
|  100 | CORP_DATA       | corporate | 10100 | VRF_CORPORATE |
|  101 | CORP_VOICE      | corporate | 10101 | VRF_CORPORATE |
|  102 | CORP_MGMT       | corporate | 10102 | VRF_CORPORATE |
|  200 | TRADING_PROD    | trading   | 10200 | VRF_TRADING |
|  201 | TRADING_MARKET_DATA | trading   | 10201 | VRF_TRADING |
|  202 | TRADING_RISK    | trading   | 10202 | VRF_TRADING |
|  203 | TRADING_DR      | trading   | 10203 | VRF_TRADING |
|  300 | DMZ_WEB         | dmz       | 10300 | VRF_DMZ |
|  301 | DMZ_PARTNER     | dmz       | 10301 | VRF_DMZ |
|  302 | DMZ_MONITORING  | dmz       | 10302 | VRF_DMZ |
