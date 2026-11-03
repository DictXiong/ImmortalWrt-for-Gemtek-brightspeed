# This file was generated entirely by AI.
# It has not been reviewed line by line by a human.
# Sourced by OpenWrt /etc/profile, including zsh's stock /etc/zsh/zprofile.
# Keep account records, remote commands, recovery shells and other users intact.
case $- in *i*) ;; *) return 0;; esac
[ -t 0 ] && [ -t 1 ] || return 0
[ "$(id -u)" = 0 ] && [ -z "${FAILSAFE:-}" ] || return 0
if [ -z "${ZSH_VERSION:-}" ]; then
 case "${0##*/}" in ash|-ash|sh|-sh) ;; *) return 0;; esac
fi
[ -x /usr/bin/zsh ] && /usr/bin/zsh -f -c ':' >/dev/null 2>&1 || return 0
export XR1710G_CLI_ROOT=/usr/share/xr1710g-cli
export ZDOTDIR=/usr/share/xr1710g-cli
export VIMINIT='source /usr/share/xr1710g-cli/vimrc'
# Already-running zsh will read ZDOTDIR/.zshrc after its login profile returns.
[ -z "${ZSH_VERSION:-}" ] || return 0
exec /usr/bin/zsh -l
