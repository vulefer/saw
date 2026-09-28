# Saw v0.2.0 compatibility policy

This document defines the interpreter and platform combinations that Saw
v0.2.0 treats as supported. A supported environment must satisfy every row
that applies to it; a successful installation elsewhere does not extend this
promise.

## Supported matrix

Saw supports the standard, GIL-enabled CPython builds listed below.
Free-threaded builds and alternative Python implementations are outside the
v0.2.0 contract.

| Operating system | Architecture | CPython versions | CI reference environment |
| --- | --- | --- | --- |
| Linux | x86-64 | 3.10, 3.11, 3.12, 3.13, 3.14 | Ubuntu 24.04 x64 |
| Windows | x86-64 | 3.10, 3.11, 3.12, 3.13, 3.14 | Windows Server 2025 x64 |
| macOS | x86-64 (Intel) | 3.10, 3.11, 3.12, 3.13, 3.14 | macOS 15 Intel |

For Linux, the binary dependency boundary is the `manylinux2014_x86_64`
wheel supplied by cbcbox. Musl-based distributions such as Alpine Linux are
not supported. For Windows, Saw relies on the `win_amd64` wheel. For macOS,
Saw relies on the Intel wheel with a macOS 15 deployment target.

Patch releases within a supported CPython minor version are included. A new
Python minor version is not supported merely because the package imports or
installs successfully; it must first pass the release suite on all three
operating systems and be added to the package metadata and this table.

## Dependency compatibility

The supported runtime dependency ranges are:

- PuLP `>=3.3.2,<4`; and
- cbcbox `>=2.929,<3`.

PuLP owns the optimization modeling API. cbcbox supplies the CBC command-line
executable used by Saw's default backend. Saw declares both packages directly
because the adapter imports cbcbox to locate that executable.

PuLP 3.3.2 is the minimum supported PuLP release because it provides the
cbcbox-based CBC installation path used by Saw. CI runs dedicated
minimum-dependency jobs on all three supported operating systems with PuLP
3.3.2 and cbcbox 2.929, installed from `requirements/minimum.txt`. The
compatibility matrix installs from `requirements/dev.txt`, which pins the
newest versions the ranges allowed when it was last regenerated, and a
separate latest-dependencies job resolves the newest non-prerelease versions
without a lock. Every job checks CBC availability and completes an
end-to-end solve through the release test suite. How bounds are chosen and
how the locks are updated is documented in
[dependencies.md](dependencies.md).

## Unsupported interpreters and platforms

Python versions below 3.10 and Python 3.15 or newer are rejected by package
metadata. Saw v0.2.0 does not support:

- PyPy, GraalPy, or other alternative Python implementations;
- free-threaded CPython builds;
- 32-bit interpreters or operating systems;
- ARM or other non-x86-64 systems, including Apple silicon;
- musl-based Linux distributions; or
- operating-system versions outside the wheel and CI scope above.

An unsupported environment may happen to work, but it receives no
compatibility guarantee and is not a release blocker. Compatibility reports
must be reproducible on a supported matrix entry before they are treated as a
Saw defect.

Advanced users may install CBC separately and place it on `PATH`; the backend
retains that lookup as a fallback. This can be useful for diagnosis, but it
does not make an otherwise unsupported interpreter or platform supported.

## Enforcement

The compatibility contract is enforced in four places:

1. `pyproject.toml` rejects unsupported Python minor versions and advertises
   the supported interpreter and operating systems.
2. The GitHub Actions compatibility matrix runs the release suite on every
   supported Python and operating-system combination.
3. The release suite checks that it is running on a declared interpreter,
   platform, and architecture and that the packaged CBC executable is usable.
4. The minimum-dependency and latest-dependency jobs verify the lower
   dependency bounds and the newest releases within the bounds, and every
   CI job tests the one built wheel, including a clean installation into a
   fresh environment. The jobs and the matrix strategy are described in
   [ci.md](ci.md).

Any change to the supported matrix must update all four enforcement points in
the same pull request.
