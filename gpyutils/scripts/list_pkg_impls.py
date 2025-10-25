#!/usr/bin/env python
# gpyutils
# (c) 2013-2025 Michał Górny <mgorny@gentoo.org>
# SPDX-License-Identifier: GPL-2.0-or-later

import sys

from gentoopm import get_package_manager

from gpyutils.implementations import get_python_impls, read_implementations
from gpyutils.packages import PackageClass, get_package_class, group_packages


def process(pkgs):
    key = "slotted_atom"
    for pg in group_packages(pkgs.sorted, key):
        kw_impls = []
        st_impls = []
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

        print(" ".join(out))


def main():
    pm = get_package_manager()
    read_implementations(pm)

    process(pm.repositories["gentoo"])
    return 0


def entry_point():
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
