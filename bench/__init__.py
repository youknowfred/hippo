"""Benchmark and measurement tools that run from a hippo repository checkout.

Not shipped with the plugin (the plugin is the ``plugin/`` directory only, and nothing under
``plugin/`` imports this package). Run a tool from the repository root with the engine on the
path, e.g. ``PYTHONPATH=plugin python -m bench.dream_eval --help``.
"""

import os

# HCR-7: a bench measures the models in its own process (load time, resident memory, the
# in-process arm every evidence file so far was read on). The engine's default on the high
# profile is the machine's embedding daemon; a bench that wants it says so itself.
os.environ.setdefault("HIPPO_EMBED_DAEMON", "0")
