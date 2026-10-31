# This file was generated entirely by AI.
# It has not been reviewed line by line by a human.
"""Complete, synthetic v3 records shared by offline backend consumers."""
from __future__ import annotations
import copy

PASSWORD_HASH = "$6$fixture$" + "A" * 86
PASSWORD = "fixture-only-wireless-password"
BANDS = ("2g", "5g", "6g")


def document(count=1):
    records = {}
    for index in range(count):
        n = 10 + index
        prefix = f"02:ad:be:{n:02x}"
        address = lambda area, offset: prefix + f":{area:02x}:{offset:02x}"
        identity = {
            "bridge": address(0, 0),
            "ethernet": {"uplink": address(0, 16), "desk": address(0, 17)},
            "radios": {f"phy{band}": address(0, 128 + 16 * i) for i, band in enumerate(BANDS)},
            "mlds": {"office": address(2, 0)},
            "bssids": {"office": [address(2, i) for i in (1, 2, 3)]},
            "duid": "00030001" + address(0, 0).replace(":", ""),
        }
        profile = {
            "key": "office", "section": "bss_office", "iface": "wifi-office", "name": "Example-Office",
            "bands": ["2g", "5g"], "mlo": True, "network": "trusted", "encryption": "sae",
            "pmf": 2, "eht": True, "hidden": False, "roaming": True,
            "radios": ["phy2g", "phy5g"], "link_ids": [0, 1],
            "bssids": identity["bssids"]["office"][:2], "mld_mac": identity["mlds"]["office"],
        }
        uci = {"network": {
            "bridge": {"type": "device", "order": 0, "options": {"name": "br-example", "type": "bridge", "macaddr": identity["bridge"], "ports": ["uplink", "desk"]}},
            "trusted": {"type": "interface", "order": 1, "options": {"device": "br-example.137", "proto": "dhcp"}},
        }, "wireless": {}}
        for i, (port, address) in enumerate(identity["ethernet"].items()):
            uci["network"]["mac_" + port] = {"type": "device", "order": 2 + i, "options": {"name": port, "macaddr": address}}
        for i, band in enumerate(BANDS):
            uci["wireless"]["phy" + band] = {"type": "wifi-device", "order": i, "options": {
                "type": "mac80211", "path": "platform/example", "radio": str(i), "band": band,
                "channel": str((11, 149, 61)[i]), "htmode": ("EHT20", "EHT160", "EHT320")[i],
                "country": "DE", "disabled": "0", "macaddr_base": identity["radios"]["phy" + band],
            }}
        uci["wireless"][profile["section"]] = {"type": "wifi-iface", "order": 10, "options": {
            "mode": "ap", "ifname": profile["iface"], "ssid": profile["name"], "encryption": profile["encryption"],
            "key": PASSWORD, "disabled": "0", "ieee80211w": "2", "hidden": "0", "mlo": "1",
            "ieee80211k": "1", "bss_transition": "1", "network": ["trusted"], "device": profile["radios"],
            "macaddr": profile["mld_mac"], "radio_macaddr": profile["bssids"],
        }}
        records[f"node_{index}"] = {
            "hostname": f"example-node-{n}", "management_ipv4": f"203.0.113.{n}",
            "maintenance_ipv4": f"198.51.100.{n}", "ssh_port": 2222,
            "identity": identity, "platform": {
                "board_names": ["example,board"], "ports": ["uplink", "desk"], "bridge": "br-example",
                "native_vlan": 137, "guest_vlan": 345, "radio_path": "platform/example",
                "max_ap_per_radio": 16, "max_mlds": 16, "mac_prefix_groups": [],
            }, "common": {"country": "DE"}, "ssids": [profile], "peers": {"office": []},
            "services": {"example-agent": {"enable": True, "running": True, "reloadOn": {"uci": [], "files": ["/etc/example.conf"]}}},
            "uci": uci, "files": {"etc/example.conf": "explicit producer content\n"},
            "resolver": {"target": "/tmp/example-resolver.conf"},
            "credentials": {"root_password_hash": PASSWORD_HASH},
        }
    for name, node in records.items():
        node["peers"]["office"] = [{"id": other_name, "address": other["management_ipv4"], "bridge_mac": other["identity"]["bridge"], "bssids": other["ssids"][0]["bssids"]}
                                      for other_name, other in records.items() if other_name != name]
    return {"schema_version": 3, "profile": "openwrt-config-v3", "devices": records}


def single_band(value, band="5g", encryption="psk2"):
    value = copy.deepcopy(value)
    for node in value["devices"].values():
        profile = node["ssids"][0]
        index = BANDS.index(band)
        profile.update(bands=[band], mlo=False, radios=["phy" + band], link_ids=[index],
                       bssids=[node["identity"]["bssids"][profile["key"]][index]], encryption=encryption, pmf=1 if encryption != "sae" else 2)
        profile.pop("mld_mac")
        options = node["uci"]["wireless"][profile["section"]]["options"]
        options.update(device=profile["radios"][0], macaddr=profile["bssids"][0], encryption=encryption, ieee80211w=str(profile["pmf"]))
        options.pop("mlo")
        options.pop("radio_macaddr")
        node["peers"][profile["key"]] = []
    return value
