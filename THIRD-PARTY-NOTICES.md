# Third-party notices

The container images of this repository are built for the owner's own server and are not published. This file lists
every distribution package in each image, as the image-audit job reads it from the built image.

- The licence text of each package is in the image under `/usr/share/doc/<pkg>/copyright`.
- The matching source is available from the Debian source packages of the listed versions.
- A package marked "owner-approved" carries a GPL-family licence and is named one by one in
  `scripts/image_base_allowlist.toml`. It runs as an unmodified distribution package and is not linked into Saathi code.

Regenerate this file with `uv run python scripts/third_party_notices.py` from the image-audit manifests.

<!-- image: asr -->
## Image `asr`

| Package | Version | Licence | Note |
|---|---|---|---|
| adduser | 3.134 | GPL-2+ | owner-approved |
| apt | 2.6.2 | GPL-2+ | owner-approved |
| base-files | 12.4+deb12u15 | UNKNOWN | owner-approved |
| base-passwd | 3.6.1 | GPL-2 | owner-approved |
| bash | 5.2.15-2+b13 | GPL-3+ | owner-approved |
| bsdutils | 1:2.38.1-5+deb12u3 | GPL-2+ | owner-approved |
| coreutils | 9.1-1 | GPL-3+ | owner-approved |
| dash | 0.5.12-2 | BSD-3-Clause |  |
| debconf | 1.5.82 | BSD-2-clause |  |
| debian-archive-keyring | 2023.3+deb12u2 | UNKNOWN | owner-approved |
| debianutils | 5.7-0.5~deb12u1 | GPL-2+ | owner-approved |
| diffutils | 1:3.8-4 | GPL-3+ | owner-approved |
| dpkg | 1.21.23 | GPL-2+ | owner-approved |
| e2fsprogs | 1.47.0-2+b2 | GPL-2 |  |
| findutils | 4.9.0-4 | GFDL-NIV-1.3+ | owner-approved |
| gcc-12-base | 12.2.0-14+deb12u1 | UNKNOWN |  |
| gpgv | 2.2.40-1.1+deb12u2 | GPL-3+ |  |
| grep | 3.8-5 | GPL-3+ | owner-approved |
| gzip | 1.12-1 | GPL-3+ | owner-approved |
| hostname | 3.23+nmu1 | GPL-2 | owner-approved |
| init-system-helpers | 1.65.2+deb12u1 | BSD-3-clause |  |
| libacl1 | 2.3.1-3 | GPL-2+ | owner-approved |
| libapt-pkg6.0 | 2.6.2 | GPL-2+ |  |
| libattr1 | 1:2.5.1-4 | GPL-2+ | owner-approved |
| libaudit-common | 1:3.0.9-1 | GPL-2 | owner-approved |
| libaudit1 | 1:3.0.9-1 | GPL-2 | owner-approved |
| libblkid1 | 2.38.1-5+deb12u3 | GPL-2+ | owner-approved |
| libbz2-1.0 | 1.0.8-5+b1 | BSD-variant |  |
| libc-bin | 2.36-9+deb12u14 | UNKNOWN | owner-approved |
| libc6 | 2.36-9+deb12u14 | UNKNOWN | owner-approved |
| libcap-ng0 | 0.8.3-1+b3 | LGPL-2.1+ | owner-approved |
| libcap2 | 1:2.66-4+deb12u3+b1 | BSD-3-clause or GPL-2 |  |
| libcom-err2 | 1.47.0-2+b2 | GPL-2 |  |
| libcrypt1 | 1:4.4.33-2 | UNKNOWN | owner-approved |
| libdb5.3 | 5.3.28+dfsg2-1 | Sleepycat and BSD-3-clause |  |
| libdebconfclient0 | 0.270 | BSD-2-Clause |  |
| libext2fs2 | 1.47.0-2+b2 | GPL-2 |  |
| libffi8 | 3.4.4-1 | Expat |  |
| libflac12 | 1.4.2+ds-2 | GPL-2+ or LGPL-2.1+ |  |
| libgcc-s1 | 12.2.0-14+deb12u1 | UNKNOWN | owner-approved |
| libgcrypt20 | 1.10.1-3+deb12u1 | License: |  |
| libgmp10 | 2:6.2.1+dfsg1-1.1 | GPL-2+ or LGPL-3+ | owner-approved |
| libgnutls30 | 3.7.9-2+deb12u7 | The main library is licensed under GNU Lesser |  |
| libgpg-error0 | 1.46-1 | LGPL-2.1+ |  |
| libhogweed6 | 3.8.1-2 | LGPL-3+ or GPL-2+ |  |
| libidn2-0 | 2.3.3-1+b1 | GPL-3+ |  |
| liblz4-1 | 1.9.4-1 | BSD-2-clause | owner-approved |
| liblzma5 | 5.4.1-1+deb12u2 | License: |  |
| libmd0 | 1.0.4-2 | BSD-3-clause |  |
| libmount1 | 2.38.1-5+deb12u3 | GPL-2+ | owner-approved |
| libnettle8 | 3.8.1-2 | LGPL-3+ or GPL-2+ |  |
| libogg0 | 1.3.5-3 | BSD-3-clause |  |
| libopus0 | 1.3.1-3 | BSD-3-clause |  |
| libopusenc0 | 0.2.1-2 | BSD-3-clause |  |
| libopusfile0 | 0.12-4 | BSD-3-clause |  |
| libp11-kit0 | 0.24.1-2 | BSD-3-Clause |  |
| libpam-modules | 1.5.2-6+deb12u2 | BSD-3-clause or GPL |  |
| libpam-modules-bin | 1.5.2-6+deb12u2 | BSD-3-clause or GPL |  |
| libpam-runtime | 1.5.2-6+deb12u2 | BSD-3-clause or GPL |  |
| libpam0g | 1.5.2-6+deb12u2 | BSD-3-clause or GPL |  |
| libpcre2-8-0 | 10.42-1+deb12u2 | BSD-3-clause-Cambridge with BINARY LIBRARY-LIKE PACKAGES exception |  |
| libseccomp2 | 2.5.4-1+deb12u1 | LGPL-2.1 | owner-approved |
| libselinux1 | 3.4-1+b6 | UNKNOWN |  |
| libsemanage-common | 3.4-1 | UNKNOWN | owner-approved |
| libsemanage2 | 3.4-1+b5 | UNKNOWN | owner-approved |
| libsepol2 | 3.4-2.1 | LGPL-2.1+ | owner-approved |
| libsmartcols1 | 2.38.1-5+deb12u3 | GPL-2+ | owner-approved |
| libss2 | 1.47.0-2+b2 | GPL-2 |  |
| libssl3 | 3.0.22-1~deb12u2 | Apache-2.0 |  |
| libstdc++6 | 12.2.0-14+deb12u1 | UNKNOWN | owner-approved |
| libsystemd0 | 252.39-1~deb12u2 | LGPL-2.1+ | owner-approved |
| libtasn1-6 | 4.19.0-2+deb12u1 | UNKNOWN |  |
| libtinfo6 | 6.4-4 | MIT/X11 |  |
| libudev1 | 252.39-1~deb12u2 | LGPL-2.1+ | owner-approved |
| libunistring2 | 1.0-2 | LGPL-3+ or GPL-2+ |  |
| libuuid1 | 2.38.1-5+deb12u3 | GPL-2+ | owner-approved |
| libxxhash0 | 0.8.1-1 | BSD-2-clause |  |
| libzstd1 | 1.5.4+dfsg2-5 | BSD-3-clause or GPL-2 |  |
| login | 1:4.13+dfsg1-1+deb12u2 | BSD-3-clause | owner-approved |
| logsave | 1.47.0-2+b2 | GPL-2 |  |
| mawk | 1.3.4.20200120-3.1 | GPL-2 | owner-approved |
| mount | 2.38.1-5+deb12u3 | GPL-2+ | owner-approved |
| ncurses-base | 6.4-4 | MIT/X11 |  |
| ncurses-bin | 6.4-4 | MIT/X11 |  |
| opus-tools | 0.2-1+b1 | BSD-2-clause |  |
| passwd | 1:4.13+dfsg1-1+deb12u2 | BSD-3-clause |  |
| perl-base | 5.36.0-7+deb12u4 | GPL-1+ or Artistic | owner-approved |
| sed | 4.9-1+deb12u1 | GPL-3+ | owner-approved |
| sysvinit-utils | 3.06-4 | GPL-2.0+ | owner-approved |
| tar | 1.34+dfsg-1.2+deb12u1 | GPL-3+ | owner-approved |
| tzdata | 2026f-0+deb12u1 | public-domain |  |
| usr-is-merged | 37~deb12u1 | GPL-2+ |  |
| util-linux | 2.38.1-5+deb12u3 | GPL-2+ | owner-approved |
| util-linux-extra | 2.38.1-5+deb12u3 | GPL-2+ |  |
| zlib1g | 1:1.2.13.dfsg-1 | Zlib |  |
<!-- end image: asr -->

<!-- image: speech -->
## Image `speech`

| Package | Version | Licence | Note |
|---|---|---|---|
| adduser | 3.152 | GPL-2+ | owner-approved |
| apt | 3.0.4 | GPL-2+ | owner-approved |
| base-files | 13.8+deb13u7 | GPL-2+ | owner-approved |
| base-passwd | 3.6.7 | GPL-2 | owner-approved |
| bash | 5.2.37-2+b10 | GPL-3+ | owner-approved |
| bsdutils | 1:2.41.5-0+deb13u1 | GPL-2+ | owner-approved |
| ca-certificates | 20250419+deb13u1 | GPL-2+ | owner-approved |
| coreutils | 9.7-3 | GPL-3+ | owner-approved |
| dash | 0.5.12-12 | BSD-3-Clause |  |
| debconf | 1.5.91 | BSD-2-clause |  |
| debian-archive-keyring | 2025.1 | UNKNOWN | owner-approved |
| debianutils | 5.23.2 | GPL-2+ | owner-approved |
| diffutils | 1:3.10-4 | GPL-3+ | owner-approved |
| dpkg | 1.22.22 | GPL-2+ | owner-approved |
| findutils | 4.10.0-3 | GFDL-NIV-1.3+ or GFDL-NIV-1.3+ | owner-approved |
| gcc-14-base | 14.2.0-19 | UNKNOWN | owner-approved |
| grep | 3.11-4 | GPL-3+ | owner-approved |
| gzip | 1.13-1+deb13u1 | GPL-3+ | owner-approved |
| hostname | 3.25 | GPL-2 | owner-approved |
| init-system-helpers | 1.69~deb13u1 | BSD-3-clause |  |
| libacl1 | 2.3.2-2+b1 | GPL-2+ | owner-approved |
| libapt-pkg7.0 | 3.0.4 | GPL-2+ | owner-approved |
| libattr1 | 1:2.5.2-3 | GPL-2+ | owner-approved |
| libaudit-common | 1:4.0.2-2+deb13u1 | GPL-2 | owner-approved |
| libaudit1 | 1:4.0.2-2+deb13u1 | GPL-2 | owner-approved |
| libblkid1 | 2.41.5-0+deb13u1 | GPL-2+ | owner-approved |
| libbsd0 | 0.12.2-2 | BSD-3-clause |  |
| libbz2-1.0 | 1.0.8-6 | BSD-variant |  |
| libc-bin | 2.41-12+deb13u4 | LGPL-2.1+ | owner-approved |
| libc6 | 2.41-12+deb13u4 | LGPL-2.1+ | owner-approved |
| libcap-ng0 | 0.8.5-4+b1 | LGPL-2.1+ | owner-approved |
| libcap2 | 1:2.75-10+deb13u1+b3 | BSD-3-clause or GPL-2 |  |
| libcrypt1 | 1:4.4.38-1 | UNKNOWN | owner-approved |
| libdb5.3t64 | 5.3.28+dfsg2-9 | Sleepycat and BSD-3-clause |  |
| libdebconfclient0 | 0.280 | BSD-2-Clause |  |
| libffi8 | 3.4.8-2 | Expat |  |
| libflac14 | 1.5.0+ds-2 | GPL-2+ or LGPL-2.1+ | owner-approved |
| libgcc-s1 | 14.2.0-19 | UNKNOWN | owner-approved |
| libgdbm6t64 | 1.24-2 | GPL-3+ | owner-approved |
| libgmp10 | 2:6.3.0+dfsg-3 | GPL-2+ or LGPL-3+ | owner-approved |
| libhogweed6t64 | 3.10.1-1 | LGPL-3+ or GPL-2+ | owner-approved |
| liblastlog2-2 | 2.41.5-0+deb13u1 | GPL-2+ | owner-approved |
| liblz4-1 | 1.10.0-4 | GPL-2+ | owner-approved |
| liblzma5 | 5.8.1-1+deb13u2 | License: |  |
| libmd0 | 1.1.0-2+b1 | BSD-3-clause |  |
| libmount1 | 2.41.5-0+deb13u1 | GPL-2+ | owner-approved |
| libncursesw6 | 6.5+20250216-2 | MIT/X11 |  |
| libnettle8t64 | 3.10.1-1 | LGPL-3+ or GPL-2+ | owner-approved |
| libogg0 | 1.3.5-3+b2 | BSD-3-clause |  |
| libopus0 | 1.5.2-2 | BSD-3-clause |  |
| libopusenc0 | 0.2.1-2+b2 | BSD-3-clause |  |
| libopusfile0 | 0.12-4+b3 | BSD-3-clause |  |
| libpam-modules | 1.7.0-5 | BSD-3-clause or GPL |  |
| libpam-modules-bin | 1.7.0-5 | BSD-3-clause or GPL |  |
| libpam-runtime | 1.7.0-5 | BSD-3-clause or GPL |  |
| libpam0g | 1.7.0-5 | BSD-3-clause or GPL |  |
| libpcre2-8-0 | 10.46-1~deb13u3 | BSD-3-clause-Cambridge with BINARY LIBRARY-LIKE PACKAGES exception |  |
| libreadline8t64 | 8.2-6 | GPL-3+ | owner-approved |
| libseccomp2 | 2.6.0-2 | LGPL-2.1 | owner-approved |
| libselinux1 | 3.8.1-1 | public-domain |  |
| libsemanage-common | 3.8.1-1 | LGPL-2.1+ | owner-approved |
| libsemanage2 | 3.8.1-1 | LGPL-2.1+ | owner-approved |
| libsepol2 | 3.8.1-1 | LGPL-2.1+ | owner-approved |
| libsmartcols1 | 2.41.5-0+deb13u1 | GPL-2+ | owner-approved |
| libsqlite3-0 | 3.46.1-7+deb13u2 | public-domain |  |
| libssl3t64 | 3.5.7-1~deb13u3 | Apache-2.0 |  |
| libstdc++6 | 14.2.0-19 | UNKNOWN | owner-approved |
| libsystemd0 | 257.13-1~deb13u1 | LGPL-2.1+ | owner-approved |
| libtinfo6 | 6.5+20250216-2 | MIT/X11 |  |
| libudev1 | 257.13-1~deb13u1 | LGPL-2.1+ | owner-approved |
| libuuid1 | 2.41.5-0+deb13u1 | GPL-2+ | owner-approved |
| libxxhash0 | 0.8.3-2 | BSD-2-clause |  |
| libzstd1 | 1.5.7+dfsg-1 | BSD-3-clause or GPL-2 |  |
| login | 1:4.16.0-2+really2.41.5-0+deb13u1 | GPL-2+ | owner-approved |
| login.defs | 1:4.17.4-2 | BSD-3-clause |  |
| mawk | 1.3.4.20250131-1 | GPL-2.0-only | owner-approved |
| mount | 2.41.5-0+deb13u1 | GPL-2+ | owner-approved |
| ncurses-base | 6.5+20250216-2 | MIT/X11 |  |
| ncurses-bin | 6.5+20250216-2 | MIT/X11 |  |
| netbase | 6.5 | GPL-2 | owner-approved |
| openssl | 3.5.7-1~deb13u3 | Apache-2.0 |  |
| openssl-provider-legacy | 3.5.7-1~deb13u3 | Apache-2.0 |  |
| opus-tools | 0.2-1+b3 | BSD-2-clause |  |
| passwd | 1:4.17.4-2 | BSD-3-clause |  |
| perl-base | 5.40.1-6+deb13u1 | GPL-1+ or Artistic | owner-approved |
| readline-common | 8.2-6 | GPL-3+ | owner-approved |
| sed | 4.9-2+deb13u1 | GPL-3+ | owner-approved |
| sqv | 1.3.0-3+b2 | LGPL-2.0-or-later | owner-approved |
| sysvinit-utils | 3.14-4 | GPL-2.0+ | owner-approved |
| tar | 1.35+dfsg-3.1 | GPL-3+ | owner-approved |
| tzdata | 2026f-0+deb13u1 | public-domain |  |
| util-linux | 2.41.5-0+deb13u1 | GPL-2+ | owner-approved |
| zlib1g | 1:1.3.dfsg+really1.3.1-1+b1 | Zlib |  |
<!-- end image: speech -->

Skipped or not checked: the Python packages of the project itself (see `scripts/license_audit.py --packages`).
