#!/usr/bin/env python
# gpyutils
# (c) 2013-2025 Michał Górny <mgorny@gentoo.org>
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Generator

from gentoopm import get_package_manager

from gpyutils.implementations import (
    get_impl_by_name,
    get_python_impls,
    read_implementations,
)
from gpyutils.packages import PackageClass, get_package_class, group_packages


def process_pkgcheck_output(path: Path | None,
                            ) -> Generator[tuple[str, tuple[str]]]:
    if path is None:
        return

    with open(path, "r") as f:
        for line in f:
            data = json.loads(line)
            if data["__class__"] != "PythonCompatUpdate":
                continue
            yield (f"{data['category']}/{data['package']}-{data['version']}",
                   data["updates"])


def colorize(t: str, c: int) -> str:
    return f"\3{c:02d}{t}\3"


def process(pkgs,
            compat_updates: dict[str, tuple[str]],
            mirc_color: bool,
            ) -> None:
    c = colorize if mirc_color else lambda x, _: x

    key = "slotted_atom"
    for pg in group_packages(pkgs.sorted, key):
        kw_impls = []
        st_impls = []
        up_impls = []
        eapi = None
        ptype = None

        for p in reversed(pg):
            # if the newest version does not use python, stop here
            impls = get_python_impls(p)
            if impls is None:
                break

            # otherwise, try to find keywords of the newest version
            # with stable and ~arch keyword
            cl = get_package_class(p)
            if eapi is None:
                eapi = p.eapi
            if not kw_impls:
                if not cl == PackageClass.non_keyworded:
                    kw_impls = [x.short_name for x in impls]
            if not st_impls:
                if cl == PackageClass.stable:
                    st_impls = [x.short_name for x in impls]
            if not up_impls:
                up_impls = [
                    get_impl_by_name(x).short_name
                    for x in compat_updates.get(f"{p.key}-{p.version}", [])
                ]
            if ptype is None:
                ptype = "(legacy)"
                test = " "
                attest = " "

                if (
                    "distutils-r1" in p.inherits or
                    "test" not in p.restrict or
                    "pypi" in p.inherits
                ):
                    with open(p.path) as f:
                        for x in f:
                            if x.startswith("DISTUTILS_USE_PEP517="):
                                ptype = "(PEP517)"
                            elif x.startswith("PYPI_VERIFY_REPO="):
                                attest = "A"
                            elif x.startswith(("distutils_enable_tests ",
                                             "python_test()")):
                                test = "T"
                                # we do not need to scan for anything else
                                break

                if "test_network" in p.properties:
                    test = "N"
                elif "test_privileged" in p.properties:
                    test = "P"
                elif "test" in p.restrict:
                    test = "r"
                if "distutils-r1" not in p.inherits:
                    ptype = "        "
                if "pypi" not in p.inherits:
                    attest = " "

            if kw_impls and st_impls:
                break

        # if no impls found, the package is either non-python
        # or unkeyworded
        if not kw_impls and not st_impls:
            continue

        out = [f"{str(getattr(p, key)):<40}"]
        out.append("EAPI:")
        out.append(c(eapi, 11))

        assert ptype is not None
        out.append(c(ptype, 7))

        if test == "T":
            test_color = 9
        elif test == "r":
            test_color = 4
        else:
            test_color = 11
        out.append(c(attest, 9) + c(test, test_color))

        if st_impls:
            out.append(" STABLE:")
            out.extend([c(x, 9) for x in st_impls])

        kw_impls = [
            x for x in kw_impls if x not in st_impls
        ]
        if kw_impls:
            out.append("  ~ARCH:")
            out.extend([c(x, 11) for x in kw_impls])

        # deduplicate, in case -9999 was missing some impls
        up_impls = [
            x for x in up_impls if x not in kw_impls and x not in st_impls
        ]
        if up_impls:
            out.append("  UP:")
            out.extend([c(x, 7) for x in up_impls])

        print(" ".join(out))


def main():
    argp = argparse.ArgumentParser()
    argp.add_argument(
        "--mirc-color",
        action="store_true",
        help="Include mIRC color codes",
    )
    argp.add_argument(
        "--pkgcheck-output",
        type=Path,
        help="Path to pkgcheck JsonStream with PythonCompatCheck results",
    )
    args = argp.parse_args()

    pm = get_package_manager()
    read_implementations(pm)

    compat_updates = dict(process_pkgcheck_output(args.pkgcheck_output))
    process(pm.repositories["gentoo"],
            compat_updates=compat_updates,
            mirc_color=args.mirc_color)
    return 0


def entry_point():
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
