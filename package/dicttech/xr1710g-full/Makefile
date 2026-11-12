# SPDX-License-Identifier: GPL-2.0-only
# This file was generated entirely by AI.
# It has not been reviewed line by line by a human.
include $(TOPDIR)/rules.mk

PKG_NAME:=xr1710g-full
PKG_VERSION:=3.1.0_rc2
PKG_RELEASE:=1
PKG_CONFIG_DEPENDS:=CONFIG_XR1710G_DEFAULT_ROLE_AP
PKG_LICENSE:=GPL-2.0-only
PKGARCH:=all
include $(INCLUDE_DIR)/package.mk

define Package/xr1710g-full
  SECTION:=base
  CATEGORY:=Base system
  TITLE:=XR1710G Full identity and network defaults
endef

define Package/xr1710g-full/description
 Public full edition identity. No personal keys, passwords, site topology,
 maintenance address or enrollment policy is installed by this marker.
endef

define Package/xr1710g-full/config
	config XR1710G_DEFAULT_ROLE_AP
		bool "Default to bridged DHCP-client AP networking"
		default n
endef

define Build/Compile
endef

define Package/xr1710g-full/install
	$(INSTALL_DIR) $(1)/etc
	printf 'XR1710G Full\n' > $(1)/etc/xr1710g-full
	chmod 0644 $(1)/etc/xr1710g-full
	printf '%s\n' '$(if $(CONFIG_XR1710G_DEFAULT_ROLE_AP),ap,gateway)' > $(1)/etc/xr1710g-network-role
	chmod 0644 $(1)/etc/xr1710g-network-role
	$(INSTALL_DIR) $(1)/lib/preinit $(1)/etc/uci-defaults
	$(INSTALL_DATA) ./files/81_xr1710g_network_role $(1)/lib/preinit/81_xr1710g_network_role
	$(INSTALL_DATA) ./files/99-xr1710g-network-role $(1)/etc/uci-defaults/99-xr1710g-network-role
endef

$(eval $(call BuildPackage,xr1710g-full))
