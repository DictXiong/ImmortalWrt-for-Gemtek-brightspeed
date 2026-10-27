#!/usr/bin/env python3
"""Convert a private v1 document to v2 locally; never deploy or contact an AP."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ap_config as model

OLD_ROLES = ("main", "legacy", "guest", "iot")
OLD_DELTA = {"country", "ssh_port", "timezone", "zonename", "ntp_servers", "radios", "ssids", "roaming"}
OLD_GROUPS = {
    "ethernet": {"wan", "lan2", "lan3", "lan4", "cpu"},
    "radios": {"radio0", "radio1", "radio2"},
    "mlds": {"main", "legacy", "guest"},
    "bssids": set(OLD_ROLES),
}


def _delta(value, path):
    """Validate v1's exact input surface before dropping its legacy shorthand."""
    model.obj(value, path, OLD_DELTA)
    result = copy.deepcopy(value)
    legacy_name = None
    if "ssids" in result:
        model.obj(result["ssids"], path + ".ssids", set(OLD_ROLES))
        if "legacy" in result["ssids"]:
            legacy = model.obj(result["ssids"].pop("legacy"), path + ".ssids.legacy", {"name"})
            if "name" in legacy:
                legacy_name = model.text(legacy["name"], path + ".ssids.legacy.name", 1, 32)
        if not result["ssids"]:
            result.pop("ssids")
    model.validate_delta(result, path)
    return result, legacy_name


def _old_mac(base, offset):
    number = int(model.mac(base, "device.mac_base").replace(":", ""), 16) + offset
    if number >= 1 << 48:
        model.fail("device.mac_base", "old derived MAC range would overflow")
    data = bytearray(number.to_bytes(6, "big"))
    data[0] = (data[0] | 2) & 254
    return ":".join(f"{value:02x}" for value in data)


def _identity(device, path):
    old = model.obj(device.get("identity", {}), path + ".identity", {"bridge", "ethernet", "radios", "mlds", "bssids", "duid"})
    for group, fields in OLD_GROUPS.items():
        if group in old:
            model.obj(old[group], path + ".identity." + group, fields)
    identity = copy.deepcopy(old)
    mlds = identity.setdefault("mlds", {})
    bssids = identity.setdefault("bssids", {})
    mlds["battalion"] = mlds.pop("legacy", _old_mac(device["mac_base"], 0x40))
    legacy_links = bssids.pop("legacy", [_old_mac(device["mac_base"], 0x41), _old_mac(device["mac_base"], 0x42)])
    if not isinstance(legacy_links, list) or len(legacy_links) != 2:
        model.fail(path + ".identity.bssids.legacy", "old legacy identity requires exactly two MACs")
    bssids["battalion"] = dict(zip(("2g", "5g"), legacy_links))
    return identity


def migrate(document: dict) -> dict:
    """Return an independently validated v2 copy, preserving effective v1 ESSes."""
    model.obj(document, "document", {"schema_version", "profile", "common", "devices", "secrets"}, {"schema_version", "profile", "devices", "secrets"})
    if type(document["schema_version"]) is not int or document["schema_version"] != 1 or document["profile"] != "dicttech-ap-v1":
        model.fail("document", "migration requires a version 1 DictTech AP document")
    common, common_legacy = _delta(document.get("common", {}), "common")
    common_legacy = common_legacy if common_legacy is not None else "327Battalion"
    extra = {"id": "battalion", "name": common_legacy, "password_ref": "battalion"}
    common["extra_ssids"] = [extra]
    old_devices = document["devices"]
    if not isinstance(old_devices, dict) or not 1 <= len(old_devices) <= 16:
        model.fail("devices", "old fleet must contain between 1 and 16 devices")
    old_secrets = model.obj(document["secrets"], "secrets", {"wifi", "devices"}, {"wifi", "devices"})
    wifi = copy.deepcopy(model.obj(old_secrets["wifi"], "secrets.wifi", set(OLD_ROLES), set(OLD_ROLES)))
    for role, value in wifi.items():
        model.text(value, "secrets.wifi." + role, 8, 63)
    wifi["battalion"] = wifi.pop("legacy")
    devices = {}
    for node, value in old_devices.items():
        path = "devices." + str(node)
        model.obj(value, path, {"hostname", "maintenance_ipv4", "mac_base", "identity", "legacy_ssid", "overrides"}, {"hostname", "maintenance_ipv4", "mac_base"})
        device = copy.deepcopy(value)
        overrides, overridden_name = _delta(device.pop("overrides", {}), path + ".overrides")
        legacy_name = overridden_name if overridden_name is not None else common_legacy
        if "legacy_ssid" in device:
            legacy_name = model.text(device.pop("legacy_ssid"), path + ".legacy_ssid", 1, 32)
        if legacy_name != common_legacy:
            overrides["extra_ssids"] = [{**extra, "name": legacy_name}]
        if overrides:
            device["overrides"] = overrides
        device["identity"] = _identity(device, path)
        devices[node] = device
    result = {"schema_version": model.SCHEMA_VERSION, "profile": model.PROFILE, "common": common,
              "devices": devices, "secrets": {"wifi": wifi, "devices": copy.deepcopy(old_secrets["devices"])}}
    # Exercise the target's complete validation, fleet consistency and rendered
    # UCI contract. No input bytes are changed and no secrets enter diagnostics.
    for node in devices:
        model.render(model.resolve(result, node))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.config.resolve() == args.output.resolve() or args.output.exists() and args.config.samefile(args.output):
            model.fail("output", "migration must write a different file and preserve its source")
        converted = migrate(model._read(args.config, secret=True))
        model.write_private(args.output, model.canonical(converted))
        print(json.dumps({"migrated": True, "schema_version": model.SCHEMA_VERSION, "profile": model.PROFILE,
                          "device_count": len(converted["devices"]), "output_written": True}))
        return 0
    except (model.ConfigError, OSError):
        print("private configuration migration failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
