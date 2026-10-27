#!/usr/bin/env python3
"""Offline host checks; never read or modify the host's account/key files."""

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import uuid


PACKAGE = Path(__file__).resolve().parents[1]
SHARE = PACKAGE / "files/usr/share/dicttech-cli"
ACTIVATE = PACKAGE / "files/usr/libexec/dicttech-cli/activate-root-shell"


def run(args, *, env=None, check=True, cwd=None):
    return subprocess.run(args, env=env, cwd=cwd, text=True, capture_output=True,
                          check=check, timeout=30)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dicttech-cli-test-")
        self.directory = Path(self.temporary.name)
        self.home = self.directory / "home"
        self.home.mkdir(mode=0o700)
        self.cache = self.directory / "cache"
        self.env = dict(os.environ, HOME=str(self.home), ZDOTDIR=str(self.home),
                        DICTTECH_CLI_ROOT=str(SHARE), DICTTECH_CLI_CACHE=str(self.cache),
                        TERM="xterm-256color", LANG="C.UTF-8", LC_ALL="C.UTF-8")
        self.network_log = self.directory / "network-calls"
        mock_bin = self.directory / "mock-bin"
        mock_bin.mkdir()
        # Initialization and rendering a prompt must not contact the network.
        for name in ("curl", "wget", "ssh", "scp", "nc", "telnet"):
            path = mock_bin / name
            path.write_text('#!/bin/sh\nprintf "%s\\n" "$0" >> "$NETWORK_LOG"\nexit 99\n')
            path.chmod(0o755)
        self.env["NETWORK_LOG"] = str(self.network_log)
        self.env["PATH"] = str(mock_bin) + os.pathsep + os.environ["PATH"]

    def tearDown(self):
        self.assertFalse(self.network_log.exists(), "profile attempted a network command")
        self.temporary.cleanup()

    def tool(self, name):
        executable = shutil.which(name)
        if not executable:
            self.skipTest(f"host {name} is unavailable")
        return executable

    def zsh(self, script, *, interactive=True):
        flags = "-fic" if interactive else "-fc"
        return run([self.tool("zsh"), flags, 'source "$DICTTECH_CLI_ROOT/zshrc"\n' + script],
                   env=self.env, cwd=self.home)

    def test_pinned_source_hashes_and_public_key_wire_format(self):
        manifest = json.loads((SHARE / "sources.json").read_text())
        for source in manifest["sources"]:
            path = SHARE / source["installed_relative_path"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), source["sha256"])
            self.assertRegex(source["commit"], r"^[0-9a-f]{40}$")
        raw = (SHARE / "authorized_keys").read_bytes()
        git_blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        self.assertEqual(git_blob, "efd264ce90af6c7252b84377ee22800a3762de23")
        keys = set()
        count = 0
        for line in raw.decode("utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            fields = line.split()
            # Key type is first: no unexpected authorized_keys options.
            kind = fields[0]
            self.assertIn(kind, ("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256",
                                 "sk-ssh-ed25519@openssh.com"))
            wire = base64.b64decode(fields[1], validate=True)
            self.assertNotIn(wire, keys)
            keys.add(wire)
            position = 0

            def field():
                nonlocal position
                self.assertGreaterEqual(len(wire) - position, 4)
                size = struct.unpack_from(">I", wire, position)[0]
                position += 4
                self.assertGreaterEqual(len(wire) - position, size)
                value = wire[position:position + size]
                position += size
                return value

            self.assertEqual(field().decode("ascii"), kind)
            if kind == "ssh-rsa":
                self.assertGreaterEqual(int.from_bytes(field(), "big"), 3)
                self.assertGreaterEqual(int.from_bytes(field(), "big").bit_length(), 2048)
            elif kind == "ecdsa-sha2-nistp256":
                self.assertEqual(field(), b"nistp256")
                point = field()
                self.assertEqual(len(point), 65)
                self.assertEqual(point[0], 4)
            else:
                self.assertEqual(len(field()), 32)
                if kind.startswith("sk-"):
                    self.assertTrue(field().startswith(b"ssh:"))
            self.assertEqual(position, len(wire))
            count += 1
        self.assertEqual(count, 11)

    def test_zsh_interactive_profile_and_local_override(self):
        (self.home / ".zshrc.local").write_text("export DICTTECH_LOCAL_MARKER=present\n")
        result = self.zsh('''
[[ $EDITOR == vim && $VISUAL == vim && $PAGER == less ]] || exit 10
[[ $DICTTECH_LOCAL_MARKER == present && $HISTFILE == "$DICTTECH_CLI_CACHE/zsh_history" ]] || exit 11
[[ $SAVEHIST == 10000 && $aliases[vi] == vim && $aliases[t] == tmux ]] || exit 12
(( $+functions[git_prompt_info] && $+functions[ys_hg_prompt_info] )) || exit 13
zstyle -t ':omz:alpha:lib:git' async-prompt && exit 14
print -r -- READY
''')
        self.assertEqual(result.stdout.strip(), "READY", result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o700)
        self.assertTrue((self.cache / "zcompdump").is_file())

    def test_zsh_noninteractive_has_no_rc_side_effects(self):
        result = self.zsh('[[ ! -v DICTTECH_CLI_LOADED ]] || exit 20\n', interactive=False)
        self.assertEqual(result.stdout + result.stderr, "")
        self.assertFalse(self.cache.exists())

    def test_zsh_rejects_symlink_cache(self):
        victim = self.directory / "victim"
        victim.mkdir(mode=0o700)
        self.cache.symlink_to(victim, target_is_directory=True)
        result = self.zsh('[[ ! -v HISTFILE && ! -v DICTTECH_CLI_CACHE && $SAVEHIST == 0 ]] || exit 21\n')
        self.assertEqual(result.stdout + result.stderr, "")
        self.assertEqual(list(victim.iterdir()), [])

    def test_git_prompt_is_read_only_and_escapes_percent(self):
        git = self.tool("git")
        run([git, "init", "-q", str(self.home)])
        run([git, "symbolic-ref", "HEAD", "refs/heads/test%branch"], cwd=self.home)
        result = self.zsh('git_prompt_info\nprint -r -- "$(__git_prompt_git config --get oh-my-zsh.hide-info)"\ntrue\n')
        self.assertIn("test%%branch", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertFalse((self.home / ".git/index.lock").exists())

    def vim(self, commands):
        script = self.directory / "checks.vim"
        script.write_text(commands + "\nif len(v:errors) | call writefile(v:errors, $TEST_ERRORS) | cquit | endif\nqa!\n")
        self.env["TEST_ERRORS"] = str(self.directory / "vim-errors")
        result = run([self.tool("vim"), "-N", "-i", "NONE", "-n", "-es", "-u", str(SHARE / "vimrc"),
                      "-S", str(script)], env=self.env, cwd=self.home, check=False)
        errors = Path(self.env["TEST_ERRORS"])
        self.assertEqual(result.returncode, 0, (errors.read_text() if errors.exists() else result.stderr))

    def test_vim_offline_onedark_settings_and_ram_state(self):
        self.vim('''
call assert_equal('onedark', g:colors_name)
call assert_equal(1, &number)
call assert_equal(0, &modeline)
call assert_equal(4, &shiftwidth)
call assert_match('^' . escape($DICTTECH_CLI_CACHE, '\\') . '/vim-swap', &directory)
call assert_match('^' . escape($DICTTECH_CLI_CACHE, '\\') . '/viminfo$', &viminfofile)
setfiletype nix
call assert_equal(2, &l:shiftwidth)
''')
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.cache / "vim-swap").stat().st_mode & 0o777, 0o700)

    def test_vim_rejects_symlink_cache(self):
        victim = self.directory / "victim"
        victim.mkdir(mode=0o700)
        self.cache.symlink_to(victim, target_is_directory=True)
        self.vim("call assert_equal('', &viminfo)\ncall assert_equal(0, &swapfile)\n")
        self.assertEqual(list(victim.iterdir()), [])

    def test_tmux_offline_config_on_private_server(self):
        tmux = self.tool("tmux")
        socket = str(self.directory / ("tmux-" + uuid.uuid4().hex))
        args = [tmux, "-S", socket, "-f", str(SHARE / "tmux.conf")]
        try:
            result = run(args + ["new-session", "-d", "-s", "check", "sleep 30"], env=self.env)
            self.assertEqual(result.stderr, "")
            for option, value in (("prefix", "C-a"), ("mouse", "on"), ("status-position", "top"),
                                  ("status-interval", "5")):
                self.assertEqual(run([tmux, "-S", socket, "show-option", "-gv", option],
                                     env=self.env).stdout.strip(), value)
            self.assertNotIn("#(", run([tmux, "-S", socket, "show-options", "-g"], env=self.env).stdout)
        finally:
            run([tmux, "-S", socket, "kill-server"], env=self.env, check=False)


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dicttech-cli-account-test-")
        self.directory = Path(self.temporary.name)
        self.passwd = self.directory / "passwd"
        self.passwd.write_text("root:x:0:0:root:/root:/bin/ash\nuser:x:1000:1000:User:/home/user:/bin/sh\n")
        self.passwd.chmod(0o640)
        self.shell = self.directory / "zsh"
        self.shell.write_text("#!/bin/sh\nexit 0\n")
        self.shell.chmod(0o755)
        self.helper = self.directory / "activate-root-shell"
        text = ACTIVATE.read_text().replace("passwd=/etc/passwd\n", f"passwd={self.passwd}\n")
        text = text.replace("shell=/usr/bin/zsh\n", f"shell={self.shell}\n")
        self.helper.write_text(text)

    def tearDown(self):
        self.temporary.cleanup()

    def activate(self):
        return run(["sh", str(self.helper)], check=False)

    def test_default_shell_changes_once_without_account_or_mode_changes(self):
        original = self.passwd.read_text()
        self.assertEqual(self.activate().returncode, 0)
        expected = original.replace("root:/root:/bin/ash", "root:/root:/usr/bin/zsh")
        self.assertEqual(self.passwd.read_text(), expected)
        self.assertEqual(self.passwd.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.activate().returncode, 0)
        self.assertEqual(self.passwd.read_text(), expected)
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()),
                         ["activate-root-shell", "passwd", "zsh"])

    def test_custom_shell_is_preserved(self):
        original = self.passwd.read_text().replace("/bin/ash", "/bin/custom-shell")
        self.passwd.write_text(original)
        self.assertEqual(self.activate().returncode, 0)
        self.assertEqual(self.passwd.read_text(), original)

    def test_missing_or_broken_zsh_leaves_ash(self):
        original = self.passwd.read_text()
        self.shell.unlink()
        self.assertEqual(self.activate().returncode, 0)
        self.shell.write_text("#!/bin/sh\nexit 1\n")
        self.shell.chmod(0o755)
        self.assertEqual(self.activate().returncode, 0)
        self.assertEqual(self.passwd.read_text(), original)

    def test_duplicate_root_or_symlink_passwd_is_rejected(self):
        original = self.passwd.read_text()
        self.passwd.write_text(original + original.splitlines()[0] + "\n")
        self.assertNotEqual(self.activate().returncode, 0)
        victim = self.directory / "victim"
        victim.write_text(original)
        self.passwd.unlink()
        self.passwd.symlink_to(victim)
        self.assertNotEqual(self.activate().returncode, 0)
        self.assertEqual(victim.read_text(), original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
