#!/usr/bin/env python3
"""Validate and render complete producer-owned OpenWrt resources.

The document contains every policy value; this backend supplies no network,
wireless, address-allocation or service defaults and never contacts devices.
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

PROFILE = "openwrt-config-v3"
SCHEMA_VERSION = 3
MAX_JSON_BYTES = 512 * 1024
BANDS = ("2g", "5g", "6g")
REPO = Path(__file__).resolve().parents[2]
CONTRACT = REPO / "package/dicttech/ap-profile/files/usr/share/dicttech-ap/contract.json"
BOOTSTRAP_KEY = CONTRACT.parent / "bootstrap-authorized-key.pub"
TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,47}$")
UCI_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")
UCI_FIELD = re.compile(r"^[A-Za-z0-9_]+$")
ENCRYPTION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+.-]*$")
IFACE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")
HOSTNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
MAC = re.compile(r"^[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}$")
HASH = re.compile(r"^\$(5|6)\$(?:rounds=([0-9]{4,9})\$)?([./A-Za-z0-9]{1,16})\$([./A-Za-z0-9]+)$")
LIFECYCLE_FILES = frozenset({
    "etc/dicttech-ap/device.json", "etc/dicttech-ap/managed-files.json",
    "etc/dicttech-ap/root-password-hash", "etc/dropbear/authorized_keys",
})


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




def named(value: Any, path: str, pattern=TOKEN) -> str:
    if not isinstance(value, str) or len(value) > 255 or not pattern.fullmatch(value):
        fail(path, "invalid identifier")
    return value


def ipv4(value: Any, path: str) -> str:
    try:
        address = ipaddress.IPv4Address(value) if isinstance(value, str) else None
    except ValueError:
        address = None
    if address is None or address.is_multicast or address.is_unspecified:
        fail(path, "must be a plain unicast IPv4 address")
    return str(address)


def scalar(value: Any, path: str) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if type(value) is int:
        return str(value)
    return text(value, path, 0, 65536)


def quote(value: Any) -> str:
    value = scalar(value, "UCI value")
    return "'" + value.replace("'", "'\\''") + "'"


def section(kind: str, name: str | None, options: dict, lists: dict | None = None) -> str:
    named(kind, "UCI type", UCI_NAME)
    if name is not None:
        named(name, "UCI section", UCI_FIELD)
    rows = ["config " + kind + (" " + quote(name) if name is not None else "")]
    for key, value in options.items():
        named(key, "UCI option", UCI_FIELD)
        rows.append("\toption " + key + " " + quote(value))
    for key, values in (lists or {}).items():
        named(key, "UCI list", UCI_FIELD)
        rows.extend("\tlist " + key + " " + quote(value) for value in values)
    return "\n".join(rows) + "\n\n"


def resource_path(value: Any, path: str, *, lifecycle: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > 4096 or not re.fullmatch(r"[A-Za-z0-9_./-]+", value)
            or any(part in ("", ".", "..") or len(part) > 255 for part in value.split("/"))):
        fail(path, "must be a safe relative resource path")
    if not (value.startswith("etc/") or value.startswith("usr/libexec/openwrt-managed/")):
        fail(path, "resource is outside configuration roots")
    blocked = {"etc/shadow", "etc/passwd", "etc/group", "etc/resolv.conf"}
    if (value in blocked or value.startswith(("etc/rc.d/", "etc/ssh/"))
            or value.startswith("etc/dropbear/dropbear_")
            or value == "etc/init.d/openwrt-deploy-guard"):
        fail(path, "resource belongs to a protected system facility")
    if not lifecycle and (value in LIFECYCLE_FILES or value.startswith("etc/dicttech-ap/")):
        fail(path, "resource belongs to the configuration lifecycle")
    if value.startswith("etc/init.d/") and not value.startswith("etc/init.d/openwrt-managed-"):
        fail(path, "custom init scripts require the managed namespace")
    return value


def _uci(value: Any, path: str) -> dict:
    if not isinstance(value, dict) or len(value) > 64:
        fail(path, "must contain at most 64 UCI packages")
    result = {}
    for package, sections in value.items():
        named(package, path, UCI_NAME)
        if not isinstance(sections, dict) or len(sections) > 256:
            fail(path + "." + package, "must contain at most 256 UCI sections")
        rows = {}
        for name, settings in sections.items():
            named(name, path + "." + package, UCI_FIELD)
            where = path + "." + package + "." + name
            obj(settings, where, {"type", "anonymous", "order", "options"}, {"type", "options"})
            named(settings["type"], where + ".type", UCI_NAME)
            anonymous = boolean(settings.get("anonymous", False), where + ".anonymous")
            order = integer(settings.get("order", 0), where + ".order", 0, 1_000_000)
            options = settings["options"]
            if not isinstance(options, dict) or len(options) > 256:
                fail(where + ".options", "must contain at most 256 options")
            normalized = {}
            for key, item in options.items():
                named(key, where + ".options", UCI_FIELD)
                if isinstance(item, list):
                    if len(item) > 256:
                        fail(where + ".options", "too many list entries")
                    normalized[key] = [scalar(child, where + ".options." + key) for child in item]
                else:
                    normalized[key] = scalar(item, where + ".options." + key)
            rows[name] = {"type": settings["type"], "anonymous": anonymous, "order": order, "options": normalized}
        result[package] = rows
    return result


def serialize_uci(sections: dict) -> bytes:
    rows = []
    for name, spec in sorted(sections.items(), key=lambda pair: (pair[1]["order"], pair[0])):
        # Preserve the producer's option/list ordering, including interleaving.
        rows.append("config " + spec["type"] + ("" if spec["anonymous"] else " " + quote(name)))
        for key, value in spec["options"].items():
            if isinstance(value, list):
                rows.extend("\tlist " + key + " " + quote(item) for item in value)
            else:
                rows.append("\toption " + key + " " + quote(value))
        rows.append("")
    return ("\n".join(rows) + "\n").encode()


def _files(value: Any, path: str) -> dict:
    if not isinstance(value, dict) or len(value) > 128:
        fail(path, "must contain at most 128 files")
    result = {}
    for name, content in value.items():
        resource_path(name, path)
        if name.startswith("etc/config/"):
            fail(path, "UCI packages require structured resources")
        if isinstance(content, str):
            try:
                data = content.encode("utf-8")
            except UnicodeError:
                fail(path, "text file requires valid UTF-8")
            if "\0" in content:
                fail(path, "binary file contents require base64 representation")
            mode = "600"
        else:
            obj(content, path, {"content_base64", "mode"}, {"content_base64", "mode"})
            mode = content["mode"]
            if mode not in ("600", "644", "700", "755"):
                fail(path, "file mode must be an explicit supported permission")
            encoded = content["content_base64"]
            if not isinstance(encoded, str):
                fail(path, "file content requires base64 text")
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError):
                fail(path, "invalid base64 file contents")
        if len(data) > 131072:
            fail(path, "file exceeds 128 KiB")
        result[name] = {"content_base64": base64.b64encode(data).decode("ascii"), "mode": mode}
    return result


def _platform(value: Any, path: str) -> dict:
    if not isinstance(value, dict):
        fail(path, "must be an object")
    boards = value.get("board_names")
    if not isinstance(boards, list) or not 1 <= len(boards) <= 64:
        fail(path + ".board_names", "must contain 1–64 board names")
    for board in boards:
        text(board, path + ".board_names", 1, 128)
        if not re.fullmatch(r"[A-Za-z0-9_,.+-]+", board):
            fail(path + ".board_names", "invalid board name")
    if "ports" in value:
        ports = value["ports"]
        if not isinstance(ports, list) or len(ports) > 256:
            fail(path + ".ports", "must be a list of interface names")
        for port in ports:
            named(port, path + ".ports", IFACE)
    for key in ("native_vlan", "guest_vlan"):
        if key in value:
            integer(value[key], path + "." + key, 1, 4094)
    if "radio_path" in value:
        text(value["radio_path"], path + ".radio_path", 1, 512)
    for key in ("max_ap_per_radio", "max_mlds"):
        if key in value:
            integer(value[key], path + "." + key, 1, 256)
    groups = value.get("mac_prefix_groups", [])
    if not isinstance(groups, list) or len(groups) > 64:
        fail(path + ".mac_prefix_groups", "must contain at most 64 interface groups")
    for group in groups:
        if (not isinstance(group, list) or len(group) < 2
                or any(not isinstance(item, str) for item in group) or len(group) != len(set(group))):
            fail(path + ".mac_prefix_groups", "each group requires distinct interface names")
        for name in group:
            named(name, path + ".mac_prefix_groups", IFACE)
    return copy.deepcopy(value)


def _identity(value: Any, path: str, platform: dict) -> dict:
    fields = {"bridge", "ethernet", "radios", "mlds", "bssids", "duid"}
    result = copy.deepcopy(obj(value, path, fields, fields))
    result["bridge"] = mac(result["bridge"], path + ".bridge")
    for group in ("ethernet", "radios", "mlds"):
        values = result[group]
        if not isinstance(values, dict) or len(values) > 256:
            fail(path + "." + group, "must be an address map with at most 256 entries")
        result[group] = {named(key, path + "." + group, IFACE if group == "ethernet" else TOKEN):
                         mac(address, path + "." + group) for key, address in values.items()}
    bssids = result["bssids"]
    if not isinstance(bssids, dict) or len(bssids) > 256:
        fail(path + ".bssids", "must be an address map with at most 256 entries")
    for key, values in bssids.items():
        named(key, path + ".bssids")
        if not isinstance(values, list) or len(values) != len(BANDS):
            fail(path + ".bssids", "each slot requires three reserved band addresses")
        bssids[key] = [mac(address, path + ".bssids") for address in values]
    if set(result["mlds"]) != set(bssids):
        fail(path, "MLD and BSSID reservation keys must match")
    all_bssids = [address for values in bssids.values() for address in values]
    if len(all_bssids) != len(set(all_bssids)):
        fail(path + ".bssids", "reserved BSSID addresses must be distinct")
    mlds = list(result["mlds"].values())
    if len(mlds) != len(set(mlds)):
        fail(path + ".mlds", "reserved MLD addresses must be distinct")
    for key, address in result["mlds"].items():
        if address in all_bssids and address not in bssids[key]:
            fail(path + ".mlds", "an MLD may alias only one of its own reserved BSSIDs")
    wired = {result["bridge"], *result["ethernet"].values()}
    radios = list(result["radios"].values())
    wireless = {*all_bssids, *mlds}
    if wired & wireless:
        fail(path, "wired and wireless identities collide")
    if len(radios) != len(set(radios)) or set(radios) & (wired | wireless):
        fail(path + ".radios", "radio allocation bases collide with another identity")
    for group in platform.get("mac_prefix_groups", []):
        if any(name not in result["ethernet"] for name in group):
            fail(path + ".ethernet", "platform MAC-prefix group names an absent interface")
        active = [result["ethernet"][name] for name in group]
        if len({address[:8] for address in active}) > 1:
            fail(path + ".ethernet", "platform shared MAC-register prefixes differ")
    duid = result["duid"]
    if not isinstance(duid, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{2}){4,64}", duid):
        fail(path + ".duid", "must be an even-length hexadecimal DHCP DUID")
    result["duid"] = duid.lower()
    return result


def _services(value: Any, path: str, uci: dict, files: dict) -> dict:
    if not isinstance(value, dict) or len(value) > 128:
        fail(path, "must contain at most 128 service policies")
    result = copy.deepcopy(value)
    reserved = _contract()[0]["reserved_services"]
    for name, settings in result.items():
        named(name, path, UCI_NAME)
        if name in reserved:
            fail(path, "configuration engine services are reserved")
        where = path + "." + name
        obj(settings, where, {"enable", "running", "oneshot", "reloadOn", "action"}, {"enable", "running", "reloadOn"})
        boolean(settings["enable"], where + ".enable")
        if settings["running"] is not None:
            boolean(settings["running"], where + ".running")
        if not settings["enable"] and settings["running"] is True:
            fail(where, "a disabled service cannot be required to run")
        if "oneshot" in settings:
            boolean(settings["oneshot"], where + ".oneshot")
        if settings.get("action", "reload") not in ("reload", "restart"):
            fail(where + ".action", "unsupported service action")
        triggers = obj(settings["reloadOn"], where + ".reloadOn", {"uci", "files"}, {"uci", "files"})
        for group, available in (("uci", uci), ("files", files)):
            if not isinstance(triggers[group], list):
                fail(where + ".reloadOn", "triggers must be lists")
            for trigger in triggers[group]:
                check = trigger[1:] if group == "files" and isinstance(trigger, str) and trigger.startswith("/") else trigger
                if not isinstance(check, str) or check not in available:
                    fail(where + ".reloadOn", "trigger names an unmanaged resource")
    return result


def _profiles(values: Any, path: str, identity: dict, uci: dict, platform: dict) -> list[dict]:
    if not isinstance(values, list) or len(values) > 256:
        fail(path, "must contain at most 256 SSID descriptions")
    result = copy.deepcopy(values)
    fields = {"key", "section", "iface", "name", "bands", "mlo", "network", "encryption", "pmf", "eht", "hidden", "roaming", "radios", "link_ids", "bssids", "mld_mac"}
    required = fields - {"mld_mac"}
    seen = {key: set() for key in ("key", "section", "iface")}
    wireless = uci.get("wireless", {})
    for profile in result:
        obj(profile, path, fields, required)
        key = named(profile["key"], path + ".key")
        named(profile["section"], path + ".section", UCI_FIELD)
        named(profile["iface"], path + ".iface", IFACE)
        text(profile["name"], path + ".name", 1, 32)
        named(profile["network"], path + ".network", UCI_NAME)
        named(profile["encryption"], path + ".encryption", ENCRYPTION)
        for field in ("mlo", "eht", "hidden", "roaming"):
            boolean(profile[field], path + "." + field)
        integer(profile["pmf"], path + ".pmf", 0, 2)
        for field, items in seen.items():
            if profile[field] in items:
                fail(path + "." + field, "SSID descriptions must be distinct")
            items.add(profile[field])
        bands = profile["bands"]
        if (not isinstance(bands, list) or not bands or len(bands) > len(BANDS)
                or any(not isinstance(band, str) or band not in BANDS for band in bands)
                or len(set(bands)) != len(bands)):
            fail(path + ".bands", "bands must be distinct supported frequencies")
        if profile["mlo"] != (len(bands) > 1) or (profile["mlo"] and not profile["eht"]):
            fail(path, "MLO, band count and EHT are inconsistent")
        base_encryption = profile["encryption"].split("+", 1)[0]
        wpa3 = {"sae", "owe", "wpa3", "wpa3-192"}
        if base_encryption in wpa3 and profile["pmf"] != 2:
            fail(path, "WPA3 requires protected management frames")
        if base_encryption == "sae-mixed" and profile["pmf"] == 0:
            fail(path, "SAE transition mode requires PMF capability")
        if (profile["mlo"] or "6g" in bands) and (base_encryption not in wpa3 or profile["pmf"] != 2):
            fail(path, "MLO and 6GHz require a WPA3 policy with required PMF")
        if key not in identity["bssids"]:
            fail(path, "SSID has no reserved identity slot")
        expected = [identity["bssids"][key][BANDS.index(band)] for band in bands]
        if not isinstance(profile["bssids"], list):
            fail(path + ".bssids", "active BSSIDs must be a list")
        profile["bssids"] = [mac(address, path + ".bssids") for address in profile["bssids"]]
        if profile["bssids"] != expected:
            fail(path + ".bssids", "active BSSIDs differ from the reserved address table")
        if profile["mlo"]:
            profile["mld_mac"] = mac(profile.get("mld_mac"), path + ".mld_mac")
            if profile["mld_mac"] != identity["mlds"][key]:
                fail(path + ".mld_mac", "MLD differs from its reserved identity")
        elif "mld_mac" in profile:
            fail(path + ".mld_mac", "a single-link SSID cannot claim an active MLD")
        radios, link_ids = profile["radios"], profile["link_ids"]
        if (not isinstance(radios, list) or len(radios) != len(bands)
                or any(not isinstance(radio, str) for radio in radios) or len(set(radios)) != len(radios)
                or not isinstance(link_ids, list) or len(link_ids) != len(bands)
                or any(type(index) is not int or not 0 <= index <= 15 for index in link_ids)
                or len(set(link_ids)) != len(link_ids)):
            fail(path, "radio/link descriptions are inconsistent")
        for radio, band, link in zip(radios, bands, link_ids):
            named(radio, path + ".radios")
            row = wireless.get(radio)
            if row is None or row["type"] != "wifi-device" or row["options"].get("band") != band:
                fail(path + ".radios", "radio metadata differs from its UCI device")
            if row["options"].get("disabled", "0") != "0":
                fail(path + ".radios", "SSID references a disabled radio")
            if "radio" in row["options"] and row["options"]["radio"] != str(link):
                fail(path + ".link_ids", "link ID differs from the radio UCI index")
        row = wireless.get(profile["section"])
        if row is None or row["type"] != "wifi-iface" or row["anonymous"]:
            fail(path + ".section", "SSID requires a named wifi-iface section")
        options = row["options"]
        expected_options = {"mode": "ap", "ifname": profile["iface"], "ssid": profile["name"], "encryption": profile["encryption"],
                            "ieee80211w": str(profile["pmf"]), "hidden": "1" if profile["hidden"] else "0", "disabled": "0"}
        if any(options.get(field) != value for field, value in expected_options.items()):
            fail(path, "SSID policy differs from explicit wireless UCI")
        for field in ("ieee80211k", "bss_transition"):
            if options.get(field, "0") != ("1" if profile["roaming"] else "0"):
                fail(path + ".roaming", "roaming metadata differs from explicit wireless UCI")
        if profile["eht"] and "disable_11be=1" in options.get("hostapd_bss_options", []):
            fail(path + ".eht", "enabled EHT differs from explicit hostapd BSS setting")
        network = options.get("network")
        if network != profile["network"] and network != [profile["network"]]:
            fail(path + ".network", "SSID network differs from explicit wireless UCI")
        if profile["mlo"]:
            if (options.get("mlo") != "1" or options.get("device") != radios
                    or not isinstance(options.get("macaddr"), str) or options["macaddr"].lower() != profile["mld_mac"]
                    or not isinstance(options.get("radio_macaddr"), list)
                    or [mac(address, path + ".radio_macaddr") for address in options["radio_macaddr"]] != expected):
                fail(path, "MLO UCI differs from explicit identity/link descriptions")
        elif (options.get("mlo", "0") != "0" or options.get("device") != radios[0]
              or not isinstance(options.get("macaddr"), str) or options["macaddr"].lower() != expected[0]):
            fail(path, "single-link UCI differs from explicit identity/link descriptions")
        if not profile["eht"] and "disable_11be=1" not in options.get("hostapd_bss_options", []):
            fail(path + ".eht", "disabled EHT requires the corresponding hostapd BSS setting")
        if base_encryption in {"sae", "sae-mixed", "psk2"}:
            password = options.get("key")
            raw_psk = base_encryption == "psk2" and isinstance(password, str) and re.fullmatch(r"[0-9a-fA-F]{64}", password)
            if not raw_psk:
                text(password, path + ".password", 8, 63)
    if "max_mlds" in platform and sum(profile["mlo"] for profile in result) > platform["max_mlds"]:
        fail(path, "MLD count exceeds platform capacity")
    if "max_ap_per_radio" in platform:
        counts = {}
        for profile in result:
            for radio in profile["radios"]:
                counts[radio] = counts.get(radio, 0) + 1
        if any(count > platform["max_ap_per_radio"] for count in counts.values()):
            fail(path, "AP count exceeds platform radio capacity")
    # Every managed AP BSS must participate in identity/security validation.
    actual = {name for name, row in wireless.items() if row["type"] == "wifi-iface" and row["options"].get("mode") == "ap"}
    if actual != seen["section"]:
        fail(path, "all AP wireless resources require matching SSID descriptions")
    return result


def _peers(value: Any, path: str, profiles: list[dict]) -> dict:
    keys = {profile["key"] for profile in profiles}
    obj(value, path, keys, keys)
    result = copy.deepcopy(value)
    for key, peers in result.items():
        if not isinstance(peers, list) or len(peers) > 256:
            fail(path, "each SSID supports at most 256 peers")
        seen = set()
        for peer in peers:
            obj(peer, path, {"id", "address", "bridge_mac", "bssids"}, {"id", "address", "bridge_mac", "bssids"})
            named(peer["id"], path + ".id")
            if peer["id"] in seen:
                fail(path, "duplicate peer device")
            seen.add(peer["id"])
            peer["address"] = ipv4(peer["address"], path + ".address")
            peer["bridge_mac"] = mac(peer["bridge_mac"], path + ".bridge_mac")
            if not isinstance(peer["bssids"], list) or not 1 <= len(peer["bssids"]) <= 256:
                fail(path, "peer requires active BSSIDs")
            peer["bssids"] = [mac(address, path + ".bssids") for address in peer["bssids"]]
            if len(set(peer["bssids"])) != len(peer["bssids"]):
                fail(path, "peer BSSIDs must be distinct")
    return result


def _contract() -> tuple[dict, str]:
    data = CONTRACT.read_bytes()
    value = json.loads(data, object_pairs_hook=no_duplicates)
    if value.get("schema_version") != SCHEMA_VERSION or value.get("profile") != PROFILE:
        fail("contract", "unsupported backend contract")
    reserved = value.get("reserved_services")
    if (not isinstance(reserved, list) or not reserved or any(not isinstance(name, str) or not UCI_NAME.fullmatch(name) for name in reserved)
            or len(set(reserved)) != len(reserved)):
        fail("contract", "invalid reserved engine services")
    return value, hashlib.sha256(data).hexdigest()


def _diagnostics(value: Any, path: str) -> dict:
    fields = {"country", "ssh_port", "timezone", "zonename", "ntp_servers", "radios", "roaming"}
    obj(value, path, fields)
    if "country" in value and (not isinstance(value["country"], str) or not re.fullmatch(r"[A-Z]{2}|00", value["country"])):
        fail(path + ".country", "must be a two-character regulatory country code")
    if "ssh_port" in value:
        integer(value["ssh_port"], path + ".ssh_port", 1, 65535)
    for field in ("timezone", "zonename"):
        if field in value:
            text(value[field], path + "." + field, 1, 128)
    if "ntp_servers" in value:
        items = value["ntp_servers"]
        if not isinstance(items, list) or len(items) > 64:
            fail(path + ".ntp_servers", "must be a list of at most 64 server names")
        for item in items:
            text(item, path + ".ntp_servers", 1, 253)
    if "radios" in value:
        radios = value["radios"]
        if not isinstance(radios, dict):
            fail(path + ".radios", "must be a map of public radio settings")
        for name, settings in radios.items():
            named(name, path + ".radios")
            obj(settings, path + ".radios", {"channel", "channels", "htmode", "force_40"})
            if "channel" in settings and settings["channel"] != "auto":
                integer(settings["channel"], path + ".radios.channel", 1, 233)
            if "channels" in settings:
                if not isinstance(settings["channels"], list):
                    fail(path + ".radios.channels", "must be a channel list")
                for channel in settings["channels"]:
                    integer(channel, path + ".radios.channels", 1, 233)
            if "htmode" in settings:
                text(settings["htmode"], path + ".radios.htmode", 1, 32)
            if "force_40" in settings:
                boolean(settings["force_40"], path + ".radios.force_40")
    if "roaming" in value:
        settings = obj(value["roaming"], path + ".roaming", {"enabled", "interval", "ttl"})
        if "enabled" in settings:
            boolean(settings["enabled"], path + ".roaming.enabled")
        for field in ("interval", "ttl"):
            if field in settings:
                integer(settings[field], path + ".roaming." + field, 1, 86400)
    return copy.deepcopy(value)


def _identity_resources(node: dict, path: str) -> None:
    identity, uci, platform = node["identity"], node["uci"], node["platform"]
    network = uci.get("network", {})
    devices = [row["options"] for row in network.values() if row["type"] == "device"]
    def programmed(name, address):
        rows = [row for row in devices if row.get("name") == name]
        if len(rows) != 1 or not isinstance(rows[0].get("macaddr"), str) or rows[0]["macaddr"].lower() != address:
            fail(path + ".identity", "address table differs from explicit network UCI")
    for name, address in identity["ethernet"].items():
        programmed(name, address)
    if "bridge" in platform:
        named(platform["bridge"], path + ".platform.bridge", IFACE)
        programmed(platform["bridge"], identity["bridge"])
    wireless = uci.get("wireless", {})
    for name, address in identity["radios"].items():
        row = wireless.get(name)
        base = row["options"].get("macaddr_base") if row and row["type"] == "wifi-device" else None
        if not isinstance(base, str) or base.lower() != address:
            fail(path + ".identity", "radio address table differs from explicit wireless UCI")
    ports = [row["options"].get("Port") for row in uci.get("dropbear", {}).values() if row["type"] == "dropbear"]
    if ports and str(node["ssh_port"]) not in ports:
        fail(path + ".ssh_port", "management SSH port differs from explicit dropbear UCI")
    systems = [row for row in uci.get("system", {}).values() if row["type"] == "system"]
    if any(row["options"].get("hostname", node["hostname"]) != node["hostname"] for row in systems):
        fail(path + ".hostname", "hostname differs from explicit system UCI")


def _resolver(value: Any, path: str) -> dict | None:
    if value is None:
        return None
    obj(value, path, {"target"}, {"target"})
    target = value["target"]
    if (not isinstance(target, str) or not 2 <= len(target.encode("utf-8", errors="replace")) <= 1024
            or not target.startswith("/") or not re.fullmatch(r"/[A-Za-z0-9_./-]+", target)
            or any(part in ("", ".", "..") for part in target[1:].split("/"))):
        fail(path, "resolver target must be a safe absolute file path")
    if target == "/etc/resolv.conf":
        fail(path, "resolver target cannot point to itself")
    return {"target": target}


def _node(value: Any, path: str) -> dict:
    fields = {"hostname", "management_ipv4", "maintenance_ipv4", "ssh_port", "identity", "platform", "common", "ssids", "peers", "services", "uci", "files", "resolver", "credentials"}
    node = copy.deepcopy(obj(value, path, fields, fields))
    if not isinstance(node["hostname"], str) or not HOSTNAME.fullmatch(node["hostname"]):
        fail(path + ".hostname", "invalid hostname")
    for field in ("management_ipv4", "maintenance_ipv4"):
        node[field] = ipv4(node[field], path + "." + field)
    integer(node["ssh_port"], path + ".ssh_port", 1, 65535)
    node["platform"] = _platform(node["platform"], path + ".platform")
    node["common"] = _diagnostics(node["common"], path + ".common")
    node["resolver"] = _resolver(node["resolver"], path + ".resolver")
    node["identity"] = _identity(node["identity"], path + ".identity", node["platform"])
    node["uci"] = _uci(node["uci"], path + ".uci")
    node["files"] = _files(node["files"], path + ".files")
    _identity_resources(node, path)
    node["services"] = _services(node["services"], path + ".services", node["uci"], node["files"])
    node["ssids"] = _profiles(node["ssids"], path + ".ssids", node["identity"], node["uci"], node["platform"])
    node["peers"] = _peers(node["peers"], path + ".peers", node["ssids"])
    credentials = obj(node["credentials"], path + ".credentials", {"root_password_hash", "authorized_keys"}, {"root_password_hash"})
    _password_hash(credentials["root_password_hash"], path + ".credentials.root_password_hash")
    if "authorized_keys" in credentials:
        _authorized_keys(credentials["authorized_keys"], path + ".credentials.authorized_keys")
    return node


def _validated_nodes(document: Any) -> dict:
    obj(document, "document", {"schema_version", "profile", "devices"}, {"schema_version", "profile", "devices"})
    if type(document["schema_version"]) is not int or document["schema_version"] != SCHEMA_VERSION or document["profile"] != PROFILE:
        fail("document", "unsupported configuration schema or profile")
    devices = document["devices"]
    if not isinstance(devices, dict) or not 1 <= len(devices) <= 256:
        fail("devices", "a document requires 1–256 devices")
    nodes = {named(name, "devices"): _node(node, "devices." + name) for name, node in devices.items()}
    owners, duids, names, addresses, ess = {}, {}, {}, {}, {}
    for name, node in nodes.items():
        identity = node["identity"]
        for value, table, field in ((node["hostname"].lower(), names, "hostname"), (node["management_ipv4"], addresses, "management address"), (identity["duid"], duids, "DUID")):
            if value in table:
                fail("devices", "devices share a " + field)
            table[value] = name
        all_addresses = [identity["bridge"]]
        for group in ("ethernet", "radios", "mlds"):
            all_addresses.extend(identity[group].values())
        all_addresses.extend(address for links in identity["bssids"].values() for address in links)
        for address in all_addresses:
            if address in owners and owners[address] != name:
                fail("devices", "MAC identity is shared by different devices")
            owners[address] = name
        for profile in node["ssids"]:
            options = node["uci"]["wireless"][profile["section"]]["options"]
            signature = (profile["network"], profile["encryption"], profile["pmf"], options.get("key"))
            if profile["name"] in ess and ess[profile["name"]] != signature:
                fail("devices", "a shared SSID has inconsistent identity, network, security or password")
            ess[profile["name"]] = signature
        for key, peers in node["peers"].items():
            profile = next(profile for profile in node["ssids"] if profile["key"] == key)
            if peers and not profile["roaming"]:
                fail("devices", "a non-roaming SSID cannot list peers")
            for peer in peers:
                if peer["id"] == name:
                    fail("devices", "an AP cannot be its own roaming peer")
                if peer["id"] not in nodes:
                    fail("devices", "roaming peer is absent from the complete producer device table")
                other = nodes[peer["id"]]
                matches = [item for item in other["ssids"] if item["name"] == profile["name"] and item["roaming"]]
                available = {address for item in matches for address in item["bssids"]}
                if (not matches or peer["address"] != other["management_ipv4"]
                        or peer["bridge_mac"] != other["identity"]["bridge"] or not set(peer["bssids"]) <= available):
                    fail("devices", "roaming peer does not match the producer's final device identity")

    return nodes


def load_document(path: str | Path) -> dict:
    document = _read(path, secret=True)
    _validated_nodes(document)
    return document


def resolve(document: dict, device_id: str) -> dict:
    nodes = _validated_nodes(document)
    if device_id not in nodes:
        fail("device", "unknown device ID")
    _, contract_digest = _contract()
    current = nodes[device_id]
    identity_fields = ("hostname", "management_ipv4", "maintenance_ipv4", "ssh_port", "identity")
    return {"schema_version": SCHEMA_VERSION, "profile": PROFILE, "contract_sha256": contract_digest,
            "device_id": device_id, "device": {key: current[key] for key in identity_fields},
            **{key: current[key] for key in ("platform", "common", "ssids", "services", "peers", "resolver", "credentials", "uci", "files")}}


def ssid_profiles(state: dict) -> list[dict]:
    return state["ssids"]


def render(resolved: dict) -> dict[str, bytes]:
    """Serialize exactly the validated resources and lifecycle bookkeeping."""
    # resolve() is the validation boundary; rendering does not fill policy gaps.
    result = {"etc/config/" + package: serialize_uci(sections) for package, sections in resolved["uci"].items()}
    result.update({name: base64.b64decode(content["content_base64"]) for name, content in resolved["files"].items()})
    public = {key: value for key, value in resolved.items() if key not in {"credentials", "uci", "files"}}
    public["config_fingerprint"] = hashlib.sha256(canonical(public)).hexdigest()
    result["etc/dicttech-ap/device.json"] = canonical(public)
    result["etc/dicttech-ap/root-password-hash"] = (resolved["credentials"]["root_password_hash"] + "\n").encode()
    if "authorized_keys" in resolved["credentials"]:
        result["etc/dropbear/authorized_keys"] = "".join(value + "\n" for value in resolved["credentials"]["authorized_keys"]).encode()
    if len(result) > 127 or any(len(data) > 131072 for data in result.values()):
        fail("resources", "configuration exceeds transaction resource limits")
    result["etc/dicttech-ap/managed-files.json"] = canonical({
        "schema_version": SCHEMA_VERSION, "profile": PROFILE,
        "files": [{"path": path, "sha256": hashlib.sha256(data).hexdigest(), "mode": resolved["files"].get(path, {}).get("mode", "600")}
                  for path, data in sorted(result.items())],
    })
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate a complete producer-owned resource document")
    validate.add_argument("--config", required=True, type=Path)
    output = sub.add_parser("render", help="render one device into a private local directory")
    output.add_argument("--config", required=True, type=Path)
    output.add_argument("--device", required=True)
    output.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        document = load_document(args.config)
        if args.command == "validate":
            print(json.dumps({"valid": True, "profile": PROFILE, "devices": sorted(document["devices"])}))
        else:
            files = render(resolve(document, args.device))
            if args.output.is_symlink() or args.output.exists() and (not args.output.is_dir() or any(args.output.iterdir())):
                fail("output", "render target must be a new or empty directory")
            args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(args.output, 0o700)
            modes = {row["path"]: row["mode"] for row in json.loads(files["etc/dicttech-ap/managed-files.json"])["files"]}
            for path, data in files.items():
                write_private(args.output / path, data)
                os.chmod(args.output / path, int(modes.get(path, "600"), 8))
            print(json.dumps({"rendered": True, "device": args.device, "managed_files": sorted(files)}))
        return 0
    except (ConfigError, OSError) as error:
        print(str(error) if isinstance(error, ConfigError) else "configuration file operation failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
