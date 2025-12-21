#!/usr/bin/env python
# gpyutils
# (c) 2017-2024 Michał Górny <mgorny@gentoo.org>
# SPDX-License-Identifier: GPL-2.0-or-later

import sys

from gentoopm import get_package_manager

from gpyutils.ansi import ANSI
from gpyutils.implementations import (
    Status,
    get_impl_by_name,
    get_impls_by_status,
    read_implementations,
)
from gpyutils.pycompat import EbuildMangler


def main(prog_name, *argv):
    pm = get_package_manager()

    ebuilds = []
    ops = []
    for arg in argv:
        if arg.endswith(".ebuild"):
            ebuilds.append(arg)
        else:
            ops.append(arg)

    if not ebuilds or not ops:
        print(f"Usage: {prog_name} <foo.ebuild>... <[+|-](impl|@group)>...")
        return 1

    read_implementations(pm)

    to_add = set()
    to_remove = set()
    for a in ops:
        if a[0] in ("-", "%", "+"):
            impl = a[1:]
        else:
            impl = a
        if impl.startswith("@"):
            impls = get_impls_by_status(Status[impl[1:]])
        else:
            impls = [get_impl_by_name(impl)]
        if a[0] not in ("-", "%"):
            to_add.update(impls)
        else:
            to_remove.update(impls)

    ret = 0
    for ebuild in ebuilds:
        try:
            em = EbuildMangler(ebuild)
        except (KeyError, ValueError) as exc:
            print(exc, file=sys.stderr)
            ret = 1
            continue
        with em:
            before = em.value
            for x in to_add:
                em.add(x.r1_name)
            for x in to_remove:
                em.remove(x.r1_name)
            after = em.value
            print(f"{ebuild}:")
            print(f"{ANSI.red}-PYTHON_COMPAT=({before}){ANSI.reset}")
            print(f"{ANSI.green}+PYTHON_COMPAT=({after}){ANSI.reset}")

    return ret


def entry_point():
    sys.exit(main(*sys.argv))


if __name__ == "__main__":
    sys.exit(main(*sys.argv))
