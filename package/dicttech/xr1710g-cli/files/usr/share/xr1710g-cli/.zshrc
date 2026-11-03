# SPDX-License-Identifier: GPL-2.0-only
# Offline OpenWrt adaptation of DictXiong/dotfiles .zshrc2; see sources.json.
[[ -o interactive ]] || return 0
[[ ${XR1710G_CLI_LOADED:-0} == 1 ]] && return 0
typeset -g XR1710G_CLI_LOADED=1
typeset -g XR1710G_CLI_ROOT=${XR1710G_CLI_ROOT:-/usr/share/xr1710g-cli}

export EDITOR=vim VISUAL=vim PAGER=less
export LESS='-FRX'
export LANG=${LANG:-C.UTF-8}
export LC_ALL=${LC_ALL:-C.UTF-8}
export LESS_TERMCAP_md=$'\e[01;33m'

# History/completion caches live in a private RAM directory, never the overlay.
typeset -g XR1710G_CLI_CACHE=${XR1710G_CLI_CACHE:-/tmp/xr1710g-cli-$EUID}
typeset -A _xr1710g_cache_stat
if [[ ! -e $XR1710G_CLI_CACHE && ! -L $XR1710G_CLI_CACHE ]]; then
  (umask 077; command mkdir -- "$XR1710G_CLI_CACHE") 2>/dev/null
fi
if [[ -d $XR1710G_CLI_CACHE && ! -L $XR1710G_CLI_CACHE ]] &&
   zmodload zsh/stat 2>/dev/null &&
   zstat -H _xr1710g_cache_stat -- "$XR1710G_CLI_CACHE" 2>/dev/null &&
   (( _xr1710g_cache_stat[uid] == EUID && (_xr1710g_cache_stat[mode] & 8#77) == 0 )); then
  HISTFILE=$XR1710G_CLI_CACHE/zsh_history
  SAVEHIST=10000
  autoload -Uz compinit
  compinit -d "$XR1710G_CLI_CACHE/zcompdump"
else
  unset HISTFILE XR1710G_CLI_CACHE
  SAVEHIST=0
  autoload -Uz compinit
  compinit -D
fi
unset _xr1710g_cache_stat
HISTSIZE=10000
setopt HIST_IGNORE_DUPS HIST_REDUCE_BLANKS SHARE_HISTORY
setopt AUTO_CD INTERACTIVE_COMMENTS PROMPT_SUBST

bindkey -e
bindkey '^b' beginning-of-line
bindkey '\eOH' beginning-of-line
bindkey '\e[H' beginning-of-line
bindkey '\e[1~' beginning-of-line
bindkey '^e' end-of-line
bindkey '\eOF' end-of-line
bindkey '\e[F' end-of-line
bindkey '\e[4~' end-of-line
bindkey '\ef' forward-word
bindkey '\e[5C' forward-word
bindkey '\e\e[C' forward-word
bindkey '\e[1;5C' forward-word
bindkey '\eb' backward-word
bindkey '\e[5D' backward-word
bindkey '\e\e[D' backward-word
bindkey '\e[1;5D' backward-word
bindkey '\ed' delete-char
autoload -Uz zcalc

alias vi=vim t=tmux
alias tmux='tmux -f /usr/share/xr1710g-cli/tmux.conf'
alias l='ls -lAh'
if command ls --color=auto -d /dev/null >/dev/null 2>&1; then
  alias ls='ls --color=auto'
fi
alias p114='ping 114.114.114.114'
alias p666='ping6 240c::6666'

# Ship the user's actual ys theme, without installing Oh My Zsh or Antigen.
# Its Git helpers run synchronously/read-only and never fetch remote objects.
autoload -Uz colors
colors
zmodload zsh/terminfo 2>/dev/null || true
zstyle ':omz:alpha:lib:git' async-prompt no
typeset -g DISABLE_UNTRACKED_FILES_DIRTY=true
source "$XR1710G_CLI_ROOT/zsh/git.zsh"
svn_prompt_info() { return 0; }
source "$XR1710G_CLI_ROOT/zsh/ys.zsh-theme"
if (( ! $+commands[hg] )); then
  ys_hg_prompt_info() { return 0; }
fi
if [[ ${TERM:-dumb} == dumb ]]; then
  PROMPT='%n@%m:%~%# '
fi

[[ ! -r $HOME/.zshrc.local ]] || source "$HOME/.zshrc.local"
