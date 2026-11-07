# Installing trex-web-gui

The common case is in the README's [Install](../README.md#install).

## Read the script first

To read the script before running any of it, or on a host with no terminal:

```
curl -fLO https://github.com/CRinATL/trex-web-gui/releases/latest/download/install.sh
```

Read it, and compare its `sha256=` line with the sha256 the release page it names lists next to the wheel. Then run:

```
sh install.sh
```

GitHub computes the release page's sha256 from the uploaded wheel, so a match shows that the script and the wheel
belong to the same published release, and that neither was swapped or damaged on the way. It does not show that the
release itself is good: the signed tag (`git verify-tag`) and the release notes are the record of that.

## Pin a version

To install v1.0.0, or to pin a version, install the wheel of a
[GitHub release](https://github.com/CRinATL/trex-web-gui/releases) by its address. Replace `<tag>` with the release's
tag (for example `v1.0.0`) and `<version>` with the same in PEP 440 form (`1.0.0`):

```
uv tool install --python 3.12 https://github.com/CRinATL/trex-web-gui/releases/download/<tag>/trex_web_gui-<version>-py3-none-any.whl
```

Then start it with `trex-web-gui serve`, which works on every release ([Older releases](#older-releases)), and the
[Command-line options](../README.md#command-line-options) you need.

With the [GitHub CLI](https://cli.github.com/), `gh release download <tag> -R CRinATL/trex-web-gui --pattern '*.whl'`
fetches the wheel instead, and `uv tool install --python 3.12 ./trex_web_gui-<version>-py3-none-any.whl` installs it.
`make wheel` builds one from a checkout.

A host without Python 3.12 (Ubuntu 22.04 has 3.10) needs nothing more: uv downloads a Python 3.12 from GitHub and the
dependencies from PyPI.

## Older releases

- **1.1.0 and earlier need the `serve` command:** `trex-web-gui serve [options]`, and `trex-web-gui serve --help`
  for the options. Without it they stop with `the following arguments are required: cmd`. 1.1.1 and later serve by
  default and still accept `serve`, so `trex-web-gui serve` works on every release.
- **The 1.1.0 installer does not add `~/.local/bin` to your `PATH`.** Re-run the installer, which installs the latest
  release and adds the directory, or see
  [The `trex-web-gui` command and `PATH`](#the-trex-web-gui-command-and-path).
- **v1.0.0 has no installer.** Install it by its wheel ([Pin a version](#pin-a-version)).

## Offline host

A host that cannot reach PyPI installs from a wheelhouse, a directory of the wheel and its dependencies.

1. On a connected machine, build the wheelhouse:

   ```
   uvx pip download --dest wheelhouse --only-binary=:all: --python-version 3.12 --implementation cp \
     --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 ./trex_web_gui-<version>-py3-none-any.whl
   ```

   The wheel ends up inside it, as `wheelhouse/trex_web_gui-<version>-py3-none-any.whl`. Copy `wheelhouse/` to the host.

2. Give the host a Python 3.12, one of:
   - if it reaches GitHub: `uv python install 3.12` on the host, the only step that uses the network;
   - if it does not: on a connected machine, `uv python install 3.12`, then
     `cd "$(uv python dir)" && tar -czf ~/cpython-3.12.tar.gz cpython-3.12.*-linux-x86_64-gnu`; copy the tarball
     to the host and unpack it there with
     `mkdir -p "$(uv python dir)" && tar -xzf cpython-3.12.tar.gz -C "$(uv python dir)"`
     (uv's Pythons can move, so the two machines' paths and user names may differ);
   - or the distribution's `python3.12` package, where it has one, from local media.

3. On the host, from the directory that holds `wheelhouse/` (both paths below are relative):

   ```
   uv tool install --offline --no-index --find-links wheelhouse --python 3.12 wheelhouse/trex_web_gui-<version>-py3-none-any.whl
   ```

   `wheelhouse/` is not needed after the install.

## Where the install puts things

`uv tool install` installs for the user who runs it, with no `sudo`:

- trex-web-gui and its dependencies go into their own environment,
  `~/.local/share/uv/tools/trex-web-gui/` (`uv tool dir`; `UV_TOOL_DIR` moves it);
- the `trex-web-gui` command is linked into `~/.local/bin` (`uv tool dir --bin`; `UV_TOOL_BIN_DIR` moves it), which must
  be on `PATH` ([The `trex-web-gui` command and `PATH`](#the-trex-web-gui-command-and-path));
- the packages also land in uv's cache, `~/.cache/uv/` (`uv cache dir`);
- the environment runs on the Python 3.12 that `--python 3.12` found, uv's own under `~/.local/share/uv/python/`
  (`uv python dir`) or the system's `python3.12`, so that Python has to stay installed.

`uv tool uninstall trex-web-gui` removes the environment and the command.

## The `trex-web-gui` command and `PATH`

The command is in `~/.local/bin` (`uv tool dir --bin` prints the directory). If `trex-web-gui` answers
`command not found`, that directory is not on your `PATH`.

The installer adds it to your shell profile, but a script run as `curl … | sh` cannot change the `PATH` of the
terminal that started it. If the installer said it updated your profile, open a new terminal, or run the
`export PATH=…` line it printed in the current one.

Otherwise, add the directory to your shell profile yourself, in one of two ways:

- Run `uv tool update-shell`. It adds the directory to your shell's startup files.
- Or add this line yourself, to `~/.profile` for a login shell, `~/.bashrc` for bash, or `~/.zshenv` for zsh:

  ```
  export PATH="$HOME/.local/bin:$PATH"
  ```

Then open a new terminal, or run that line in the current one. `command -v trex-web-gui` prints the command's path
once it is found. Until then, `~/.local/bin/trex-web-gui` runs it by its full path.
