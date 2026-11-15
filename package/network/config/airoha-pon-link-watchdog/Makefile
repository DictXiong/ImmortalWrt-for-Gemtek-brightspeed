# SPDX-License-Identifier: GPL-2.0-only

include $(TOPDIR)/rules.mk

PKG_NAME:=airoha-pon-link-watchdog
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only
PKGARCH:=all

include $(INCLUDE_DIR)/package.mk

define Package/airoha-pon-link-watchdog
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Airoha PON link recovery watchdog
  DEPENDS:=@TARGET_airoha_an7581 +kmod-airoha-xpon +airoha-ponctl +airoha-pond
  HIDDEN:=1
endef

define Package/airoha-pon-link-watchdog/description
 Existing userspace PON RX-stall fallback, selected only by PON device profiles.
endef

define Build/Compile
endef

define Package/airoha-pon-link-watchdog/install
	$(INSTALL_DIR) $(1)/usr/sbin $(1)/etc/uci-defaults
	$(INSTALL_BIN) ./files/usr/sbin/pon-link-watchdog $(1)/usr/sbin/pon-link-watchdog
	$(INSTALL_BIN) ./files/etc/uci-defaults/99-pon-link-watchdog $(1)/etc/uci-defaults/99-pon-link-watchdog
endef

$(eval $(call BuildPackage,airoha-pon-link-watchdog))
