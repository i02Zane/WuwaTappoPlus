# Third-party notices

Wuwa Tappo Plus source code is licensed under GPL-3.0-only; see LICENSE.
Matching source is available through the release tag archives.

The installed application retains these notices under `_internal/licenses/`:

- CPython and its standard-library/runtime components. Their license and
  incorporated-software notices are in `licenses/Python.txt`.
- Additional CPython incorporated-software notices from the upstream Python
  documentation are in `licenses/Python-components.txt`.
- OpenSSL 3, used by CPython's SSL and hashing modules, is distributed under
  Apache-2.0; see `licenses/OpenSSL.txt`.
- The PyInstaller bootloader and runtime support. Its GPL license and the
  bootloader exception are in `licenses/PyInstaller.txt`.

These components retain their own copyright notices and license terms.
The vendored documentation notices are from CPython 3.13.12 and OpenSSL 3.0.18.
When changing the build runtime, review and update these alongside its own license.
PyInstaller, setuptools and build are also used to build the release; their
build dependencies are not runtime requirements of Wuwa Tappo Plus.

Wuwa Tappo itself is a separate third-party application. Its executable,
artwork and game assets are not included in this project or the release executable.

## JavaScript build tooling

esbuild is used only during development to bundle project-owned scripts. No CloudBase SDK or other third-party JavaScript runtime is shipped.

## Windows installer

Inno Setup builds the installer and uninstaller. Copyright (C) 1997-2026 Jordan Russell; portions Copyright (C) 2000-2026 Martijn Laan. See `licenses/InnoSetup.txt` and https://jrsoftware.org/.

The Simplified Chinese translation retains its upstream notices in `packaging/ChineseSimplified.isl`, from Inno Setup tag `is-6_7_4`, `Files/Languages/Unofficial/ChineseSimplified.isl`, maintained by Zhenghan Yang.
