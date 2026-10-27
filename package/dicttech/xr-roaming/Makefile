# SPDX-License-Identifier: GPL-2.0-only

include $(TOPDIR)/rules.mk

PKG_NAME:=xr-roaming
PKG_VERSION:=6.0.0
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only
PKGARCH:=all

include $(INCLUDE_DIR)/package.mk

define Package/xr-roaming
  SECTION:=net
  CATEGORY:=Network
  SUBMENU:=WirelessAPD
  TITLE:=XR1710G wired-backhaul neighbor exchange
  DEPENDS:=@TARGET_airoha_an7581 +hostapd-utils +jq +curl +coreutils-timeout +flock +uci +ubus +uhttpd
endef

define Package/xr-roaming/description
  Validated multi-link neighbor-report exchange for the C7 XR1710G AP profile.
  Maintains separate candidate lists for up to seventeen configured ESSes over wired LAN.
  Does not change wireless configuration, select channels or steer clients.
endef

define Package/xr-roaming/conffiles
/etc/config/xr_roaming
endef

define Build/Prepare
endef

define Build/Configure
endef

define Build/Compile
endef

define Package/xr-roaming/preinst
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] && exit 0
[ "$${IPKG_NO_SCRIPT}" = 1 ] && exit 0
[ -x /etc/init.d/xr_roaming ] || exit 0
/etc/init.d/xr_roaming stop || exit 1
# An old shell must not continue running while its jq modules are replaced.
base=/tmp/xr-roaming-private
[ ! -L "$$base" ] || exit 1
count=0
# The main lock and every old/new profile lock live at depth zero or one.
# Do not infer the old worker set from the newly supplied UCI configuration.
for state in "$$base" "$$base"/*; do
	if [ -L "$$state" ]; then
		echo 'xr-roaming: refusing a symlinked runtime lock directory' >&2
		/etc/init.d/xr_roaming start >/dev/null 2>&1 || true
		exit 1
	fi
	[ -d "$$state" ] || continue
	count=$$((count + 1))
	if [ "$$count" -gt 64 ]; then
		echo 'xr-roaming: too many stale runtime directories' >&2
		/etc/init.d/xr_roaming start >/dev/null 2>&1 || true
		exit 1
	fi
	if [ -L "$$state" ] || [ -L "$$state/lock" ]; then
		echo 'xr-roaming: refusing a symlinked runtime lock' >&2
		/etc/init.d/xr_roaming start >/dev/null 2>&1 || true
		exit 1
	fi
	[ -f "$$state/lock" ] || continue
	attempt=0
	# C7 BusyBox flock has -n but no -w; dependencies may not yet be upgraded.
	while ! (flock -n 9) 9>"$$state/lock"; do
		attempt=$$((attempt + 1))
		if [ "$$attempt" -ge 20 ]; then
			echo 'xr-roaming: old workers have not released their locks' >&2
			/etc/init.d/xr_roaming start >/dev/null 2>&1 || true
			exit 1
		fi
		sleep 1
	done
done
exit 0
endef

define Package/xr-roaming/install
	$(INSTALL_DIR) $(1)/etc/config $(1)/etc/init.d
	$(INSTALL_CONF) ./files/xr_roaming.config $(1)/etc/config/xr_roaming
	$(INSTALL_BIN) ./files/xr_roaming.init $(1)/etc/init.d/xr_roaming
	$(INSTALL_DIR) $(1)/usr/lib/xr-roaming
	$(INSTALL_BIN) ./files/helper.sh $(1)/usr/lib/xr-roaming/helper.sh
	$(INSTALL_DATA) ./files/common.jq ./files/cli-report.jq $(1)/usr/lib/xr-roaming/
	$(INSTALL_DIR) $(1)/www
	$(LN) /tmp/xr-roaming-public $(1)/www/xr-roaming
endef

$(eval $(call BuildPackage,xr-roaming))
