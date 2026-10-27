#!/usr/bin/env python3
"""Private v1→v2 migration tests, using synthetic credentials only."""
from pathlib import Path
import copy
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "ap-migrate-v1.py"
spec = importlib.util.spec_from_file_location("dicttech_migrate", SOURCE)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
HASH = "$6$fixturesalt$" + "a" * 86


def old(count=2):
    devices = {f"ap_{i}": {"hostname": f"ap-{i}", "maintenance_ipv4": f"192.168.250.{i + 1}",
                            "mac_base": f"02:aa:bb:10:{i:02x}:00"} for i in range(count)}
    return {"schema_version": 1, "profile": "dicttech-ap-v1", "common": {}, "devices": devices,
            "secrets": {"wifi": {key: key + "-migration-fixture" for key in m.OLD_ROLES},
                        "devices": {key: {"root_password_hash": HASH} for key in devices}}}


class Migration(unittest.TestCase):
    def test_defaults_secret_values_and_old_derived_mac_pins_preserved(self):
        for count in [1, 2]:
            document = old(count); before = copy.deepcopy(document); result = m.migrate(document)
            self.assertEqual(document, before)
            self.assertEqual(result["schema_version"], 2)
            self.assertEqual(result["profile"], "dicttech-ap-v2")
            self.assertEqual(result["common"]["extra_ssids"], [{"id": "battalion", "name": "327Battalion", "password_ref": "battalion"}])
            self.assertEqual(result["secrets"]["wifi"]["battalion"], document["secrets"]["wifi"]["legacy"])
            self.assertEqual(result["secrets"]["devices"], document["secrets"]["devices"])
            for role in ["main", "guest", "iot"]:
                self.assertEqual(result["secrets"]["wifi"][role], document["secrets"]["wifi"][role])
            for node, device in document["devices"].items():
                identity = result["devices"][node]["identity"]
                self.assertEqual(identity["mlds"]["battalion"], m._old_mac(device["mac_base"], 0x40))
                expected_links = [m._old_mac(device["mac_base"], 0x41), m._old_mac(device["mac_base"], 0x42)]
                self.assertEqual(identity["bssids"]["battalion"], dict(zip(("2g", "5g"), expected_links)))
                state = m.model.resolve(result, node)
                profile = next(p for p in state["ssids"] if p["key"] == "battalion")
                self.assertEqual(profile["mld_mac"], identity["mlds"]["battalion"])
                self.assertEqual(profile["bssids"], expected_links)
                self.assertEqual(profile["iface"], "ap-mld2")
        with self.assertRaises(m.model.ConfigError):
            m.migrate(old(0))

    def test_effective_override_precedence_and_other_settings_preserved(self):
        value = old()
        value["common"] = {"ssids": {"legacy": {"name": "CommonOld"}, "main": {"six_ghz": False}},
                           "radios": {"radio1": {"channel": 36}}}
        value["devices"]["ap_0"]["overrides"] = {"ssids": {"legacy": {"name": "DeviceOld"}}, "timezone": "UTC"}
        value["devices"]["ap_0"]["legacy_ssid"] = "ShorthandWins"
        result = m.migrate(value)
        self.assertEqual(result["devices"]["ap_0"]["overrides"], {"timezone": "UTC", "extra_ssids": [
            {"id": "battalion", "name": "ShorthandWins", "password_ref": "battalion"}]})
        self.assertEqual(next(p for p in m.model.resolve(result, "ap_1")["ssids"] if p["key"] == "battalion")["name"], "CommonOld")
        self.assertEqual(m.model.resolve(result, "ap_0")["peers"]["battalion"], [])
        self.assertEqual(result["common"]["radios"], value["common"]["radios"])
        self.assertEqual(result["common"]["ssids"], {"main": {"six_ghz": False}})
        value["devices"]["ap_0"].pop("legacy_ssid")
        self.assertEqual(next(p for p in m.model.resolve(m.migrate(value), "ap_0")["ssids"] if p["key"] == "battalion")["name"], "DeviceOld")
        value["devices"]["ap_0"]["legacy_ssid"] = "CommonOld"
        self.assertNotIn("extra_ssids", m.migrate(value)["devices"]["ap_0"]["overrides"])

    def test_explicit_identity_pins_are_renamed_without_changing_addresses(self):
        value = old(1)
        value["devices"]["ap_0"]["identity"] = {"bridge": "02:cc:aa:01:00:00", "duid": "0003000102ccaa010000",
            "mlds": {"legacy": "02:cc:aa:01:00:40", "main": "02:cc:aa:01:00:50"},
            "bssids": {"legacy": ["02:cc:aa:01:00:41", "02:cc:aa:01:00:42"],
                       "main": ["02:cc:aa:01:00:50", "02:cc:aa:01:00:52", "02:cc:aa:01:00:53"]}}
        before = copy.deepcopy(value["devices"]["ap_0"]["identity"])
        after = m.migrate(value)["devices"]["ap_0"]["identity"]
        for group in ["mlds", "bssids"]:
            previous = before[group].pop("legacy")
            self.assertEqual(after[group]["battalion"], dict(zip(("2g", "5g"), previous)) if group == "bssids" else previous)
            self.assertEqual({k: v for k, v in after[group].items() if k != "battalion"}, before[group])
        self.assertEqual(after["bridge"], before["bridge"])
        self.assertEqual(after["duid"], before["duid"])

    def test_migrated_identity_does_not_force_custom_ssid_or_band_set(self):
        result = m.migrate(old(1))
        expected = m.model.resolve(result, "ap_0")["device"]["identity"]
        item = result["common"]["extra_ssids"][0]
        result["common"]["extra_ssids"] = [{**item, "bands": ["5g"]}]
        single = m.model.resolve(result, "ap_0")
        self.assertEqual(single["device"]["identity"], expected)
        self.assertEqual(next(p for p in single["ssids"] if p["key"] == "battalion")["bssids"], expected["bssids"]["battalion"][1:2])
        result["common"]["extra_ssids"] = []
        removed = m.model.resolve(result, "ap_0")
        self.assertNotIn("battalion", {p["key"] for p in removed["ssids"]})
        self.assertEqual(removed["device"]["identity"], expected)
        result["common"]["extra_ssids"] = [{**item, "bands": ["2g", "5g", "6g"]}]
        restored = next(p for p in m.model.resolve(result, "ap_0")["ssids"] if p["key"] == "battalion")
        self.assertEqual(restored["bssids"], expected["bssids"]["battalion"])

    def test_wrong_version_unknown_old_fields_and_invalid_values_rejected(self):
        variants = []
        value = old(); value["schema_version"] = 2; value["profile"] = "dicttech-ap-v2"; variants.append(value)
        for update in [lambda d: d["common"].update(extra_ssids=[]),
                       lambda d: d["common"].update(ssids={"legacy": {"hidden": True}}),
                       lambda d: d["devices"]["ap_0"].update(unknown=True),
                       lambda d: d["devices"]["ap_0"].update(identity={"mlds": {"battalion": "02:aa:bb:00:00:01"}}),
                       lambda d: d["secrets"]["wifi"].update(extra="extra-fixture-secret"),
                       lambda d: d["devices"]["ap_0"].update(legacy_ssid="bad\nname")]:
            value = old(); update(value); variants.append(value)
        for value in variants:
            with self.assertRaises(m.model.ConfigError):
                m.migrate(value)

    def test_cli_private_output_source_unchanged_and_values_not_logged(self):
        value = old(1)
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "source", Path(tmp) / "output"
            raw = json.dumps(value).encode(); source.write_bytes(raw); source.chmod(0o600)
            command = ["python3", str(SOURCE), "--config", str(source), "--output", str(output)]
            q = subprocess.run(command, capture_output=True)
            self.assertEqual(q.returncode, 0, q.stderr)
            self.assertEqual(source.read_bytes(), raw)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            for secret in [*value["secrets"]["wifi"].values(), HASH]:
                self.assertNotIn(secret.encode(), q.stdout + q.stderr)
            source.chmod(0o644)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            source.chmod(0o600)
            same = ["python3", str(SOURCE), "--config", str(source), "--output", str(source)]
            self.assertNotEqual(subprocess.run(same, capture_output=True).returncode, 0)
            self.assertEqual(source.read_bytes(), raw)
            output.unlink(); os.link(source, output)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(source.read_bytes(), raw)
            output.unlink(); source.write_text('{"schema_version":1,"schema_version":1}')
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
