# SPDX-License-Identifier: GPL-2.0-only
include $(TOPDIR)/rules.mk

PKG_NAME:=ap-profile
PKG_VERSION:=2.2.0
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only
PKGARCH:=all

include $(INCLUDE_DIR)/package.mk

define Package/ap-profile
  SECTION:=net
  CATEGORY:=Network
  TITLE:=DictTech declarative configuration and enrollment runtime
  DEPENDS:=@TARGET_airoha_an7581 +cli-profile +xr-roaming +jq +uci +ubus +firewall4 +nftables-json +dropbear +uhttpd +ip-full +cpufreq +kmod-nf-conntrack-bridge +kmod-nft-bridge +kmod-nft-offload
endef

define Package/ap-profile/description
 Policy-free configuration contract, guarded resource transactions, DHCP and
 wired factory enrollment. Configured resources and policy come from an external
 producer such as Nix; no AP presets are resolved by this package. Fresh setup accepts root/password until the
 enrollment transaction confirms and activates the configured credentials.
endef

define Package/ap-profile/conffiles
/etc/dicttech-ap/
/etc/dicttech-ap-bootstrap-key/
endef

define Build/Compile
endef

define Package/ap-profile/install
	$(INSTALL_DIR) $(1)/usr/share/dicttech-ap $(1)/usr/libexec/dicttech-ap $(1)/usr/lib/dicttech-ap $(1)/etc/dicttech-ap
	chmod 0700 $(1)/etc/dicttech-ap
	$(INSTALL_DIR) $(1)/etc/dicttech-ap-bootstrap-key
	chmod 0700 $(1)/etc/dicttech-ap-bootstrap-key
	$(INSTALL_DIR) $(1)/etc/init.d $(1)/etc/dropbear
	$(CP) ./files/usr/share/dicttech-ap/. $(1)/usr/share/dicttech-ap/
	$(CP) ./files/usr/libexec/dicttech-ap/. $(1)/usr/libexec/dicttech-ap/
	$(INSTALL_BIN) ./files/usr/lib/dicttech-ap/bootstrap.sh ./files/usr/lib/dicttech-ap/services.sh $(1)/usr/lib/dicttech-ap/
	# This image explicitly selects the XR factory adapter and its enrollment assets.
	$(INSTALL_DIR) $(1)/usr/lib/dicttech-ap/factory
	$(CP) ./files/usr/lib/dicttech-ap/platforms/xr1710g/. $(1)/usr/lib/dicttech-ap/factory/
	$(INSTALL_BIN) ./files/etc/init.d/* $(1)/etc/init.d/
	$(INSTALL_CONF) $(TOPDIR)/package/dicttech/cli-profile/files/usr/share/dicttech-cli/authorized_keys $(1)/etc/dropbear/authorized_keys
	$(INSTALL_DIR) $(1)/lib/upgrade/keep.d
	$(INSTALL_DATA) ./files/lib/upgrade/dicttech-managed.sh $(1)/lib/upgrade/
	$(INSTALL_DATA) ./files/lib/upgrade/keep.d/dicttech-ap-bootstrap-key $(1)/lib/upgrade/keep.d/
endef

$(eval $(call BuildPackage,ap-profile))
