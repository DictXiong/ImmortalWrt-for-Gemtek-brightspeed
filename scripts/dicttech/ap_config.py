#!/usr/bin/env python3
"""Strict, local-only DictTech AP v2 configuration resolution and rendering.

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
import shlex
import stat
import sys
import tempfile
from typing import Any

PROFILE = "dicttech-ap-v2"
SCHEMA_VERSION = 2
MAX_JSON_BYTES = 512 * 1024
ROLES = ("main", "guest", "iot")
ROAM_ROLES = {role: role for role in ROLES}
SECTIONS = {role: "ssid_" + role for role in ROLES}
IFACES = {"main": "ap-mld0", "guest": "ap-mld1", "iot": "ap-iot"}
BANDS = ("2g", "5g", "6g")
REPO = Path(__file__).resolve().parents[2]
DEFAULTS = REPO / "package/dicttech/ap-profile/files/usr/share/dicttech-ap/defaults.json"
BOOTSTRAP_KEY = DEFAULTS.parent / "bootstrap-authorized-key.pub"


def without_bootstrap_key(data: bytes) -> bytes:
    """Do not promote the initialization credential into a permanent export."""
    kind, blob = BOOTSTRAP_KEY.read_bytes().split()[:2]
    result = []
    for line in data.splitlines(keepends=True):
        if line.lstrip().startswith(b'#'):
            result.append(line)
            continue
        try:
            lexer = shlex.shlex(line.decode('utf-8'), posix=True)
            lexer.whitespace_split = True
            lexer.commenters = ''
            fields = [next(lexer, ''), next(lexer, '')]
            if not re.match(r'^(ssh-|ecdsa-|sk-)', fields[0]):
                fields.append(next(lexer, ''))
        except (UnicodeDecodeError, ValueError):
            result.append(line)
            continue
        identity = [kind.decode(), blob.decode()]
        if fields[:2] == identity or fields[1:3] == identity:
            continue
        result.append(line)
    return b"".join(result)
TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,47}$")
EXTRA_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
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
    obj(delta, path, {"country", "ssh_port", "timezone", "zonename", "ntp_servers", "radios", "ssids", "extra_ssids", "roaming"})
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
            obj(values, where, {"name", "six_ghz", "encryption"} if role == "main" else {"name", "hidden", "encryption", "eht"} if role == "iot" else {"name", "encryption"})
            if "name" in values:
                text(values["name"], where + ".name", 1, 32)
            if "encryption" in values and values["encryption"] not in ("sae", "sae-mixed", "psk2"):
                fail(where + ".encryption", "unsupported security policy")
            for key in ("six_ghz", "hidden", "eht"):
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
    if "extra_ssids" in delta:
        extras = delta["extra_ssids"]
        if not isinstance(extras, list) or len(extras) > 14:
            fail(path + ".extra_ssids", "must be an ordered array of at most 14 custom SSIDs")
        ids = set()
        for index, values in enumerate(extras):
            where = path + ".extra_ssids[" + str(index) + "]"
            obj(values, where, {"id", "name", "bands", "mlo", "encryption", "network", "hidden", "password_ref", "roaming", "eht"}, {"id", "name"})
            ident = values["id"]
            if not isinstance(ident, str) or not EXTRA_ID.fullmatch(ident) or ident in ROLES:
                fail(where + ".id", "must be a non-reserved UCI-safe ID of 1–32 characters")
            if ident in ids:
                fail(path + ".extra_ssids", "duplicate custom SSID ID")
            ids.add(ident)
            text(values["name"], where + ".name", 1, 32)
            if "bands" in values:
                bands = values["bands"]
                if not isinstance(bands, list) or not 1 <= len(bands) <= 3 or any(not isinstance(b, str) or b not in BANDS for b in bands) or len(set(bands)) != len(bands):
                    fail(where + ".bands", "must contain distinct supported bands")
            for field in ("mlo", "hidden", "roaming", "eht"):
                if field in values:
                    boolean(values[field], where + "." + field)
            if "encryption" in values and values["encryption"] not in ("sae", "sae-mixed", "psk2"):
                fail(where + ".encryption", "unsupported security policy")
            if "network" in values and values["network"] not in ("lan", "guest"):
                fail(where + ".network", "must select the trusted or guest network")
            if "password_ref" in values and (not isinstance(values["password_ref"], str) or not TOKEN.fullmatch(values["password_ref"])):
                fail(where + ".password_ref", "must name a Wi-Fi secret")


def _ssid_specs(common: dict, defaults: dict, path: str) -> list[dict]:
    """Resolve public per-ESS policy before assigning MAC identities."""
    def preset(role, bands):
        encryption = common["ssids"][role]["encryption"]
        if role != "iot" and encryption != "sae":
            fail(path + ".ssids." + role, "MLO requires WPA3 SAE with required PMF")
        return {"key": role, "section": SECTIONS[role], "iface": IFACES[role],
                "name": common["ssids"][role]["name"], "bands": bands,
                "mlo": role != "iot", "network": "guest" if role == "guest" else "lan",
                "encryption": encryption, "pmf": 2 if encryption == "sae" else 1,
                "eht": common["ssids"][role].get("eht", True),
                "hidden": common["ssids"][role].get("hidden", False),
                "roaming": common["roaming"]["enabled"], "password_ref": role}
    main = preset("main", list(BANDS if common["ssids"]["main"]["six_ghz"] else BANDS[:2]))
    guest = preset("guest", list(BANDS[:2]))
    profiles, extras, next_mld = [main, guest], [], 2
    for entry in common.get("extra_ssids", []):
        value = deep_merge(defaults["extra_defaults"], entry)
        bands = [band for band in BANDS if band in value["bands"]]
        value["bands"] = bands
        value["mlo"] = entry.get("mlo", len(bands) > 1)
        value["eht"] = value.get("eht", True)
        where = path + ".extra_ssids"
        if (value["mlo"] and len(bands) < 2) or (not value["mlo"] and len(bands) != 1):
            fail(where, "MLO requires multiple bands; single-link SSIDs require one band")
        if value["mlo"] and not value["eht"]:
            fail(where, "MLO requires EHT")
        if (value["mlo"] or "6g" in bands) and value["encryption"] != "sae":
            fail(where, "MLO and 6GHz require WPA3 SAE with required PMF")
        extras.append(copy.deepcopy(value))
        key = value["id"]
        iface = "ap-mld" + str(next_mld) if value["mlo"] else "ap-x" + hashlib.sha256(value["id"].encode()).hexdigest()[:8]
        next_mld += int(value["mlo"])
        profiles.append({"key": key, "section": "ssid_extra_" + key, "iface": iface,
                         "name": value["name"], "bands": bands, "mlo": value["mlo"], "eht": value["eht"],
                         "network": value["network"], "encryption": value["encryption"],
                         "pmf": 2 if value["encryption"] == "sae" else 1,
                         "hidden": value["hidden"], "roaming": common["roaming"]["enabled"] and value["roaming"],
                         "password_ref": value["password_ref"]})
    common["extra_ssids"] = extras
    profiles.append(preset("iot", ["2g"]))
    if len({profile["name"] for profile in profiles}) != len(profiles):
        fail(path, "all SSIDs on an AP must have distinct names")
    if len({profile["iface"] for profile in profiles}) != len(profiles):
        fail(path, "derived interface names collide; choose another custom ID")
    if common["country"] == "CN" and any("6g" in profile["bands"] for profile in profiles):
        fail(path, "CN profile does not permit any 6GHz SSID")
    if len(extras) > defaults["platform"].get("max_extra_ssids", 14):
        fail(path, "custom SSID count exceeds the profile limit")
    if sum(profile["mlo"] for profile in profiles) > defaults["platform"].get("max_mlds", 16):
        fail(path, "MLD count exceeds the hardware limit")
    for band in BANDS:
        if sum(band in profile["bands"] for profile in profiles) > defaults["platform"].get("max_ap_per_radio", 16):
            fail(path, "AP count exceeds a radio's hardware limit")
    for profile in profiles:
        profile["radios"] = ["radio" + str(BANDS.index(b)) for b in profile["bands"]]
        profile["link_ids"] = [BANDS.index(b) for b in profile["bands"]]
    return profiles


def _complete_identity(device: dict, path: str, profiles: list[dict]) -> dict:
    """Validate a producer-owned address table without allocating any addresses."""
    path += ".identity"
    fields = {"complete", "bridge", "ethernet", "radios", "mlds", "bssids", "duid"}
    result = copy.deepcopy(obj(device["identity"], path, fields, fields))
    # mac_base remains part of the device interchange schema, but is not an
    # allocator input in this mode. In particular, do not add legacy offsets.
    mac(device["mac_base"], path.rsplit(".", 1)[0] + ".mac_base")
    ports = {"wan", "lan2", "lan3", "lan4"}
    obj(result["ethernet"], path + ".ethernet", ports | {"cpu"}, ports)
    radios = {"radio0", "radio1", "radio2"}
    obj(result["radios"], path + ".radios", radios, radios)
    keys = set(ROLES)
    for group in ("mlds", "bssids"):
        values = result[group]
        if not isinstance(values, dict):
            fail(path + "." + group, "must be an object")
        for key in values:
            if key not in ROLES and (not isinstance(key, str) or not EXTRA_ID.fullmatch(key)):
                fail(path + "." + group, "invalid custom identity ID")
        keys.update(values)
    if len(keys - set(ROLES)) > 64:
        fail(path, "at most 64 custom identity slots may be reserved")
    keys.update(profile["key"] for profile in profiles)
    for group in ("mlds", "bssids"):
        obj(result[group], path + "." + group, keys, keys)
    _validate_identity(result, path, {key: 3 for key in keys}, complete=True)
    return result


def _identity(device: dict, path: str, profiles: list[dict]) -> dict:
    override = device.get("identity", {})
    if isinstance(override, dict) and "complete" in override:
        if boolean(override["complete"], path + ".identity.complete"):
            return _complete_identity(device, path, profiles)
        override = {key: value for key, value in override.items() if key != "complete"}
    base = mac(device["mac_base"], path + ".mac_base")
    number = int(base.replace(":", ""), 16)
    def derived(offset):
        n = number + offset
        if n >= 1 << 48:
            fail(path + ".mac_base", "derived MAC range would overflow")
        data = bytearray(n.to_bytes(6, "big"))
        data[0] = (data[0] | 2) & 254
        return ":".join(f"{v:02x}" for v in data)
    def custom(key, band):
        # Stable across inventory/list ordering and other custom SSID changes.
        data = bytearray(hashlib.sha256(("dicttech-ap-v2\0" + base + "\0" + key + "\0" + band).encode()).digest()[:6])
        data[0] = (data[0] | 2) & 254
        return ":".join(f"{v:02x}" for v in data)
    result = {"bridge": base,
              "ethernet": {"wan": derived(1), "lan2": base, "lan3": base, "lan4": base, "cpu": derived(4)},
              "radios": {"radio0": derived(0x10), "radio1": derived(0x20), "radio2": derived(0x30)},
              "mlds": {"main": derived(0x50), "guest": derived(0x60)},
              "bssids": {"main": [derived(0x50), derived(0x52), derived(0x53)],
                          "guest": [derived(0x60), derived(0x62)], "iot": [derived(0x70)]},
              "duid": "00030001" + base.replace(":", "")}
    counts = {"main": 3, "guest": 2, "iot": 1}
    override = obj(override, path + ".identity", set(result))
    custom_ids = {profile["key"] for profile in profiles if profile["key"] not in ROLES}
    for group in ("mlds", "bssids"):
        values = override.get(group, {})
        if not isinstance(values, dict):
            fail(path + ".identity." + group, "must be an object")
        for key in values:
            if key in result[group]:
                continue
            if not isinstance(key, str) or not EXTRA_ID.fullmatch(key) or key in ROLES:
                fail(path + ".identity." + group, "invalid custom identity ID")
            custom_ids.add(key)
    if len(custom_ids) > 64:
        fail(path + ".identity", "at most 64 custom identity slots may be reserved")
    for key in sorted(custom_ids):
        # Keep all bands and the MLD identity even while a custom SSID is absent
        # or single-link. Its private inventory need not track active topology.
        result["bssids"][key] = [custom(key, band) for band in BANDS]
        result["mlds"][key] = custom(key, "mld")
        counts[key] = 3
    normalized_override = copy.deepcopy(override)
    for group in ("ethernet", "radios"):
        if group in normalized_override:
            obj(normalized_override[group], path + ".identity." + group, set(result[group]))
    for key, values in normalized_override.get("bssids", {}).items():
        if key in ROLES or not isinstance(values, dict):
            continue
        obj(values, path + ".identity.bssids." + key, set(BANDS))
        if not values:
            fail(path + ".identity.bssids." + key, "a band map must contain at least one band")
        slots = result["bssids"][key][:]
        for band, address in values.items():
            slots[BANDS.index(band)] = address
        normalized_override["bssids"][key] = slots
    result = deep_merge(result, normalized_override)
    _validate_identity(result, path + ".identity", counts)
    return result


def _validate_identity(result: dict, path: str, counts: dict, *, complete: bool = False) -> None:
    result["bridge"] = mac(result["bridge"], path + ".bridge")
    for group in ("ethernet", "radios", "mlds"):
        result[group] = {key: mac(value, path + "." + group + "." + key) for key, value in result[group].items()}
    all_bssids = []
    for key, count in counts.items():
        values = result["bssids"][key]
        if not isinstance(values, list) or len(values) != count:
            fail(path + ".bssids." + key, f"must contain exactly {count} MAC address(es)")
        result["bssids"][key] = [mac(value, path + ".bssids." + key) for value in values]
        all_bssids.extend(result["bssids"][key])
    if len(set(all_bssids)) != len(all_bssids):
        fail(path + ".bssids", "different BSSes must have different addresses")
    if result["bridge"] in all_bssids:
        fail(path, "bridge address collides with a BSS")
    if len(set(result["mlds"].values())) != len(result["mlds"]) or result["bridge"] in result["mlds"].values():
        fail(path + ".mlds", "MLD addresses must be distinct and separate from the bridge")
    for key, address in result["mlds"].items():
        aliases = result["bssids"][key] if complete or key not in ROLES else result["bssids"][key][:1]
        if address in all_bssids and address not in aliases:
            fail(path + ".mlds", "an MLD may share only its own reserved BSSID")
    if complete:
        # XR1710G's CPU and LAN2 GDMs share the LAN high-MAC register.
        # This validates the target table, not whether a live sequence of
        # per-interface updates can migrate a previously registered prefix.
        ethernet = result["ethernet"]
        if "cpu" in ethernet and ethernet["cpu"][:8] != ethernet["lan2"][:8]:
            fail(path + ".ethernet", "cpu and lan2 must share their first three MAC bytes")
        # A bridge and its Ethernet ports may intentionally share an address.
        # Radio bases are allocation parameters, not extra active interfaces,
        # but reserve them against each other and all explicit identities.
        wired = {result["bridge"], *result["ethernet"].values()}
        radio_bases = list(result["radios"].values())
        wireless = {*all_bssids, *result["mlds"].values()}
        if len(set(radio_bases)) != len(radio_bases):
            fail(path + ".radios", "radio allocation bases must be distinct")
        if set(radio_bases) & (wired | wireless):
            fail(path + ".radios", "radio allocation base collides with another identity")
        if wired & wireless:
            fail(path, "Ethernet address collides with a wireless identity")
    duid = result["duid"]
    if not isinstance(duid, str) or not re.fullmatch(r"[0-9a-fA-F]{8,128}", duid) or len(duid) % 2:
        fail(path + ".duid", "must be an even-length hexadecimal DHCP DUID")
    result["duid"] = duid.lower()


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
        if parts[:2] == BOOTSTRAP_KEY.read_text().split()[:2]:
            fail(path, "the firmware initialization key cannot be a permanent authorization")
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
        fail("devices", "a fleet must contain between 1 and 16 devices")
    secrets = obj(document["secrets"], "secrets", {"wifi", "devices"}, {"wifi", "devices"})
    if not isinstance(secrets["wifi"], dict):
        fail("secrets.wifi", "must be an object")
    wifi = obj(secrets["wifi"], "secrets.wifi", set(secrets["wifi"]), set(ROLES))
    for role, password in wifi.items():
        if not isinstance(role, str) or not TOKEN.fullmatch(role):
            fail("secrets.wifi", "secret names must be UCI-safe tokens")
        text(password, "secrets.wifi." + role, 8, 63)
    credentials = obj(secrets["devices"], "secrets.devices", set(devices), set(devices))
    for node, device in devices.items():
        if not isinstance(node, str) or not TOKEN.fullmatch(node):
            fail("devices", "device IDs must be UCI-safe tokens, start with a letter, and contain only letters, digits or underscore")
        if node in ROLES:
            fail("devices", "device ID collides with a reserved roaming section name")
        path = "devices." + node
        obj(device, path, {"hostname", "maintenance_ipv4", "mac_base", "identity", "overrides"}, {"hostname", "maintenance_ipv4", "mac_base"})
        if not isinstance(device["hostname"], str) or not HOSTNAME.fullmatch(device["hostname"]):
            fail(path + ".hostname", "invalid hostname")
        try:
            address = ipaddress.IPv4Address(device["maintenance_ipv4"])
        except (ValueError, TypeError):
            fail(path + ".maintenance_ipv4", "must be a plain IPv4 address")
        if address not in ipaddress.IPv4Network("192.168.250.0/24") or not 1 <= int(str(address).rsplit(".", 1)[1]) <= 99:
            fail(path + ".maintenance_ipv4", "must be a registered address from 192.168.250.1 through .99")
        validate_delta(device.get("overrides", {}), path + ".overrides")
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
    for node, device in sorted(document["devices"].items()):
        common = deep_merge(defaults["common"], document.get("common", {}))
        common = deep_merge(common, device.get("overrides", {}))
        profiles = _ssid_specs(common, defaults, "devices." + node)
        if common["roaming"]["ttl"] < 3 * common["roaming"]["interval"]:
            fail("devices." + node + ".roaming", "TTL must cover at least three check intervals")
        if common["radios"]["radio0"]["force_40"] and common["radios"]["radio0"]["htmode"] != "EHT40":
            fail("devices." + node + ".radios.radio0", "force_40 requires EHT40")
        for profile in profiles:
            if profile["password_ref"] not in document["secrets"]["wifi"]:
                fail("devices." + node, "an SSID references a missing Wi-Fi secret")
        for key, table in (("hostname", seen_hostnames), ("maintenance_ipv4", seen_addresses)):
            value = device[key].lower() if key == "hostname" else device[key]
            if value in table:
                fail("devices", "duplicate " + key)
            table[value] = node
        identity = _identity(device, "devices." + node, profiles)
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
        for profile in profiles:
            key = profile["key"]
            # Inventory slots are canonical 2g/5g/6g; active MLO link IDs use
            # those same physical-radio indexes, even for a 5+6GHz profile.
            profile["bssids"] = [identity["bssids"][key][index] for index in profile["link_ids"]]
            if profile["mlo"]:
                profile["mld_mac"] = identity["mlds"][key]
        normalized = copy.deepcopy(device)
        normalized["mac_base"] = mac(device["mac_base"], "devices." + node + ".mac_base")
        normalized["identity"] = identity
        normalized.pop("overrides", None)
        nodes[node] = {"device": normalized, "common": common, "ssids": profiles}
    reserved = {profile["key"] for value in nodes.values() for profile in value["ssids"]}
    if set(document["devices"]) & reserved:
        fail("devices", "a device ID collides with a roaming profile ID")
    # Same names must describe the same ESS and profile, including VLAN/security.
    ess = {}
    for resolved in nodes.values():
        for profile in resolved["ssids"]:
            name = profile["name"]
            signature = (profile["key"], profile["network"], profile["encryption"], profile["pmf"], document["secrets"]["wifi"][profile["password_ref"]])
            if name in ess and ess[name] != signature:
                fail("devices", "a shared SSID has inconsistent profile, network, security or password")
            ess[name] = signature
    return nodes


def ssid_profiles(state: dict) -> list[dict]:
    """The resolved ordered SSID contract shared by rendering and acceptance."""
    return state["ssids"]


def resolve(document: dict, device_id: str, defaults_path: str | Path | None = None) -> dict:
    _validate(document)
    defaults, digest = _defaults(defaults_path)
    nodes = _resolved_nodes(document, defaults)
    if device_id not in nodes:
        fail("device", "unknown device ID")
    current = nodes[device_id]
    peers = {}
    for profile in current["ssids"]:
        key = profile["key"]
        peers[key] = []
        if not profile["roaming"]:
            continue
        for node, resolved in nodes.items():
            if node == device_id:
                continue
            other = next((value for value in resolved["ssids"] if value["key"] == key), None)
            if other is None or other["name"] != profile["name"] or not other["roaming"]:
                continue
            peers[key].append({"id": node, "address": resolved["device"]["maintenance_ipv4"],
                               "bridge_mac": resolved["device"]["identity"]["bridge"], "bssids": other["bssids"]})
    return {"schema_version": SCHEMA_VERSION, "profile": PROFILE, "defaults_sha256": digest,
            "device_id": device_id, "device": current["device"], "common": current["common"], "ssids": current["ssids"],
            "platform": copy.deepcopy(defaults["platform"]),
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
                   "country": common["country"], "disabled": "0" if any(band in profile["bands"] for profile in ssid_profiles(resolved)) else "1",
                   "macaddr_base": identity["radios"][name]}
        lists = {"channels": [str(value) for value in range(36, 65, 4)]} if index == 1 else {}
        if index == 0:
            options["noscan"] = "1" if common["radios"][name]["force_40"] else "0"
        radios[name] = section("wifi-device", name, options, lists)
    wireless = radios["radio0"] + radios["radio1"] + radios["radio2"]
    for profile in ssid_profiles(resolved):
        enabled = "1" if profile["roaming"] else "0"
        options = {"mode": "ap", "ifname": profile["iface"], "ssid": profile["name"], "encryption": profile["encryption"],
                   "key": resolved["credentials"]["wifi"][profile["password_ref"]], "disabled": "0", "ieee80211w": str(profile["pmf"]),
                   "hidden": "1" if profile["hidden"] else "0", "wmm": "1", "ieee80211k": enabled,
                   "rrm_neighbor_report": enabled, "rrm_beacon_report": enabled,
                   "bss_transition": enabled, "ieee80211r": "0"}
        lists = {"network": [profile["network"]]}
        if not profile["eht"]:
            # EHT+PMF enabled beacon protection before this compatibility
            # override; retain that protection after a full config reload.
            lists["hostapd_bss_options"] = ["disable_11be=1", "beacon_prot=1"]
        if profile["mlo"]:
            options.update(mlo="1", macaddr=profile["mld_mac"])
            lists["device"] = profile["radios"]
            lists["radio_macaddr"] = profile["bssids"]
        else:
            options.update(device=profile["radios"][0], macaddr=profile["bssids"][0])
        if profile["network"] == "guest":
            options["isolate"] = "0"
        if "6g" in profile["bands"]:
            options["rnr"] = "1"
        wireless += section("wifi-iface", profile["section"], options, lists)
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
    for profile in ssid_profiles(resolved):
        key = profile["key"]
        roaming += section("roaming", key, {"enabled": "1" if profile["roaming"] else "0", "peer_mode": "fleet",
                            "section": profile["section"], "iface": profile["iface"], "mlo": "1" if profile["mlo"] else "0",
                            "max_links": str(len(profile["bands"])), "interval": str(common["roaming"]["interval"]),
                            "ttl": str(common["roaming"]["ttl"])},
                           {"peers": [value["id"] for value in resolved["peers"][key]]})
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
