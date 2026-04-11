#!/usr/bin/env python
# gpyutils
# (c) 2013-2026 Michał Górny <mgorny@gentoo.org>
# SPDX-License-Identifier: GPL-2.0-or-later

import argparse
import functools
import os.path
import re
import sys

from gentoopm import get_package_manager
from gentoopm.basepm.atom import PMAtom
from gentoopm.exceptions import InvalidBashCodeError

from gpyutils.ansi import ANSI
from gpyutils.implementations import (
    get_impl_by_name,
    get_python_impls,
    read_implementations,
)
from gpyutils.packages import PackageClass, get_package_class, group_packages
from gpyutils.pycompat import EbuildMangler


def obfuscate_email(email):
    if email.endswith("@gentoo.org"):
        return email.split("@")[0]
    return email.replace("@", "[at]")


def print_package(p, pkg_print, maintainers=False):
    # in case stdout & stderr goes to the same console,
    # clean up the line before printing
    sys.stderr.write("%s\r" % ANSI.clear_line)
    out = str(pkg_print(p))
    if maintainers:
        out += " ["
        out += " ".join(obfuscate_email(m.email) for m in p.maintainers)
        out += "]"
    print(out)


def process_one(p, repo, old, new, printer, fix=False, stabilizations=False,
                eclass_filter=None):
    try:
        impls = get_python_impls(p)
    except InvalidBashCodeError:
        sys.stderr.write(f"{ANSI.clear_line}{ANSI.red}Unable to process {p}{ANSI.reset}\n")
        return None

    if impls is None:
        # not a Python package
        return None

    if (eclass_filter is not None
            and not p.inherits.intersection(eclass_filter)):
        return

    if stabilizations and new in impls:
        # check if new is supported in stable
        # and if any stable version supported old
        has_in_stable = False
        has_any_stable = False

        for p in sorted(repo.filter(p.key), reverse=True):
            if get_package_class(p) == PackageClass.stable:
                impls = (get_python_impls(p) or ())
                if new in impls:
                    has_in_stable = True
                    break
                elif old in impls:
                    has_any_stable = True

        if has_any_stable:
            if not has_in_stable:
                printer(p)
                return True
    elif not stabilizations and old in impls:
        if new not in impls:
            printer(p)

            if fix:
                upd_list = []

                for p in sorted(repo.filter(p.key), reverse=True):
                    upd_list.append(p.path)

                    # update all non-keyworded (possibly live) ebuilds
                    # and the newest keyworded ebuild, then stop
                    if get_package_class(p) != PackageClass.non_keyworded:
                        break

                for path in upd_list:
                    try:
                        with EbuildMangler(path) as em:
                            em.add(new.r1_name)
                    except Exception as e:
                        sys.stderr.write("%s%s%s\n"
                                         % (ANSI.brown, str(e), ANSI.reset))

            return True
    return False


usedep_re = re.compile(r"^(?P<pkg>[^\[]*)(?:\[(?P<flags>[^:]*)\])?(?:::.*)?$")


def process_dep(repo, dep, func, package_cache):
    if not isinstance(dep, PMAtom):
        for d in dep:
            process_dep(repo, d, func, package_cache)
    else:
        if dep.blocking:
            return

        # only packages with use-deps are interesting to us
        # (hack copied from gpy-depcheck)
        m = usedep_re.match(str(dep))
        flags = (m.group("flags") or "").split(",")
        for f in flags:
            if f.startswith("python_targets_"):
                # ok, dep's there
                break
        else:
            return

        pkg = repo.select(m.group("pkg"))
        assert pkg
        if pkg not in package_cache:
            package_cache.add(pkg)
            if func(pkg):
                process_pkg_deps(repo, pkg, func, package_cache)


def process_pkg_deps(repo, p, f, package_cache):
    dep_groups = (p.run_dependencies, p.build_dependencies,
                  p.post_dependencies)
    if hasattr(p, "cbuild_build_dependencies"):
        dep_groups += (p.cbuild_build_dependencies,)
    for dg in dep_groups:
        for dep in dg:
            process_dep(repo, dep, f, package_cache)


def process(repo, pkgs, old, new, printer, fix=False, stabilizations=False,
            deps=False, package_cache=None, eclass_filter=None):
    total_upd = 0
    total_pkg = 0

    sys.stderr.write("%s%sWaiting for PM to start iterating...%s\r"
                     % (ANSI.clear_line, ANSI.brown, ANSI.reset))

    for pg in group_packages(pkgs, key="slotted_atom"):
        sys.stderr.write("%s%s%-40s%s (%s%4d%s of %s%4d%s need checking)\r"
                         % (ANSI.clear_line, ANSI.green, pg[0].key, ANSI.reset,
                            ANSI.white, total_upd, ANSI.reset,
                            ANSI.white, total_pkg, ANSI.reset))

        p = pg[-1]
        r = process_one(p, repo, old, new,
                        printer=printer,
                        fix=fix,
                        stabilizations=stabilizations,
                        eclass_filter=eclass_filter)

        if r is None:
            continue
        total_pkg += 1
        if r:
            total_upd += 1
            if deps:
                process_pkg_deps(
                    repo, p, functools.partial(
                        process_one, repo=repo, old=old, new=new, fix=fix,
                        stabilizations=stabilizations, printer=printer),
                    package_cache)

    sys.stderr.write("%s%sDone.%s\n"
                     % (ANSI.clear_line, ANSI.white, ANSI.reset))


def main(prog_name, *argv):
    pm = get_package_manager()

    opt = argparse.ArgumentParser(prog=prog_name)
    me = opt.add_mutually_exclusive_group()
    me.add_argument("-f", "--fix", action="store_true",
                    help="Automatically add <new-impl> to PYTHON_COMPAT "
                         "in the newest testing (and live) ebuild")
    me.add_argument("-s", "--stabilizations", action="store_true",
                    help="Find stabilization candidates needed for "
                         "<new-impl> support")
    opt.add_argument("-d", "--depends", action="store_true",
                     help="Include the dependencies of specified packages")
    opt.add_argument("-e", "--eclass-filter",
                     help="Include only ebuild using specified eclass(es)")
    opt.add_argument("-m", "--maintainers", action="store_true",
                     help="Print maintainers of listed packages")
    opt.add_argument("-p", "--print-path", action="store_const",
                     dest="pkg_print",
                     const=lambda p: os.path.sep.join(p.path.split(
                         os.path.sep)[-3:]),
                     help="Print relative path to the ebuild")
    opt.add_argument("-r", "--repo",
                     help="Work on given repository (default: gentoo)")
    opt.add_argument("old", metavar="old-impl",
                     help="Old implementation")
    opt.add_argument("new", metavar="new-impl",
                     help="New implementation")
    opt.add_argument("package", nargs="*",
                     help="Packages to scan (whole repo if none provided)")
    opt.set_defaults(
       repo="gentoo",
       pkg_print=lambda p: p.slotted_atom)

    vals = opt.parse_args(list(argv))

    read_implementations(pm)

    old = get_impl_by_name(vals.old)
    new = get_impl_by_name(vals.new)

    eclass_filter = None
    if vals.eclass_filter:
        eclass_filter = vals.eclass_filter.split(",")

    if not vals.package:
        process(pm.repositories[vals.repo], pm.repositories[vals.repo],
                old, new, fix=vals.fix, stabilizations=vals.stabilizations,
                eclass_filter=eclass_filter,
                printer=lambda p: print_package(p,
                                                maintainers=vals.maintainers,
                                                pkg_print=vals.pkg_print))
    else:
        package_cache = set()
        for pkg in vals.package:
            process(pm.repositories[vals.repo],
                    pm.repositories[vals.repo].filter(pkg), old, new,
                    fix=vals.fix, stabilizations=vals.stabilizations,
                    package_cache=package_cache, deps=vals.depends,
                    eclass_filter=eclass_filter,
                    printer=lambda p: print_package(
                        p, maintainers=vals.maintainers,
                        pkg_print=vals.pkg_print))

    return 0


def entry_point():
    sys.exit(main(*sys.argv))


if __name__ == "__main__":
    sys.exit(main(*sys.argv))
