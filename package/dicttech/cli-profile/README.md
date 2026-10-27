# DictTech command-line profile

`cli-profile` supplies the owner's familiar command-line tools without a network
request during login: Zsh, full Vim and its runtime, tmux, less, Git with HTTPS,
and rsync. It does not enable any daemon. The payload is architecture independent;
OpenWrt builds the executable dependencies for the target architecture.

The settings adapt DictXiong/dotfiles at
`e1aaeea93fbf333aebce73f4bc0d83f175577505`: its **ys** Zsh prompt, editing keys,
**OneDark** Vim colors, indentation/statusline, and tmux prefix/pane bindings.
Only the required theme files and read-only Git prompt helpers are included.
Antigen/Vundle, plugin downloads, automatic updates, login reporting, machine
identity, Git signing, Nix/systemd helpers, and tmux CPU collectors are omitted.
Prompt and tmux status rendering therefore work offline without periodic shell
commands for system statistics. The standard system `/bin/sh` remains BusyBox ash.

Theme assets and licenses are vendored at exact upstream revisions; the manifest
`files/usr/share/dicttech-cli/sources.json` records source paths and SHA256 hashes.
Changing a vendored asset requires updating its revision and hash together.
Zsh uses Oh My Zsh's `ys` theme/Git helpers at
`d2b7f557cceaddb16ba89bbf8200d8a8df35c4d8` (MIT); Vim uses OneDark at
`47bec7a6196a843dad195d2666c3ac84c6e80c78` (MIT). Both licenses ship in
`/usr/share/dicttech-cli/licenses/`.

## Image and deployment integration

The package installs the exact public `.ssh/authorized_keys2` file from that
dotfiles revision as `/usr/share/dicttech-cli/authorized_keys`:

- Git blob: `efd264ce90af6c7252b84377ee22800a3762de23`
- SHA256: `8d85e9b6f0bded08886380539156ad668d63db8063a326d89c0cffdc85c0c684`
- 11 distinct public keys, without per-key authorization options.

This file is a pinned reference. The image bootstrap/deployment tool owns the
active `/etc/dropbear/authorized_keys`, permissions, explicit replacement and
revocation policy. Installing or starting this package never appends keys to an
existing authorization file. No private key, host key, password, or token is
included.

The bootstrap may explicitly run
`/usr/libexec/dicttech-cli/activate-root-shell` after installing the package. It
checks that `/usr/bin/zsh` runs, then atomically changes root's login shell from
`/bin/ash` or `/bin/sh` to `/usr/bin/zsh`. It preserves custom shells, all other
account fields, file permissions, and the root password hash. Missing or broken
Zsh leaves the existing shell in place. It has no `postinst` hook and is never
called from a shell rc file. Bootstrap can therefore order this change relative
to its own deployment checks and rollback.

## Local customization and state

Small conffile wrappers load the managed defaults. Put additional settings in
`~/.zshrc.local`, `~/.vimrc.local`, or `~/.tmux.conf.local` to keep them separate
from future package updates. Noninteractive Zsh does not load the interactive
profile, preserving clean SSH command output.

Zsh history/completion and Vim swap/history live in a private, owner-checked
`/tmp/dicttech-cli-0` directory, so they do not repeatedly write flash and do not
persist across reboot. An unsafe cache directory disables persistent editor or
shell state. Only explicit configuration changes write overlay conffiles.

## Validation

From the firmware repository:

```sh
python3 package/dicttech/cli-profile/tests/test_cli_profile.py
```

These offline host checks validate asset hashes, all SSH public-key wire formats,
interactive/noninteractive shell behavior, theme/editor/tmux startup, private
cache handling, and account-update fixtures. tmux uses a separate test socket;
the account fixtures never touch the host's `/etc/passwd`. Host tests do not
replace a target image build and SSH/login check on the router.

The locked feed needs three small metadata fixes: optional rrsync/gitweb
interpreter dependencies are conditional on those subpackages, and Vim's huge
variant must not provide the real `vim-full` package it conflicts with. They
preserve normal Vim, Git HTTPS and rsync while avoiding unused server tooling
in the build graph. `build-ap.sh` runs `test-cli-feed-metadata.py` after feed
preparation and Kconfig compilation. The test uses the pinned original
Makefiles, actual patches, GNU Make, OpenWrt's metadata generator and Kconfig
with both failing original and passing patched controls.
