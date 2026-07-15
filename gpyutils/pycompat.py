# gpyutils
# (c) 2013-2026 Michał Górny <mgorny@gentoo.org>
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import os
import os.path
import re
import shutil
import tempfile
from dataclasses import dataclass


class Whitespace(str):
    def __init__(self, s):
        self.removed = False
        str.__init__(self)

    def __repr__(self):
        return f"Whitespace({super().__repr__()})"


@dataclass
class Value:
    full_name: str
    local_name: str | None = None
    removed: bool = False

    def __post_init__(self) -> None:
        if self.local_name is None:
            self.local_name = self.full_name

    def __str__(self):
        assert not self.removed
        return self.local_name


def get_previous_val_index(values, v):
    """
    Return index of value in list values that lexically precedes v,
    or -1 if no value precedes it.

    >>> get_previous_val_index([Value('a'), Value('b')], Value('c'))
    1
    >>> get_previous_val_index([Value('a'), Value('c')], Value('b'))
    0
    >>> get_previous_val_index([Value('c'), Value('a')], Value('b'))
    1
    >>> get_previous_val_index([Value('c'), Value('b')], Value('a'))
    -1
    >>> get_previous_val_index([Value('b'), Whitespace(' '), Value('c')],
    ...                        Value('a'))
    -1
    """
    def sort_key(x):
        if isinstance(x, Whitespace):
            # sort whitespace out to the end
            return (1,)
        elif isinstance(x, Value):
            return (0, x.local_name)
        else:
            return (0, x.prefix)

    sorted_values = sorted(values + [v], key=sort_key)
    idx = sorted_values.index(v)
    if idx == 0:
        return -1
    else:
        return values.index(sorted_values[idx - 1])


@dataclass
class Group:
    prefix: str
    suffix: str
    values: list[Value]
    is_range: bool = False

    def add_sorted(self, v: str) -> bool:
        """Add value to the group and return True if it can be added"""
        # only numeric values can be added to a range
        if self.is_range and not v.local_name.isdigit():
            return False
        self.values.insert(get_previous_val_index(self.values, v) + 1, v)
        # try to opportunistically convert into a range
        # (str() will check if it's contiguous)
        if not self.is_range:
            self.is_range = all(x.local_name.isdigit() for x in self.values)
        return True

    @property
    def removed(self):
        return all(x.removed for x in self.values)

    def __iter__(self):
        for x in self.values:
            yield x

    def _local_str(self) -> str:
        vals = [x for x in self.values if not x.removed]

        assert len(vals) > 0
        if len(vals) == 1:
            return vals[0]

        if self.is_range:
            # try a contiguous range
            minrange = int(vals[0].local_name)
            maxrange = int(vals[-1].local_name)
            # lazy way of checking
            ovalues = [x.local_name for x in vals]
            rvalues = [str(x) for x in range(minrange, maxrange + 1)]
            if ovalues == rvalues:
                return f"{{{minrange}..{maxrange}}}"
        return f"{{{','.join(str(x) for x in vals)}}}"

    def __str__(self):
        return f"{self.prefix}{self._local_str()}{self.suffix}"


new_split_re = re.compile(r"(?P<mid> \d+) (?P<suffix> .*)", re.VERBOSE)


class PythonCompat:
    def __init__(self):
        self.nodes = []

    def append(self, n):
        self.nodes.append(n)

    def add(self, impl_name):
        if impl_name in self:
            return

        # first, try adding to an existing group
        # longer groups come first, so that should be good enough
        for g in self.groups:
            if impl_name.startswith(g.prefix) and impl_name.endswith(g.suffix):
                value = Value(
                    impl_name,
                    impl_name.removeprefix(g.prefix).removesuffix(g.suffix),
                )
                if g.add_sorted(value):
                    return

        # then, try splitting something else
        for v in sorted(self, key=lambda x: len(x.full_name), reverse=True):
            new_split = impl_name.split("_")
            old_split = v.full_name.split("_")
            cpfx = ""
            while new_split[0] == old_split[0]:
                cpfx += f"{new_split.pop(0)}_"
                del old_split[0]
            # only those with common prefix
            if not cpfx:
                continue
            # only split in global scope, don't nest
            if v not in self.nodes:
                continue
            # avoid 'empty' sections (e.g. pypy{,2_0})
            if not old_split or not new_split:
                continue

            suff1 = new_split_re.match(old_split[-1])
            suff2 = new_split_re.match(new_split[-1])

            # split only over digits
            if suff1 is None or suff2 is None:
                continue
            # don't split if we have different suffixes
            if suff1.group("suffix") != suff2.group("suffix"):
                continue

            suffix = suff1.group("suffix")
            # don't merge if maintainer didn't use any groups for matching
            # impls
            if len([
                x for x in self.nodes if isinstance(x, Value) and
                x.full_name.startswith(cpfx) and x.full_name.endswith(suffix)
            ]) > 1:
                continue

            v1 = Value(v.full_name, suff1.group("mid"))
            v2 = Value(impl_name, suff2.group("mid"))

            i = self.nodes.index(v)
            self.nodes[i] = Group(cpfx, suffix,
                                  sorted([v1, v2], key=lambda x: x.local_name))
            return

        # add it (sorted!)
        v = Value(impl_name, impl_name)
        i = get_previous_val_index(self.nodes, v)

        prepend_ws = (i != -1)
        if i == -1 and isinstance(self.nodes[0], Whitespace):
            i = 0

        self.nodes.insert(i + 1, v)
        self.nodes.insert(i + 1 if prepend_ws else i + 2, Whitespace(" "))

    def remove(self, impl_name):
        for i in self:
            if i.full_name == impl_name:
                i.removed = True

    @property
    def groups(self):
        return (node for node in self.nodes if isinstance(node, Group))

    def __iter__(self):
        for x in self.nodes:
            if isinstance(x, Group):
                for y in x:
                    if not y.removed:
                        yield y
            elif isinstance(x, Value):
                if not x.removed:
                    yield x

    def __contains__(self, full_name: str) -> bool:
        return any(x.full_name == full_name for x in self)

    def __repr__(self):
        return repr(self.nodes)

    def __str__(self):
        first = True
        for i, x in enumerate(self.nodes):
            if not isinstance(x, Whitespace):
                if x.removed:
                    if not first:
                        self.nodes[i - 1].removed = True
                    else:
                        self.nodes[i + 1].removed = True
                first = False

        return "".join([str(x) for x in self.nodes if not x.removed])


pycompat_re = re.compile(
    r"(?P<prefix> \w*)"
    r"(?:"
    r"  [{]"
    r"    (?:"
    r"      (?P<range_start> \d+) [.][.] (?P<range_end> \d+)"  # a..b
    r"    |"
    r"      (?P<groups> (?: \w*,)+ \w*)"  # a,b...
    r"    )"
    r"  [}]"
    r"  (?P<suffix> \w*)"
    r")?"
    ,
    re.VERBOSE,
)


def parse_item(s):
    match = pycompat_re.fullmatch(s)
    if match is None:
        raise ValueError(f"Invalid value in PYTHON_COMPAT: {s}")

    prefix = match.group("prefix")
    range_start = match.group("range_start")
    range_end = match.group("range_end")
    groups = match.group("groups")
    suffix = match.group("suffix")

    if range_start is not None:
        values = [
            Value(f"{prefix}{x}{suffix}", f"{x}")
            for x in range(int(range_start), int(range_end) + 1)
        ]
        return Group(prefix, suffix, values, is_range=True)
    elif match.group("groups") is not None:
        values = groups.split(",")
        return Group(prefix, suffix,
                     [Value(f"{prefix}{x}{suffix}", x) for x in values])

    assert suffix is None
    return Value(prefix)


ws_split_re = re.compile(r"(\s+)")


def parse(s):
    out = PythonCompat()

    data = ws_split_re.split(s)
    for i, w in enumerate(data):
        if not w:
            pass
        elif i % 2:
            # 1, 3, 5 are whitespace
            out.append(Whitespace(w))
        else:
            # 0, 2, 4 are the real deal
            out.append(parse_item(w))

    return out


def add_impl(s, new):
    """
    >>> add_impl('python3_13 python3_13t', 'python3_14t')
    'python3_13 python3_{13,14}t'
    >>> add_impl('pypy1_9', 'python3_3')
    'pypy1_9 python3_3'
    >>> add_impl('python2_7', 'pypy2_0')
    'pypy2_0 python2_7'
    >>> add_impl('python2_6 python2_7 python3_2 pypy1_9', 'python3_3')
    'python2_6 python2_7 python3_{2,3} pypy1_9'
    >>> add_impl('python2_6 python2_7 python3_4 pypy1_9', 'python3_3')
    'python2_6 python2_7 python3_{3,4} pypy1_9'
    >>> add_impl('python2_{6,7} python3_{1,2}', 'python3_3')
    'python2_{6,7} python3_{1..3}'
    >>> add_impl('python2_{6,7} python3_2', 'python3_3')
    'python2_{6,7} python3_{2,3}'
    >>> add_impl('python2_{6,7} python3_4', 'python3_3')
    'python2_{6,7} python3_{3,4}'
    >>> add_impl('python{2_6,2_7,3_2} pypy1_9', 'python3_3')
    'python{2_6,2_7,3_2,3_3} pypy1_9'
    >>> add_impl('python{2_6,2_7,3_4} pypy1_9', 'python3_3')
    'python{2_6,2_7,3_3,3_4} pypy1_9'
    >>> add_impl('python{2_6,2_7,3_2,3_3} pypy2_0', 'jython2_7')
    'jython2_7 python{2_6,2_7,3_2,3_3} pypy2_0'
    >>> add_impl('python{2_6,2_7,3_2,3_3} pypy2_0', 'pypy')
    'pypy python{2_6,2_7,3_2,3_3} pypy2_0'
    >>> add_impl(' pypy ', 'python2_6')
    ' pypy python2_6 '
    >>> add_impl(' python{2_6,2_7} ', 'pypy')
    ' pypy python{2_6,2_7} '
    >>> add_impl('python2_7 python3_{3..4}', 'python3_5')
    'python2_7 python3_{3..5}'
    >>> add_impl('python2_7 python3_{3..4}', 'python3_2')
    'python2_7 python3_{2..4}'
    >>> add_impl('python2_7 python3_{4..5}', 'python3_2')
    'python2_7 python3_{2,4,5}'
    >>> add_impl('pypy{,3}', 'python2_7')
    'pypy{,3} python2_7'
    >>> add_impl('pypy{,3}', 'pypy4')
    'pypy{,3,4}'
    >>> add_impl('pypy{3,4}', 'pypy')
    'pypy{,3,4}'
    >>> add_impl('python3_{12,13}', 'python3_13t')
    'python3_{12,13,13t}'
    >>> add_impl('python3_{12..13}', 'python3_13t')
    'python3_{12..13} python3_13t'
    >>> add_impl('python3_13', 'python3_13t')
    'python3_13 python3_13t'
    >>> add_impl('python3_{10..13} python3_13t', 'python3_14t')
    'python3_{10..13} python3_{13,14}t'
    >>> add_impl('python3_{10..13} python3_{13,14}t', 'python3_15t')
    'python3_{10..13} python3_{13..15}t'
    >>> add_impl('python3_{10..13} python3_{13..14}t', 'python3_15t')
    'python3_{10..13} python3_{13..15}t'
    >>> add_impl('python3_10', 'python3_11')
    'python3_{10,11}'
    """
    pc = parse(s)
    pc.add(new)
    return str(pc)


def del_impl(s, old):
    """
    >>> del_impl('python2_6 python2_7 python3_2 pypy1_9', 'python2_6')
    'python2_7 python3_2 pypy1_9'
    >>> del_impl('python2_{6,7} python3_{1,2}', 'python2_6')
    'python2_7 python3_{1,2}'
    >>> del_impl('python2_{6,7} python3_{1,2,3}', 'python3_1')
    'python2_{6,7} python3_{2,3}'
    >>> del_impl('python{2_6,2_7,3_2} pypy1_9', 'python2_6')
    'python{2_7,3_2} pypy1_9'
    >>> del_impl('python{2_6,2_7} pypy1_9', 'python2_6')
    'python2_7 pypy1_9'
    >>> del_impl(' python2_6 python2_7 ', 'python2_6')
    ' python2_7 '
    >>> del_impl(' python2_6 python2_7 ', 'python2_7')
    ' python2_6 '
    >>> del_impl(' python2_6 python2_7 python3_2 ', 'python2_7')
    ' python2_6 python3_2 '
    >>> del_impl('python2_{6..7}', 'python2_6')
    'python2_7'
    >>> del_impl('python3_{1..5}', 'python3_1')
    'python3_{2..5}'
    >>> del_impl('python3_{1..5}', 'python3_5')
    'python3_{1..4}'
    >>> del_impl('python3_{1..5}', 'python3_3')
    'python3_{1,2,4,5}'
    >>> del_impl('pypy{,3} python2_7', 'python2_7')
    'pypy{,3}'
    >>> del_impl('pypy{,3}', 'pypy3')
    'pypy'
    >>> del_impl('pypy{,3}', 'pypy')
    'pypy3'
    >>> del_impl('pypy{3,} python2_7', 'python2_7')
    'pypy{3,}'
    >>> del_impl('pypy{3,}', 'pypy3')
    'pypy'
    >>> del_impl('pypy{3,}', 'pypy')
    'pypy3'
    >>> del_impl('python3_{10..13} python3_13t', 'python3_13t')
    'python3_{10..13}'
    >>> del_impl('python3_{10..13} python3_13t', 'python3_10')
    'python3_{11..13} python3_13t'
    >>> del_impl('python3_{10..13} python3_13t', 'python3_13')
    'python3_{10..12} python3_13t'
    >>> del_impl('python3_{10..13} python3_13t', 'python3_12')
    'python3_{10,11,13} python3_13t'
    >>> del_impl('python3_{13,13t}', 'python3_13t')
    'python3_13'
    >>> del_impl('python3_{13,13t}', 'python3_13')
    'python3_13t'
    >>> del_impl('python3_{10..14} python3_{13,14}t', 'python3_10')
    'python3_{11..14} python3_{13,14}t'
    >>> del_impl('python3_{10..14} python3_{13..14}t', 'python3_10')
    'python3_{11..14} python3_{13..14}t'
    >>> del_impl('python3_{10..14} python3_{13,14}t', 'python3_13t')
    'python3_{10..14} python3_14t'
    >>> del_impl('python3_{10..14} python3_{13..14}t', 'python3_13t')
    'python3_{10..14} python3_14t'
    >>> del_impl('python3_{10..14} python3_{13,14,15}t', 'python3_13t')
    'python3_{10..14} python3_{14,15}t'
    >>> del_impl('python3_{10..14} python3_{13..15}t', 'python3_13t')
    'python3_{10..14} python3_{14..15}t'
    >>> del_impl('python3_{10..14} python3_{13,14,15}t', 'python3_14t')
    'python3_{10..14} python3_{13,15}t'
    >>> del_impl('python3_{10..14} python3_{13..15}t', 'python3_14t')
    'python3_{10..14} python3_{13,15}t'
    """
    pc = parse(s)
    pc.remove(old)
    return str(pc)


python_compat_re = re.compile(r"(?<![^\n])PYTHON_COMPAT=\((?P<value>.*)\)")


class EbuildMangler:
    def __init__(self, path):
        with open(path, "rb") as f:
            data = f.read().decode("utf8")

        m = python_compat_re.search(data)
        if m:
            self._path = path
            self._data = data
            self._value = parse(m.group("value"))
            self._start = m.start()
            self._end = m.end()
        else:
            raise KeyError("Unable to find PYTHON_COMPAT in %s" % path)

    def write(self):
        data = "".join((self._data[:self._start],
                        "PYTHON_COMPAT=(", str(self._value), ")",
                        self._data[self._end:]))

        with tempfile.NamedTemporaryFile(
                "wb", dir=os.path.dirname(self._path), delete=False) as f:
            tmp_path = f.name
            f.write(data.encode("utf8"))

        shutil.copymode(self._path, tmp_path)
        os.rename(tmp_path, self._path)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.write()

    def add(self, impl):
        self._value.add(impl)

    def remove(self, impl):
        self._value.remove(impl)

    def __contains__(self, full_name: str) -> bool:
        return full_name in self._value

    @property
    def value(self):
        return str(self._value)
