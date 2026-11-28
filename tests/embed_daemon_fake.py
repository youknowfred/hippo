"""HCR-7: the embedding daemon with MLX faked out — a launcher and the fakes it serves.

``python tests/embed_daemon_fake.py --space <id>`` is ``python -m memory.embed_daemon`` with a
deterministic embedder and reranker in place of the MLX ones, so the tests can start, race
and kill real daemon processes on Linux CI. ``tests/test_embed_daemon.py`` points
``embed_remote.SPAWN`` at it, and uses the same fakes for daemons it runs on a thread.

A running fake daemon takes orders from ``$CLAUDE_PLUGIN_DATA/fake-daemon-mode``, read on
every query: ``slow <seconds>`` sleeps before it embeds, ``die`` exits the process without
an answer (a daemon killed mid-call).
"""

from __future__ import annotations

import os
import sys
import threading
import time

import numpy as np

from memory import build_index as B
from memory import embed_mlx as E


def bag(text: str, dim: int, salt: str = "qwen") -> np.ndarray:
    """``tests/test_embed_mlx._bag``: a hashed bag of words, the same in every process."""
    v = np.zeros(dim, dtype="float32")
    for tok in B.tokenize(text):
        v[int.from_bytes(f"{salt}{tok}".encode()[-8:], "little") % dim] += 1.0
    return v


def _order() -> str:
    try:
        with open(os.path.join(os.environ["CLAUDE_PLUGIN_DATA"], "fake-daemon-mode"), encoding="utf-8") as fh:
            return fh.read().strip()
    except (KeyError, OSError):
        return ""


class FakeEmbedder:
    """The MLX embedder's shape. Records what it embedded and how many texts each query pass
    carried; ``gate``, when set by a test, holds a query pass until the test releases it."""

    made: list = []

    def __init__(self, model, dim, snap=""):
        self.model_id, self.dim = model, dim
        self.load_ms, self.resident_bytes = 7.0, 4 * 2**30
        self.docs: list = []
        self.queries: list = []
        self.passes: list = []
        self.gate = None  # a threading.Event the next query pass waits on
        self.entered = threading.Event()
        FakeEmbedder.made.append(self)

    def _vec(self, text):
        return bag(text, E.HIGH_MODELS[self.model_id]["native_dim"])[: self.dim]

    def embed(self, texts):
        self.docs.extend(texts)
        return iter([self._vec(t) for t in texts])

    def query_embed(self, texts):
        order = _order()
        if order == "die":
            os._exit(9)
        if order.startswith("slow"):
            time.sleep(float(order.split()[1]))
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(120)
        self.queries.extend(texts)
        self.passes.append(len(texts))
        return iter([self._vec(t) for t in texts])


class FakeReranker:
    """P(yes) rises with the document's share of the query's words."""

    made: list = []

    def __init__(self, model, snap=""):
        self.model_id = model
        self.load_ms, self.resident_bytes = 5.0, 2**30
        self.calls: list = []
        FakeReranker.made.append(self)

    def score(self, query, docs):
        self.calls.append((query, list(docs)))
        q = set(B.tokenize(query))
        return [len(q & set(B.tokenize(d))) / (1 + len(q)) for d in docs]


if __name__ == "__main__":
    from memory import embed_daemon
    from memory import rerank_mlx as R

    E.BACKEND, E.unavailable_reason = FakeEmbedder, lambda model: None
    R.BACKEND, R.unavailable_reason = FakeReranker, lambda model: None
    raise SystemExit(embed_daemon.main(sys.argv[1:]))
