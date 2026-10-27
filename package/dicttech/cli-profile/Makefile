# SPDX-License-Identifier: GPL-2.0-only
include $(TOPDIR)/rules.mk

PKG_NAME:=cli-profile
PKG_VERSION:=1.0.0
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only MIT
PKGARCH:=all

include $(INCLUDE_DIR)/package.mk

define Package/cli-profile
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=DictTech offline command-line profile
  DEPENDS:=+zsh +vim-full +tmux +less +git-http +rsync
endef

define Package/cli-profile/description
 Pinned DictTech public SSH trust material and an offline adaptation of the
 user's ys, OneDark and tmux settings. No plugin manager, login network request
 or updater runs. Host keys and other private identities are never included.
endef

define Package/cli-profile/conffiles
/root/.zshrc
/root/.vimrc
/root/.tmux.conf
endef

define Build/Compile
endef

define Package/cli-profile/install
	$(INSTALL_DIR) $(1)/usr/share/dicttech-cli $(1)/usr/libexec/dicttech-cli $(1)/root
	$(CP) ./files/usr/share/dicttech-cli/. $(1)/usr/share/dicttech-cli/
	$(INSTALL_BIN) ./files/usr/libexec/dicttech-cli/activate-root-shell $(1)/usr/libexec/dicttech-cli/
	$(INSTALL_CONF) ./files/root/.zshrc $(1)/root/.zshrc
	$(INSTALL_CONF) ./files/root/.vimrc $(1)/root/.vimrc
	$(INSTALL_CONF) ./files/root/.tmux.conf $(1)/root/.tmux.conf
endef

$(eval $(call BuildPackage,cli-profile))
