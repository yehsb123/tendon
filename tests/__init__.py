"""Not a package of tests — a package so `tests/cli_source.py` can be imported.

pytest collects test modules by path and does not need this. `tests.cli_source` does: it
holds the reader several structural tests share, and sharing it is the point. Each of them
used to name `src/tendon/cli/main.py`, and when that file was split into four every one
broke while none of the properties had changed.
"""
