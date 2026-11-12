"""Benchmark and measurement tools that run from a hippo repository checkout.

Not shipped with the plugin (the plugin is the ``plugin/`` directory only, and nothing under
``plugin/`` imports this package). Run a tool from the repository root with the engine on the
path, e.g. ``PYTHONPATH=plugin python -m bench.dream_eval --help``.
"""
