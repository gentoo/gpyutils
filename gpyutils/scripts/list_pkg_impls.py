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


def process_pkgcheck_output(path: Path | None) -> Generator[tuple[str, tuple[str]]]:
    if path is None:
        return

    with open(path, "r") as f:
        for l in f:
            data = json.loads(l)
            if data["__class__"] != "PythonCompatUpdate":
                continue
            yield (f"{data['category']}/{data['package']}-{data['version']}",
                   data["updates"])


def process(pkgs, compat_updates: dict[str, tuple[str]]) -> None:
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

                if "distutils-r1" in p.inherits or "test" not in p.restrict:
                    with open(p.path) as f:
                        for x in f:
                            if x.startswith("DISTUTILS_USE_PEP517="):
                                ptype = "(PEP517)"
                            if x.startswith(("distutils_enable_tests ",
                                             "python_test()")):
                                test = "T"
                                # we do not need to scan for anything else
                                break

                if "test" in p.restrict:
                    test = "r"
                if "distutils-r1" not in p.inherits:
                    ptype = "        "

            if kw_impls and st_impls:
                break

        # if no impls found, the package is either non-python
        # or unkeyworded
        if not kw_impls and not st_impls:
            continue

        out = [f"{str(getattr(p, key)):<40}"]
        out.append("EAPI:")
        out.append(eapi)

        assert ptype is not None
        out.append(ptype)

        out.append(test)

        if st_impls:
            out.append(" STABLE:")
            out.extend(st_impls)

        # print only extra impls
        for impl in list(kw_impls):
            if impl in st_impls:
                kw_impls.remove(impl)

        if kw_impls:
            out.append("  ~ARCH:")
            out.extend(kw_impls)

        # deduplicate, in case -9999 was missing some impls
        up_impls = [
            x for x in up_impls if x not in kw_impls and x not in st_impls
        ]
        if up_impls:
            out.append("  UP:")
            out.extend(up_impls)

        print(" ".join(out))


def main():
    argp = argparse.ArgumentParser()
    argp.add_argument(
        "--pkgcheck-output",
        type=Path,
        help="Path to pkgcheck JsonStream with PythonCompatCheck results",
    )
    args = argp.parse_args()

    pm = get_package_manager()
    read_implementations(pm)

    compat_updates = dict(process_pkgcheck_output(args.pkgcheck_output))
    process(pm.repositories["gentoo"], compat_updates)
    return 0


def entry_point():
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
