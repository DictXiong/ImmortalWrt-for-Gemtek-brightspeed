# SPDX-License-Identifier: GPL-2.0-only
# This file was generated entirely by AI.
# It has not been reviewed line by line by a human.
include $(TOPDIR)/rules.mk

PKG_NAME:=xr1710g-cli
PKG_VERSION:=3.0.0
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only MIT
PKGARCH:=all
include $(INCLUDE_DIR)/package.mk

define Package/xr1710g-cli
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=XR1710G Full offline shell and editor environment
  DEPENDS:=+zsh +vim-full +tmux +less +git-http +rsync
endef

define Package/xr1710g-cli/description
 Offline ys shell prompt, One Dark editor theme and tmux defaults. Interactive
 root terminals use zsh; account shells, SSH commands and failsafe remain intact.
 No authentication keys, passwords, plugin downloads or deployment dependency.
endef

define Build/Compile
endef

define Package/xr1710g-cli/install
	$(INSTALL_DIR) $(1)/usr/share/xr1710g-cli $(1)/etc/profile.d
	$(CP) ./files/usr/share/xr1710g-cli/. $(1)/usr/share/xr1710g-cli/
	$(INSTALL_DATA) ./files/etc/profile.d/80-xr1710g-cli.sh $(1)/etc/profile.d/
endef

$(eval $(call BuildPackage,xr1710g-cli))
