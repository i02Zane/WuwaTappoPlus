# Third-party notices

Wuwa Tappo Plus source code is licensed under GPL-3.0-only; see LICENSE.
The matching source ZIP is distributed separately alongside each Windows EXE.

The single-file Windows executable embeds these notices under `licenses/` in its runtime extraction directory:

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
