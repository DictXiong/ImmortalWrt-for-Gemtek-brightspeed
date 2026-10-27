# SPDX-License-Identifier: GPL-2.0-only
include $(TOPDIR)/rules.mk

PKG_NAME:=ap-profile
PKG_VERSION:=2.0.0
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only
PKGARCH:=all

include $(INCLUDE_DIR)/package.mk

define Package/ap-profile
  SECTION:=net
  CATEGORY:=Network
  TITLE:=DictTech versioned wired-backhaul AP profile
  DEPENDS:=@TARGET_airoha_an7581 +xr-roaming +jq +uci +ubus +firewall4 +nftables-json +dropbear +uhttpd +ip-full +cpufreq +kmod-nf-conntrack-bridge +kmod-nft-bridge +kmod-nft-offload
endef

define Package/ap-profile/description
 Versioned no-secret AP defaults, isolated first boot, provisioning guard and
 device-specific configuration support. Requires separately supplied local
 device and credential data; no shared password is included in this package.
endef

define Package/ap-profile/conffiles
/etc/dicttech-ap/
endef

define Build/Compile
endef

define Package/ap-profile/install
	$(INSTALL_DIR) $(1)/usr/share/dicttech-ap $(1)/usr/libexec/dicttech-ap $(1)/usr/lib/dicttech-ap $(1)/etc/dicttech-ap
	chmod 0700 $(1)/etc/dicttech-ap
	$(INSTALL_DIR) $(1)/etc/init.d
	$(CP) ./files/usr/share/dicttech-ap/. $(1)/usr/share/dicttech-ap/
	$(CP) ./files/usr/libexec/dicttech-ap/. $(1)/usr/libexec/dicttech-ap/
	$(CP) ./files/usr/lib/dicttech-ap/. $(1)/usr/lib/dicttech-ap/
	$(INSTALL_BIN) ./files/etc/init.d/* $(1)/etc/init.d/
endef

$(eval $(call BuildPackage,ap-profile))
