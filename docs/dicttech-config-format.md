# Configuration ownership and protocol

The firmware no longer maintains its own JSON schema or `ap_*` configuration
tools. The independently pinned wrtix source owns the resource model, contract,
renderer, controller and target transaction engine. Consult its `docs/usage.md`,
`docs/architecture.md` and `docs/extensions.md` for the public interface.

Nix producers generate generic UCI, file, service and package requirements.
Secrets resolve at deployment time. User-defined profiles compile through the
same ownership and conflict checks as direct resources. AP policy, complete MAC
allocation, hardware capacity and wireless security checks belong to the consumer
profile, not this firmware or the public agent.

The current runtime installs `/usr/share/wrtix/contract.json` and packaged runtime
hashes. Controllers verify these before deployment and do not replace package
files during ordinary configuration transactions. Matching firmware version
strings alone are not proof of protocol compatibility.

Use the controller pinned with the agent. The public image provides one packaged
runtime and does not include older configuration parsers or selectable legacy
runtimes. A protocol mismatch stops deployment instead of translating an old
request implicitly.
See [deployment and upgrades](xr1710g-full.md).
