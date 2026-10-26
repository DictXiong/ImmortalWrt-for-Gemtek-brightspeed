#!/usr/bin/env python3
"""Strict, local-only DictTech AP v1 configuration resolution and rendering.

JSON is the interchange format: temporary files and future Nix+sops producers
use exactly the same document. This module does not log secrets or contact APs.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any

PROFILE = "dicttech-ap-v1"
SCHEMA_VERSION = 1
MAX_JSON_BYTES = 512 * 1024
ROLES = ("main", "legacy", "guest", "iot")
ROAM_ROLES = {"main": "main", "legacy": "battalion", "guest": "guest", "iot": "iot"}
SECTIONS = {"main": "dicttech_mlo", "legacy": "default_radio1", "guest": "guest_mlo", "iot": "wifinet1"}
IFACES = {"main": "ap-mld1", "legacy": "ap-mld0", "guest": "ap-mld2", "iot": "phy0.0-ap1"}
REPO = Path(__file__).resolve().parents[2]
DEFAULTS = REPO / "package/dicttech/ap-profile/files/usr/share/dicttech-ap/defaults.json"
TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,47}$")
HOSTNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
MAC = re.compile(r"^[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}$")
HASH = re.compile(r"^\$(5|6)\$(?:rounds=([0-9]{4,9})\$)?([./A-Za-z0-9]{1,16})\$([./A-Za-z0-9]+)$")


class ConfigError(ValueError):
    """A safe-to-display, value-free configuration validation failure."""


def fail(path: str, message: str) -> None:
    raise ConfigError(f"{path}: {message}")


def obj(value: Any, path: str, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        fail(path, "must be an object")
    if set(value) - allowed:
        fail(path, "unknown field(s): " + ", ".join(sorted(set(value) - allowed)))
    if required - set(value):
        fail(path, "missing required field(s): " + ", ".join(sorted(required - set(value))))
    return value


def text(value: Any, path: str, minimum: int = 1, maximum: int = 255) -> str:
    try:
        length = len(value.encode("utf-8")) if isinstance(value, str) else -1
    except UnicodeError:
        fail(path, "must contain valid Unicode")
    if not minimum <= length <= maximum:
        fail(path, f"must be a UTF-8 string of {minimum}–{maximum} bytes")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        fail(path, "control characters are not allowed")
    return value


def integer(value: Any, path: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        fail(path, f"must be an integer between {low} and {high}")
    return value


def boolean(value: Any, path: str) -> bool:
    if type(value) is not bool:
        fail(path, "must be boolean")
    return value


def mac(value: Any, path: str) -> str:
    if not isinstance(value, str) or not MAC.fullmatch(value):
        fail(path, "must be an Ethernet MAC address")
    value = value.lower()
    if int(value[0:2], 16) & 1 or value == "00:00:00:00:00:00":
        fail(path, "must be a nonzero unicast MAC address")
    return value


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("JSON: duplicate object field")
        result[key] = value
    return result


def _read(path: str | Path, secret: bool = False) -> dict:
    path = Path(path)
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_JSON_BYTES:
        fail("JSON", "input must be a regular file no larger than 512 KiB")
    if secret and metadata.st_mode & 0o077:
        fail("JSON", "a file containing secrets must have mode 0600 or stricter")
    try:
        value = json.loads(path.read_text(), object_pairs_hook=no_duplicates,
                           parse_constant=lambda _: fail("JSON", "non-finite numbers are not allowed"))
    except (UnicodeError, json.JSONDecodeError):
        fail("JSON", "invalid UTF-8 JSON document")
    if not isinstance(value, dict):
        fail("JSON", "document must be an object")
    return value


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def deep_merge(default: dict, override: dict) -> dict:
    result = copy.deepcopy(default)
    for key, value in override.items():
        result[key] = deep_merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else copy.deepcopy(value)
    return result


def validate_delta(delta: Any, path: str = "common") -> None:
    obj(delta, path, {"country", "ssh_port", "timezone", "zonename", "ntp_servers", "radios", "ssids", "roaming"})
    if "country" in delta:
        if delta["country"] not in ("US", "CN"):
            fail(path + ".country", "supported profile country codes are US and CN")
    if "ssh_port" in delta:
        integer(delta["ssh_port"], path + ".ssh_port", 1, 65535)
    for key in ("timezone", "zonename"):
        if key in delta:
            text(delta[key], path + "." + key, 1, 64)
    if "ntp_servers" in delta:
        values = delta["ntp_servers"]
        if not isinstance(values, list) or not 1 <= len(values) <= 8:
            fail(path + ".ntp_servers", "must contain 1–8 server names")
        for value in values:
            text(value, path + ".ntp_servers", 1, 253)
            if not re.fullmatch(r"[A-Za-z0-9.:-]+", value):
                fail(path + ".ntp_servers", "invalid server name")
    if "radios" in delta:
        obj(delta["radios"], path + ".radios", {"radio0", "radio1", "radio2"})
        for radio, values in delta["radios"].items():
            where = path + ".radios." + radio
            obj(values, where, {"channel", "htmode", "force_40"} if radio == "radio0" else {"channel", "htmode"})
            if "channel" in values:
                channel = values["channel"]
                integer(channel, where + ".channel", 1, 233)
                choices = range(1, 12) if radio == "radio0" else range(36, 65, 4) if radio == "radio1" else range(1, 234, 4)
                if channel not in choices:
                    fail(where + ".channel", "channel is outside this profile's allowed set")
            if "htmode" in values:
                allowed = {"EHT20", "EHT40"} if radio == "radio0" else {"EHT20", "EHT40", "EHT80", "EHT160"}
                if radio == "radio2":
                    allowed.add("EHT320")
                if values["htmode"] not in allowed:
                    fail(where + ".htmode", "unsupported channel width")
            if "force_40" in values:
                boolean(values["force_40"], where + ".force_40")
    if "ssids" in delta:
        obj(delta["ssids"], path + ".ssids", set(ROLES))
        for role, values in delta["ssids"].items():
            where = path + ".ssids." + role
            obj(values, where, {"name", "six_ghz"} if role == "main" else {"name", "hidden"} if role == "iot" else {"name"})
            if "name" in values:
                text(values["name"], where + ".name", 1, 32)
            for key in ("six_ghz", "hidden"):
                if key in values:
                    boolean(values[key], where + "." + key)
    if "roaming" in delta:
        values = obj(delta["roaming"], path + ".roaming", {"enabled", "interval", "ttl"})
        if "enabled" in values:
            boolean(values["enabled"], path + ".roaming.enabled")
        if "interval" in values:
            integer(values["interval"], path + ".roaming.interval", 30, 300)
        if "ttl" in values:
            integer(values["ttl"], path + ".roaming.ttl", 180, 1800)


def _identity(device: dict, path: str) -> dict:
    base = mac(device["mac_base"], path + ".mac_base")
    number = int(base.replace(":", ""), 16)
    def derived(offset):
        n = number + offset
        if n >= 1 << 48:
            fail(path + ".mac_base", "derived MAC range would overflow")
        data = bytearray(n.to_bytes(6, "big"))
        data[0] = (data[0] | 2) & 254
        return ":".join(f"{v:02x}" for v in data)
    result = {"bridge": base,
              "ethernet": {"wan": derived(1), "lan2": base, "lan3": base, "lan4": base, "cpu": derived(4)},
              "radios": {"radio0": derived(0x10), "radio1": derived(0x20), "radio2": derived(0x30)},
              "mlds": {"legacy": derived(0x40), "main": derived(0x50), "guest": derived(0x60)},
              "bssids": {"legacy": [derived(0x41), derived(0x42)], "main": [derived(0x50), derived(0x52), derived(0x53)],
                          "guest": [derived(0x60), derived(0x62)], "iot": [derived(0x70)]},
              "duid": "00030001" + base.replace(":", "")}
    override = obj(device.get("identity", {}), path + ".identity", set(result))
    for key in ("ethernet", "radios", "mlds", "bssids"):
        if key in override:
            obj(override[key], path + ".identity." + key, set(result[key]))
    result = deep_merge(result, override)
    for key in ("bridge",):
        result[key] = mac(result[key], path + ".identity." + key)
    for group in ("ethernet", "radios", "mlds"):
        result[group] = {key: mac(value, path + ".identity." + group + "." + key) for key, value in result[group].items()}
    all_bssids = []
    for role, count in (("main", 3), ("legacy", 2), ("guest", 2), ("iot", 1)):
        values = result["bssids"][role]
        if not isinstance(values, list) or len(values) != count:
            fail(path + ".identity.bssids." + role, f"must contain exactly {count} MAC address(es)")
        result["bssids"][role] = [mac(value, path + ".identity.bssids." + role) for value in values]
        all_bssids.extend(result["bssids"][role])
    if len(set(all_bssids)) != len(all_bssids):
        fail(path + ".identity.bssids", "different BSSes must have different addresses")
    if result["bridge"] in all_bssids:
        fail(path + ".identity", "bridge address collides with a BSS")
    if len(set(result["mlds"].values())) != len(result["mlds"]) or result["bridge"] in result["mlds"].values():
        fail(path + ".identity.mlds", "MLD addresses must be distinct and separate from the bridge")
    for role, address in result["mlds"].items():
        if address in all_bssids and address != result["bssids"][role][0]:
            fail(path + ".identity.mlds", "an MLD may share only its own first-link BSSID")
    duid = result["duid"]
    if not isinstance(duid, str) or not re.fullmatch(r"[0-9a-fA-F]{8,128}", duid) or len(duid) % 2:
        fail(path + ".identity.duid", "must be an even-length hexadecimal DHCP DUID")
    result["duid"] = duid.lower()
    return result


def _password_hash(value: Any, path: str) -> str:
    if not isinstance(value, str):
        fail(path, "must be a SHA-512 or SHA-256 crypt password hash")
    match = HASH.fullmatch(value)
    if not match or len(match[4]) != (86 if match[1] == "6" else 43):
        fail(path, "must be a supported SHA-512 or SHA-256 crypt password hash")
    if match[2] and not 1000 <= int(match[2]) <= 999999999:
        fail(path, "invalid crypt rounds")
    return value


def _authorized_keys(values: Any, path: str) -> list[str]:
    if not isinstance(values, list) or len(values) > 32:
        fail(path, "must be an array of at most 32 public keys")
    result = []
    kinds = {"ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com"}
    for value in values:
        text(value, path, 1, 16384)
        parts = value.split()
        if len(parts) < 2 or parts[0] not in kinds:
            fail(path, "expected a public key without authorized_keys options")
        try:
            raw = base64.b64decode(parts[1], validate=True)
        except (ValueError, TypeError):
            fail(path, "invalid public-key encoding")
        if len(raw) < 16:
            fail(path, "invalid public-key encoding")
        kind_length = int.from_bytes(raw[:4], "big")
        if kind_length != len(parts[0]) or raw[4:4 + kind_length] != parts[0].encode() or len(raw) < 4 + kind_length + 8:
            fail(path, "public-key type does not match its wire encoding")
        if parts[0] == "ssh-ed25519":
            start = 4 + kind_length
            if int.from_bytes(raw[start:start + 4], "big") != 32 or len(raw) != start + 36:
                fail(path, "invalid Ed25519 public key")
        result.append(value)
    if len(result) != len(set(result)):
        fail(path, "duplicate public keys")
    return result


def _validate(document: Any) -> None:
    obj(document, "document", {"schema_version", "profile", "common", "devices", "secrets"}, {"schema_version", "profile", "devices", "secrets"})
    if type(document["schema_version"]) is not int or document["schema_version"] != SCHEMA_VERSION:
        fail("schema_version", "unsupported version")
    if document["profile"] != PROFILE:
        fail("profile", "unsupported profile")
    validate_delta(document.get("common", {}))
    devices = document["devices"]
    if not isinstance(devices, dict) or not 1 <= len(devices) <= 16:
        fail("devices", "a v1 fleet must contain between 1 and 16 devices")
    secrets = obj(document["secrets"], "secrets", {"wifi", "devices"}, {"wifi", "devices"})
    wifi = obj(secrets["wifi"], "secrets.wifi", set(ROLES), set(ROLES))
    for role, password in wifi.items():
        text(password, "secrets.wifi." + role, 8, 63)
    credentials = obj(secrets["devices"], "secrets.devices", set(devices), set(devices))
    for node, device in devices.items():
        if not isinstance(node, str) or not TOKEN.fullmatch(node):
            fail("devices", "device IDs must be UCI-safe tokens, start with a letter, and contain only letters, digits or underscore")
        if node in {"main", "battalion", "guest", "iot"}:
            fail("devices", "device ID collides with a reserved roaming section name")
        path = "devices." + node
        obj(device, path, {"hostname", "maintenance_ipv4", "mac_base", "identity", "legacy_ssid", "overrides"}, {"hostname", "maintenance_ipv4", "mac_base"})
        if not isinstance(device["hostname"], str) or not HOSTNAME.fullmatch(device["hostname"]):
            fail(path + ".hostname", "invalid hostname")
        try:
            address = ipaddress.IPv4Address(device["maintenance_ipv4"])
        except (ValueError, TypeError):
            fail(path + ".maintenance_ipv4", "must be a plain IPv4 address")
        if address not in ipaddress.IPv4Network("192.168.250.0/24") or not 1 <= int(str(address).rsplit(".", 1)[1]) <= 99:
            fail(path + ".maintenance_ipv4", "must be a registered address from 192.168.250.1 through .99")
        if "legacy_ssid" in device:
            text(device["legacy_ssid"], path + ".legacy_ssid", 1, 32)
        validate_delta(device.get("overrides", {}), path + ".overrides")
        _identity(device, path)
        secret = obj(credentials[node], "secrets.devices." + node, {"root_password_hash", "authorized_keys"}, {"root_password_hash"})
        _password_hash(secret["root_password_hash"], "secrets.devices." + node + ".root_password_hash")
        if "authorized_keys" in secret:
            _authorized_keys(secret["authorized_keys"], "secrets.devices." + node + ".authorized_keys")


def load_document(path: str | Path) -> dict:
    document = _read(path, secret=True)
    _validate(document)
    return document


def _defaults(path: str | Path | None) -> tuple[dict, str]:
    source = Path(path) if path else DEFAULTS
    data = source.read_bytes()
    value = json.loads(data, object_pairs_hook=no_duplicates)
    if value.get("profile") != PROFILE or value.get("schema_version") != SCHEMA_VERSION:
        fail("defaults", "unsupported profile or schema version")
    return value, hashlib.sha256(data).hexdigest()


def _resolved_nodes(document: dict, defaults: dict) -> dict:
    nodes = {}
    seen_hostnames, seen_addresses, seen_macs, seen_duids = {}, {}, {}, {}
    for node, device in document["devices"].items():
        common = deep_merge(defaults["common"], document.get("common", {}))
        common = deep_merge(common, device.get("overrides", {}))
        if "legacy_ssid" in device:
            common["ssids"]["legacy"]["name"] = device["legacy_ssid"]
        names = [common["ssids"][role]["name"] for role in ROLES]
        if len(set(names)) != len(names):
            fail("devices." + node, "the four SSIDs must have distinct names")
        if common["country"] == "CN" and common["ssids"]["main"]["six_ghz"]:
            fail("devices." + node, "CN profile requires main.six_ghz=false")
        if common["roaming"]["ttl"] < 3 * common["roaming"]["interval"]:
            fail("devices." + node + ".roaming", "TTL must cover at least three check intervals")
        if common["radios"]["radio0"]["force_40"] and common["radios"]["radio0"]["htmode"] != "EHT40":
            fail("devices." + node + ".radios.radio0", "force_40 requires EHT40")
        for key, table in (("hostname", seen_hostnames), ("maintenance_ipv4", seen_addresses)):
            value = device[key].lower() if key == "hostname" else device[key]
            if value in table:
                fail("devices", "duplicate " + key)
            table[value] = node
        identity = _identity(device, "devices." + node)
        if identity["duid"] in seen_duids:
            fail("devices", "DHCP DUID is shared by different devices")
        seen_duids[identity["duid"]] = node
        addresses = {identity["bridge"], *identity["ethernet"].values(), *identity["radios"].values(), *identity["mlds"].values()}
        for values in identity["bssids"].values():
            addresses.update(values)
        for address in addresses:
            if address in seen_macs:
                fail("devices", "MAC identity is shared by different devices")
            seen_macs[address] = node
        normalized = copy.deepcopy(device)
        normalized["mac_base"] = mac(device["mac_base"], "devices." + node + ".mac_base")
        normalized["identity"] = identity
        normalized["legacy_ssid"] = common["ssids"]["legacy"]["name"]
        normalized.pop("overrides", None)
        nodes[node] = {"device": normalized, "common": common}
    # Same names must describe the same ESS, including security and credentials.
    ess = {}
    for node, resolved in nodes.items():
        for role in ROLES:
            name = resolved["common"]["ssids"][role]["name"]
            signature = (role, defaults["security"][role]["encryption"], defaults["security"][role]["pmf"], document["secrets"]["wifi"][role])
            if name in ess and ess[name] != signature:
                fail("devices", "a shared SSID has inconsistent security or password")
            ess[name] = signature
    return nodes


def resolve(document: dict, device_id: str, defaults_path: str | Path | None = None) -> dict:
    _validate(document)
    defaults, digest = _defaults(defaults_path)
    nodes = _resolved_nodes(document, defaults)
    if device_id not in nodes:
        fail("device", "unknown device ID")
    current = nodes[device_id]
    peers = {}
    for role in ROLES:
        peers[ROAM_ROLES[role]] = []
        for node, resolved in nodes.items():
            if node == device_id or resolved["common"]["ssids"][role]["name"] != current["common"]["ssids"][role]["name"]:
                continue
            values = resolved["device"]["identity"]["bssids"][role]
            if role == "main" and not resolved["common"]["ssids"]["main"]["six_ghz"]:
                values = values[:2]
            peers[ROAM_ROLES[role]].append({"id": node, "address": resolved["device"]["maintenance_ipv4"],
                                         "bridge_mac": resolved["device"]["identity"]["bridge"], "bssids": values})
    return {"schema_version": SCHEMA_VERSION, "profile": PROFILE, "defaults_sha256": digest,
            "device_id": device_id, "device": current["device"], "common": current["common"],
            "platform": copy.deepcopy(defaults["platform"]), "security": copy.deepcopy(defaults["security"]),
            "services": copy.deepcopy(defaults["services"]), "peers": peers,
            "credentials": {"wifi": copy.deepcopy(document["secrets"]["wifi"]), **copy.deepcopy(document["secrets"]["devices"][device_id])}}


def quote(value: Any) -> str:
    value = str(value)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        fail("render", "control character in UCI value")
    return "'" + value.replace("'", "'\\''") + "'"


def section(kind: str, name: str | None, options: dict, lists: dict | None = None) -> str:
    rows = ["config " + kind + (" " + quote(name) if name is not None else "")]
    rows.extend("\toption " + key + " " + quote(value) for key, value in options.items())
    for key, values in (lists or {}).items():
        rows.extend("\tlist " + key + " " + quote(value) for value in values)
    return "\n".join(rows) + "\n\n"


def render(resolved: dict, defaults_path: str | Path | None = None) -> dict[str, bytes]:
    """Return desired files. Secret values never appear in filenames or logs."""
    default_source = Path(defaults_path) if defaults_path else DEFAULTS
    default_root = default_source.parent
    device, common = resolved["device"], resolved["common"]
    identity, platform = device["identity"], resolved["platform"]
    ports = platform["ports"]
    result = {}
    def put(name, value):
        result[name] = value.encode() if isinstance(value, str) else value
    network = section("interface", "loopback", {"device": "lo", "proto": "static"}, {"ipaddr": ["127.0.0.1/8"]})
    network += section("globals", "globals", {"dhcp_default_duid": identity["duid"]})
    network += section("device", "bridge", {"name": "br-lan", "type": "bridge", "macaddr": identity["bridge"], "bridge_empty": "1", "vlan_filtering": "1"}, {"ports": ports})
    network += section("interface", "lan", {"device": "br-lan.1", "proto": "dhcp", "hostname": device["hostname"], "sendclientid": "hardware"})
    network += section("interface", "lan6", {"device": "br-lan.1", "proto": "dhcpv6", "reqaddress": "try", "reqprefix": "no"})
    network += section("interface", "ap_maintenance", {"device": "br-lan.1", "proto": "static", "ipaddr": device["maintenance_ipv4"] + "/24"})
    network += section("bridge-vlan", "native_vlan", {"device": "br-lan", "vlan": "1", "local": "1"}, {"ports": [p + ":u*" for p in ports]})
    network += section("bridge-vlan", "guest_vlan", {"device": "br-lan", "vlan": "192", "local": "1"}, {"ports": [p + ":t" for p in ports]})
    network += section("device", "guest_device", {"name": "br-lan.192", "type": "8021q", "ifname": "br-lan", "vid": "192", "ipv6": "0"})
    network += section("interface", "guest", {"device": "br-lan.192", "proto": "none"})
    for port, address in identity["ethernet"].items():
        network += section("device", "mac_" + port, {"name": port, "macaddr": address})
    put("etc/config/network", network)
    radios = {}
    for index, band in enumerate(("2g", "5g", "6g")):
        name = "radio" + str(index)
        options = {"type": "mac80211", "path": platform["radio_path"], "radio": str(index), "band": band,
                   "channel": str(common["radios"][name]["channel"]), "htmode": common["radios"][name]["htmode"],
                   "country": common["country"], "disabled": "0" if index != 2 or common["ssids"]["main"]["six_ghz"] else "1",
                   "macaddr_base": identity["radios"][name]}
        lists = {"channels": [str(value) for value in range(36, 65, 4)]} if index == 1 else {}
        if index == 0:
            options["noscan"] = "1" if common["radios"][name]["force_40"] else "0"
        radios[name] = section("wifi-device", name, options, lists)
    wireless = radios["radio0"] + radios["radio1"] + radios["radio2"]
    for role in ("legacy", "iot", "main", "guest"):
        security = resolved["security"][role]
        devices = ["radio0"] if role == "iot" else ["radio0", "radio1"]
        if role == "main" and common["ssids"]["main"]["six_ghz"]:
            devices.append("radio2")
        options = {"mode": "ap", "ssid": common["ssids"][role]["name"], "encryption": security["encryption"],
                   "key": resolved["credentials"]["wifi"][role], "disabled": "0", "ieee80211w": str(security["pmf"]),
                   "wmm": "1", "ieee80211k": "1", "rrm_neighbor_report": "1", "rrm_beacon_report": "1",
                   "bss_transition": "1", "ieee80211r": "0"}
        lists = {"network": ["guest" if role == "guest" else "lan"]}
        if role == "iot":
            options.update(device="radio0", hidden="1" if common["ssids"][role]["hidden"] else "0", macaddr=identity["bssids"][role][0])
        else:
            options.update(mlo="1", macaddr=identity["mlds"][role])
            lists["device"] = devices
            lists["radio_macaddr"] = identity["bssids"][role][:len(devices)]
        if role == "guest":
            options["isolate"] = "0"
        if role == "main":
            options["rnr"] = "1"
        wireless += section("wifi-iface", SECTIONS[role], options, lists)
    put("etc/config/wireless", wireless)
    firewall = section("defaults", "defaults", {"syn_flood": "1", "input": "REJECT", "output": "ACCEPT", "forward": "REJECT", "flow_offloading": "1", "flow_offloading_hw": "1", "fullcone": "0", "fullcone6": "0"})
    firewall += section("zone", "lan", {"name": "lan", "input": "ACCEPT", "output": "ACCEPT", "forward": "ACCEPT"}, {"network": ["lan", "lan6", "ap_maintenance"]})
    firewall += section("zone", "ap_bridge", {"name": "ap_bridge", "input": "REJECT", "output": "ACCEPT", "forward": "ACCEPT"}, {"device": ["br-lan"]})
    firewall += section("zone", "guest", {"name": "guest", "input": "DROP", "output": "ACCEPT", "forward": "DROP"}, {"network": ["guest"]})
    firewall += section("include", "guest_guard", {"type": "nftables", "position": "ruleset-append", "path": "/etc/xr-guest-vlan192.nft"})
    put("etc/config/firewall", firewall)
    dhcp = section("dnsmasq", "dnsmasq", {"port": "0", "dns_redirect": "0", "nonwildcard": "1", "localservice": "1"})
    for interface in ("lan", "guest"):
        dhcp += section("dhcp", interface, {"interface": interface, "ignore": "1", "dhcpv4": "disabled", "dhcpv6": "disabled", "ra": "disabled", "ndp": "disabled"})
    dhcp += section("odhcpd", "odhcpd", {"maindhcp": "0", "loglevel": "4"})
    put("etc/config/dhcp", dhcp)
    put("etc/config/system", section("system", "system", {"hostname": device["hostname"], "timezone": common["timezone"], "zonename": common["zonename"], "ttylogin": "0", "log_size": "128", "urandom_seed": "0", "compat_version": "1.0"}) + section("timeserver", "ntp", {"enabled": "1", "enable_server": "0"}, {"server": common["ntp_servers"]}))
    put("etc/config/dropbear", section("dropbear", "main", {"Port": str(common["ssh_port"]), "DirectInterface": "ap_maintenance", "PasswordAuth": "on", "RootPasswordAuth": "on"}))
    put("etc/config/uhttpd", section("uhttpd", "main", {"home": "/www", "rfc1918_filter": "1", "max_requests": "3", "max_connections": "100", "cert": "/etc/uhttpd.crt", "key": "/etc/uhttpd.key", "cgi_prefix": "/cgi-bin", "ubus_prefix": "/ubus", "script_timeout": "60", "network_timeout": "30", "http_keepalive": "20", "tcp_keepalive": "1", "redirect_https": "0"}, {"listen_http": ["0.0.0.0:80", "[::]:80"], "listen_https": ["0.0.0.0:443", "[::]:443"], "lua_prefix": ["/cgi-bin/luci=/usr/lib/lua/luci/sgi/uhttpd.lua"]}) + section("cert", "defaults", {"days": "397", "key_type": "ec", "bits": "256", "country": "ZZ", "state": "Somewhere", "location": "Unknown", "commonname": "OpenWrt"}))
    fan = section("fancontrol", "settings", {"mode": "auto", "manual_pwm": "127", "curve_preset": "balanced"})
    for preset, temps, pwms in [("quiet", [45,55,65,75,85],[54,69,95,199,255]), ("balanced",[40,50,60,70,80],[54,69,95,199,255]), ("performance",[35,45,55,65,75],[69,95,150,199,255]), ("custom",[40,50,60,70,80],[54,69,95,199,255])]:
        options = {}
        for index, (temp, pwm) in enumerate(zip(temps, pwms), 1):
            options[f"point{index}_temp"], options[f"point{index}_pwm"] = str(temp), str(pwm)
        fan += section("curve", preset, options)
    put("etc/config/fan", fan)
    put("etc/config/cpufreq", section("settings", "cpufreq", {"governor0": "ondemand", "minfreq0": "500000", "maxfreq0": "1200000"}) + section("settings", "global", {"set": "1"}))
    put("etc/config/airoha_npu", section("cpu", None, {"governor": "ondemand", "max_freq": "1200000"}))
    roaming = ""
    for role in ROLES:
        profile = ROAM_ROLES[role]
        roaming += section("roaming", profile, {"enabled": "1" if common["roaming"]["enabled"] else "0", "peer_mode": "fleet", "section": SECTIONS[role], "iface": IFACES[role], "interval": str(common["roaming"]["interval"]), "ttl": str(common["roaming"]["ttl"])}, {"peers": [value["id"] for value in resolved["peers"][profile]]})
    all_peers = {}
    for profile, values in resolved["peers"].items():
        for peer in values:
            all_peers.setdefault(peer["id"], {"address": peer["address"], "bridge_mac": peer["bridge_mac"], "bssids": {}})["bssids"][profile] = peer["bssids"]
    for node, peer in sorted(all_peers.items()):
        roaming += section("peer", node, {"address": peer["address"], "bridge_mac": peer["bridge_mac"]}, {profile + "_bssid": values for profile, values in sorted(peer["bssids"].items())})
    put("etc/config/xr_roaming", roaming)
    put("etc/sysctl.d/13-nf-bridge.conf", (default_root / "templates/13-nf-bridge.conf").read_bytes())
    put("etc/xr-guest-vlan192.nft", (default_root / "templates/guest-guard.nft").read_bytes())
    public = {key: value for key, value in resolved.items() if key != "credentials"}
    public["config_fingerprint"] = hashlib.sha256(canonical(public)).hexdigest()
    put("etc/dicttech-ap/device.json", canonical(public))
    put("etc/dicttech-ap/root-password-hash", resolved["credentials"]["root_password_hash"] + "\n")
    if "authorized_keys" in resolved["credentials"]:
        put("etc/dropbear/authorized_keys", "".join(value + "\n" for value in resolved["credentials"]["authorized_keys"]))
    # Private first-preseed completeness proof. Exclude this file to avoid a
    # recursive digest; never copy these secret-derived hashes into device.json.
    put("etc/dicttech-ap/managed-files.json", canonical({
        "schema_version": SCHEMA_VERSION, "profile": PROFILE,
        "files": [{"path": path, "sha256": hashlib.sha256(data).hexdigest()}
                  for path, data in sorted(result.items())],
    }))
    return result


def write_private(path: str | Path, data: bytes) -> None:
    path = Path(path)
    if path.is_symlink():
        fail("output", "refusing a symbolic-link output")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def merge_files(common_path: str | Path, devices_path: str | Path, secrets_path: str | Path) -> dict:
    common = _read(common_path)
    devices = _read(devices_path)
    secrets = _read(secrets_path, secret=True)
    obj(common, "common file", {"schema_version", "profile", "common"}, {"schema_version", "profile"})
    obj(devices, "devices file", {"devices"}, {"devices"})
    obj(secrets, "secrets file", {"secrets"}, {"secrets"})
    result = {**common, **devices, **secrets}
    _validate(result)
    # Full fleet collision and shared ESS validation, not just JSON shape.
    defaults, _ = _defaults(None)
    _resolved_nodes(result, defaults)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    merge = sub.add_parser("merge", help="merge private local components into one canonical interchange document")
    for name in ("common", "devices", "secrets", "output"):
        merge.add_argument("--" + name, required=True, type=Path)
    validate = sub.add_parser("validate", help="validate document and resolve all devices without printing secrets")
    validate.add_argument("--config", required=True, type=Path)
    validate.add_argument("--defaults", type=Path)
    output = sub.add_parser("render", help="render one device to a new private local directory")
    output.add_argument("--config", required=True, type=Path)
    output.add_argument("--device", required=True)
    output.add_argument("--defaults", type=Path)
    output.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "merge":
            document = merge_files(args.common, args.devices, args.secrets)
            write_private(args.output, canonical(document))
            print(json.dumps({"valid": True, "devices": sorted(document["devices"]), "output_written": True}))
        elif args.command == "validate":
            document = load_document(args.config)
            for node in document["devices"]:
                resolve(document, node, args.defaults)
            print(json.dumps({"valid": True, "profile": PROFILE, "devices": sorted(document["devices"])}))
        else:
            document = load_document(args.config)
            state = resolve(document, args.device, args.defaults)
            files = render(state, args.defaults)
            if args.output.is_symlink() or args.output.exists() and (not args.output.is_dir() or any(args.output.iterdir())):
                fail("output", "render target must be a new or empty directory")
            args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(args.output, 0o700)
            for path, data in files.items():
                write_private(args.output / path, data)
            print(json.dumps({"rendered": True, "device": args.device, "managed_files": sorted(files)}))
        return 0
    except (ConfigError, OSError) as error:
        print(str(error) if isinstance(error, ConfigError) else "configuration file operation failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
