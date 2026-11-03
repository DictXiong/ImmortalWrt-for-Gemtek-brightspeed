# Access and maintenance

A newly installed `full` image uses ordinary OpenWrt LAN management at
`192.168.1.1`; LAN provides DHCP, and WAN is a separate DHCP client with NAT.
There is no personal offline maintenance address or one-time shared password.
The initial account is `root` with an empty password; SSH listens on port 22
on the LAN. Connect from that trusted LAN and set authentication through LuCI
or include it in the first wrtix deployment. A DHCP address obtained on WAN
does not expose an initialization service. If the deployment changes SSH ports,
use `--port 22 --post-switch-port NEW_PORT` along with any address handoff options.

Once configured, management addresses, SSH policy and any offline maintenance
interface belong to the user's configuration. wrtix does not allocate addresses,
pick recovery ports or reset credentials on installation. A user profile may
provide DHCP management and an additional static address, but that is not a
property of the public firmware or agent.

For a configured static maintenance interface, connect to the declared trusted
port and use an unused address in its subnet with the correct mask. A direct
connection requires no gateway or DNS. This is normal Linux management, distinct
from bootloader Recovery, and cannot recover an unbootable kernel.

Do not carry old personal overlay state into a presumed fresh public setup.
Back up first, then deliberately retain or replace configuration. Existing wrtix
transactions must finish before firmware/package replacement. Required recovery
checks and the original transaction deadline remain active after controller loss.
