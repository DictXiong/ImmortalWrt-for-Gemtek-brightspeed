# naoki development baseline integration

The development line is `master`. This integration retains the published
`34d763d37aa0be4b927d15869b0bf6bcc376dbc5` history and merges naoki
`9a70b25731dc64822dcb24cf9a7dbe7a84ed1ba1`. The old master
`c15760c70b7ceeb85fd852d081b06c4bcac1cf5c` is already an ancestor of the
published source. Maintenance branches, existing tags and release assets are
not advanced by this integration.

naoki rewrote its history. The normal merge base is in 2016, so conflict
resolution uses the known pre-rewrite naoki baseline
`218b2b82a1c335c224a298bdf00c44453fade033` to identify the final product delta
through the published source. A three-way content merge of that delta with the
new naoki tree retains the new kernel, drivers and platform changes. The merge
commit retains both real parent histories; no grafts, replacements or rewritten
commits are used.

The Full AP/GW defaults, legacy partition offsets, read-only Factory UI,
identity/transaction guards, bridge TTL/FDB lifecycle fixes, monitor guard,
association retry protection and umdns netifd address selection remain product
requirements. Upstream already contains the split bootloader/uenv/dsd layout,
I2C binding correction and refreshed association patches; their old copies must
not override newer source contexts. Public feeds and wrtix/wrtmesh source pins
remain those from the published source. YYH is not merged.

The version `3.1.1-alpha.1` identifies development artifacts and is combined with
the exact integration commit by the existing build controller. It does not
identify the published 3.1.0 artifacts. Builds and image audits do not establish
hardware acceptance; a separate device acceptance window is required.

The inherited `sync-upstream.yml` writer is archived as `.yml.disabled` because
it assumes an unrelated `main` branch and runner. Full role candidate checks
follow pushes to `master`. Creating a release still requires an explicit manual
release dispatch; a normal push only builds candidates.

Integration repairs unwrap the five mt76 SER/MCU/PCIe/MIB patches and the
Airoha PCIe PHY patch so they modify C sources. The mt76 RX-ring hunk count
and beacon-protection context are corrected. The PCS USXGMII guard and
bridge TTL/lifecycle patches are refreshed against the newer PCS/PPE code;
upstream E2/rxlock, VPM and forwarding-path changes remain intact. These
application checks are separate from compilation and hardware behavior.

The Full manifest does not select argon or helloworld. Their four inherited
feed patches are excluded rather than adding floating feeds or weakening strict
patch checks. The two old LuCI bridge-member and msd_lite init patches remain
necessary for the fixed feed revisions and are retained from the formal source.
The former XG2010G-2G offload guard is already present in the fixed app_airoha
source and is not duplicated.

The inherited global PON watchdog overlay is packaged as
`airoha-pon-link-watchdog` and selected by the existing PON profiles. The two
runtime scripts are unchanged. XR1710G Full excludes this package, retains the
no-overlay gate, and does not acquire the PON cron entry.

The upstream `16aa9e1c9ba12971651427338f6454f99c395dbf` change disables
AN7581 Linux CPU DVFS after a reported CPU fault and excludes `cpufreq` from
this target. Full now explicitly excludes that package and audits for its
absence, retired boot inputs and compiled CPU DVFS bindings. The PLL/CPU driver
and its target guard are not restored. PSCI, critical thermal protection and
the fixed NPU/fan feed stay in place; this is separate from device acceptance.
