# Prepare public source permissions

A writable source mode can survive a feed's install rules and build cache into
an APK. In particular, rpcd refuses a world-writable ucode module: correct module
bytes and dependencies do not make mode `0666` safe. Use a restrictive build
umask (`022`) and normalize checked-out public source permissions before
packaging:

```sh
python3 scripts/dicttech/prepare-source-permissions.py \
  --source-root /build/source
python3 scripts/dicttech/prepare-source-permissions.py \
  --source-root /build/source --check
```

The first command restores permissions. `--check` changes nothing and exits
with status 1 when permissions differ. Both emit counts per repository; they
do not print file contents, filenames or private checkout paths.

The tool reads the main repository's Git index and
`configs/dicttech-feeds.conf`. Each feed must already exist at its pinned Git
commit; both the default feed directory and `--directory=NAME` are supported.
There is no download, checkout, reset or network operation. Local source
patches are retained; indexed file modes, rather than working-tree executable
bits, provide the permission policy:

- Indexed ordinary files become `0644` (`100644`) or `0755` (`100755`).
- Directories containing indexed entries, plus the `feeds` container, become
  `0755`. A repository root only loses group/other write permissions, retaining
  an intentionally private root such as `0700`.
- Indexed symbolic links are checked but never followed or chmodded. A symbolic
  link replacing a regular file or any selected parent directory is refused.
  Tracked files with hard links are also refused to prevent changing an inode
  shared outside the selected checkout.
- Missing files, unexpected file types, unmerged indexes, submodules, unsafe
  feed paths and mismatched feed commits stop preparation before any chmod.
  Preparation should run while the source trees are otherwise idle. A later
  I/O error or concurrent replacement can leave earlier permission repairs in
  place; re-run the check after resolving it. The operation is not atomic.

Untracked files, `.git` internals, private signing keys and generated
`build_dir`, `staging_dir` and `bin` caches are outside the selection. New public
source files must be added to the Git index before this check can cover them.
The tool changes permissions only, never bytes or ownership. Indexed public
files previously restricted to `0600`, and their non-root parent directories
previously restricted to `0700`, are deliberately normalized to the public Git
policy. Private material must remain untracked/outside those source paths.

Fixing the checkout does **not** repair previously assembled files, cached
install stamps or APKs. A cache built from polluted permissions must be cleaned
and repackaged, then the actual image rootfs must pass `check-ap-rootfs.py` and
the release APK set must pass `check-ap-packages.py`. Do not merely chmod the
assembled rootfs: that leaves package/cache artifacts capable of reintroducing
the bad modes. Cache cleanup and rebuild are explicit build steps, outside this
source-only tool.

Run the offline Git fixtures:

```sh
python3 scripts/dicttech/tests/test_source_permissions.py
```
