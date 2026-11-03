# SPDX-License-Identifier: GPL-2.0-only
# This file was generated entirely by AI.
# It has not been reviewed line by line by a human.
include $(TOPDIR)/rules.mk

PKG_NAME:=xr1710g-full
PKG_VERSION:=3.0.0_alpha1
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only
PKGARCH:=all
include $(INCLUDE_DIR)/package.mk

define Package/xr1710g-full
  SECTION:=base
  CATEGORY:=Base system
  TITLE:=XR1710G Full edition marker
endef

define Package/xr1710g-full/description
 Public full edition identity. No personal keys, passwords, AP topology,
 maintenance address or enrollment policy is installed by this marker.
endef

define Build/Compile
endef

define Package/xr1710g-full/install
	$(INSTALL_DIR) $(1)/etc
	printf 'XR1710G Full\n' > $(1)/etc/xr1710g-full
	chmod 0644 $(1)/etc/xr1710g-full
endef

$(eval $(call BuildPackage,xr1710g-full))
