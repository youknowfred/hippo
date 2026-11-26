"""HCR-4: the high profile's Qwen3 reranker behind the one rerank seam.

Everything here runs on Linux CI with a deterministic fake in place of MLX. One test drives
the real backend and skips unless MLX is installed and the pinned weights are on disk.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from memory import build_index as B
from memory import recall_rank as RR
from memory import recall_view as V
from memory import recall_warm as W
from memory import recall_warm_high as H
from memory import rerank_mlx as R
from memory import setup_cli

_SMALL, _BIG = R.WARM_MODEL, R.EXPLICIT_MODEL


class _FakeQwenReranker:
    """The MLX reranker's stand-in: P(yes) rises with the doc's share of query words."""

    made: list = []

    def __init__(self, model, snap):
        self.model_id, self.snap = model, snap
        self.load_ms, self.resident_bytes = 5.0, 2**30
        self.calls: list = []
        _FakeQwenReranker.made.append(self)

    def score(self, query, docs):
        self.calls.append((query, list(docs)))
        q = set(B.tokenize(query))
        return [len(q & set(B.tokenize(d))) / (1 + len(q)) for d in docs]


class _FakeMiniLM:
    calls: list = []

    def rerank(self, query, documents, **kwargs):
        _FakeMiniLM.calls.append(list(documents))
        return [0.5] * len(documents)  # a tie: sorted() keeps the fused order


@pytest.fixture
def fake(monkeypatch):
    _FakeQwenReranker.made.clear()
    _FakeMiniLM.calls.clear()
    monkeypatch.setattr(R, "BACKEND", _FakeQwenReranker)
    monkeypatch.setattr(R, "unavailable_reason", lambda model: None)
    monkeypatch.setattr(B, "_get_cross_encoder", lambda allow_download: _FakeMiniLM())
    monkeypatch.delenv("HIPPO_RERANK", raising=False)
    monkeypatch.delenv("HIPPO_RERANK_TIMEOUT", raising=False)
    R._reset_for_tests()
    H._reset_for_tests()
    yield
    H._reset_for_tests()
    R._reset_for_tests()


def _hits():
    return [
        {"name": "a", "description": "alpha pager rota", "corpus": None, "score": 0.9, "rank": 1},
        {"name": "Deploys", "description": "rule section", "corpus": "rule", "score": 1.0, "rank": 2},
        {"name": "b", "description": "zebra canary deploy lane", "corpus": None, "score": 0.5, "rank": 3},
    ]


# --------------------------------------------------------------------------- #
# The seam
# --------------------------------------------------------------------------- #
def test_no_active_reranker_runs_the_minilm_exactly_as_before(fake):
    assert R.active_reranker() is None
    out = RR._cross_encoder_rerank("zebra canary deploy", _hits())
    assert [h["name"] for h in out] == ["a", "b", "Deploys"]  # MiniLM tie: fused order
    assert _FakeMiniLM.calls == [["alpha pager rota", "zebra canary deploy lane"]]
    assert _FakeQwenReranker.made == []


def test_an_active_reranker_scores_through_mlx_and_keeps_the_seam_rules(fake):
    with R.using_reranker(_BIG):
        out = RR._cross_encoder_rerank("zebra canary deploy", _hits())
    assert [h["name"] for h in out] == ["b", "a", "Deploys"]  # rule pointer stays at the tail
    assert [(h["score"], h["rank"]) for h in out] == [(0.5, 3), (0.9, 1), (1.0, 2)]  # COR-8
    (rr,) = _FakeQwenReranker.made
    assert rr.model_id == _BIG and rr.calls == [("zebra canary deploy",
                                                 ["alpha pager rota", "zebra canary deploy lane"])]
    assert _FakeMiniLM.calls == []


def test_the_warm_model_never_loads_on_a_call(fake):
    with R.using_reranker(_SMALL):
        out = RR._cross_encoder_rerank("zebra canary deploy", _hits())
    assert out == _hits() and _FakeQwenReranker.made == []  # not loaded: the fused order


@pytest.mark.parametrize("failure", ["raise", "wrong_count", "slow"])
def test_any_failure_keeps_the_fused_order(fake, monkeypatch, failure):
    class _Bad(_FakeQwenReranker):
        def score(self, query, docs):
            if failure == "raise":
                raise RuntimeError("metal device lost")
            if failure == "slow":
                time.sleep(0.5)
            return [1.0] if failure == "wrong_count" else [0.0, 1.0]

    monkeypatch.setattr(R, "BACKEND", _Bad)
    monkeypatch.setenv("HIPPO_RERANK_TIMEOUT", "0.05")
    with R.using_reranker(_BIG):
        assert RR._cross_encoder_rerank("q", _hits()) == _hits()


def test_a_call_that_finds_a_scoring_still_running_fails_fast(fake, monkeypatch):
    """An abandoned scoring keeps the one MLX thread busy; the next call must not queue
    behind it (and time out in turn) — it keeps the fused order at once."""
    import threading

    gate, entered = threading.Event(), threading.Event()

    class _Slow(_FakeQwenReranker):
        def score(self, query, docs):
            entered.set()
            gate.wait(5)
            return [0.0, 1.0]

    monkeypatch.setattr(R, "BACKEND", _Slow)
    monkeypatch.setenv("HIPPO_RERANK_TIMEOUT", "0.05")
    with R.using_reranker(_BIG):
        assert RR._cross_encoder_rerank("q", _hits()) == _hits()  # times out, keeps running
        assert entered.wait(2)
        t = time.monotonic()
        assert RR._cross_encoder_rerank("q", _hits()) == _hits()
        assert time.monotonic() - t < 0.04  # refused at once, not after its own bound
    gate.set()


def test_a_failed_load_is_remembered_and_the_explicit_surface_falls_back(fake, monkeypatch):
    monkeypatch.setattr(R, "unavailable_reason", _only_failures)
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")

    def boom(model, snap):
        raise RuntimeError("bad weights")

    monkeypatch.setattr(R, "BACKEND", boom)
    with R.using_reranker(_BIG):
        assert RR._cross_encoder_rerank("q", _hits()) == _hits()
    assert R.status(_BIG)["status"] == "failed"
    assert R.explicit_model() is None  # later calls: the MiniLM, not a retried broken load


def _only_failures(model):
    rec = R._STATUS.get(model, {})
    return rec.get("reason") if rec.get("status") == "failed" else None


def test_bounds(monkeypatch):
    monkeypatch.delenv("HIPPO_RERANK_TIMEOUT", raising=False)
    assert R.bound_secs(_SMALL, 5.0) == R.WARM_TIMEOUT_SECS
    assert R.bound_secs(_BIG, 5.0) == R.EXPLICIT_TIMEOUT_SECS
    monkeypatch.setenv("HIPPO_RERANK_TIMEOUT", "2")
    assert R.bound_secs(_SMALL, 2.0) == 2.0


# --------------------------------------------------------------------------- #
# Where it is on
# --------------------------------------------------------------------------- #
def test_rerank_is_off_unless_asked_for_even_inside_the_warm_reranker(fake, monkeypatch):
    """HCR-4 shipped the warm 0.6B ON by default; HCR-6's evidence turned that off."""
    assert RR._rerank_enabled() is False
    for model in (_SMALL, _BIG):
        with R.using_reranker(model):
            assert RR._rerank_enabled() is False
    monkeypatch.setenv("HIPPO_RERANK", "1")
    with R.using_reranker(_SMALL):
        assert RR._rerank_enabled() is True
    monkeypatch.setenv("HIPPO_RERANK", "0")
    assert RR._rerank_enabled() is False


@pytest.mark.parametrize("profile", ["lean", "floor"])
def test_explicit_model_is_none_off_the_high_profile(fake, monkeypatch, profile):
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", profile)
    assert R.explicit_model() is None


def test_explicit_model_needs_a_servable_4b(fake, monkeypatch):
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    assert R.explicit_model() == _BIG
    monkeypatch.setattr(R, "unavailable_reason", lambda m: "weights not downloaded")
    assert R.explicit_model() is None


def test_unavailable_reasons_never_import_mlx(tmp_path, monkeypatch):
    from memory import embed_mlx as E

    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    monkeypatch.setattr(E, "apple_silicon", lambda: False)
    assert "Apple Silicon" in R.unavailable_reason(_SMALL)
    monkeypatch.setattr(E, "apple_silicon", lambda: True)
    assert "~0.6 GB" in R.unavailable_reason(_SMALL) and "--fetch" in R.unavailable_reason(_SMALL)
    assert "~4.2 GB" in R.unavailable_reason(_BIG)
    snap = R.snapshot_dir(_SMALL)
    os.makedirs(snap)
    for name in ("config.json", "tokenizer.json", "model.safetensors"):
        open(os.path.join(snap, name), "w").close()
    monkeypatch.setattr(E, "mlx_installed", lambda: False)
    assert "MLX is not installed" in R.unavailable_reason(_SMALL)
    monkeypatch.setattr(E, "mlx_installed", lambda: True)
    assert R.unavailable_reason(_SMALL) is None
    assert snap.startswith(os.path.join(str(tmp_path), "mlx", "models--mlx-community--"))


# --------------------------------------------------------------------------- #
# The explicit surface: the 4B
# --------------------------------------------------------------------------- #
def _corpus(tmp_path):
    md, idx = str(tmp_path / "memory"), str(tmp_path / ".memory-index")
    os.makedirs(md)
    for name, desc in {
        "zebra_pager_rota": "zebra service pager rota for the on-call week",
        "zebra_canary_deploy": "zebra service canary deploy lane, then the pager path",
    }.items():
        with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write(f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: project\n---\n{desc}\n')
    B.build_index(md, idx)
    return md, idx


def test_describe_on_high_reranks_with_the_4b(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    md, idx = _corpus(tmp_path)
    out = V.describe("zebra service canary deploy lane", memory_dir=md, index_dir=idx)
    (rr,) = _FakeQwenReranker.made
    assert rr.model_id == _BIG and len(rr.calls) == 1  # loaded on first use, one pass
    assert out.index("zebra_canary_deploy") < out.index("zebra_pager_rota")
    assert _FakeMiniLM.calls == []


def test_describe_off_high_is_the_minilm(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "lean")
    md, idx = _corpus(tmp_path)
    V.describe("zebra service canary deploy lane", memory_dir=md, index_dir=idx)
    assert _FakeQwenReranker.made == [] and len(_FakeMiniLM.calls) == 1


def test_the_plain_recall_call_never_reaches_a_qwen_reranker(fake, tmp_path, monkeypatch):
    """The spawned hook's recall (no context entered) on the high profile: unchanged."""
    from memory.recall import recall

    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    md, idx = _corpus(tmp_path)
    assert recall("zebra service canary deploy lane", memory_dir=md, index_dir=idx)
    assert _FakeQwenReranker.made == [] and _FakeMiniLM.calls == []


# --------------------------------------------------------------------------- #
# The warm server: the 0.6B, loaded in the background, on by default
# --------------------------------------------------------------------------- #
@pytest.fixture
def warm_project(tmp_path, monkeypatch):
    project = str(tmp_path / "project")
    md = os.path.join(project, ".claude", "memory")
    os.makedirs(md)
    for name, desc in {
        "zebra_pager_rota": "zebra service pager rota for the on-call week",
        "zebra_canary_deploy": "zebra service canary deploy lane, then the pager path",
    }.items():
        with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write(f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: project\n---\n{desc}\n')
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Memory Index\n\n## User\n")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", project)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("HIPPO_DISABLE_DENSE", "1")
    B.build_index(md, B.default_index_dir(md))
    W._SESSIONS.clear()
    W._CACHE._rows.clear()
    W._DEFERRED.clear()
    W._STATE.update(busy=False, last_sweep=time.time(), served=False)
    yield project
    W._SESSIONS.clear()
    W._CACHE._rows.clear()
    W._DEFERRED.clear()
    W._STATE.update(busy=False, last_sweep=0.0, served=False)


def _serve(sid, pid, prompt):
    d = W.warm_dir(create=True)
    assert W._create_claim(W.claim_path(d, sid, pid), "warm")
    out = W.serve({"session_id": sid, "prompt_id": pid, "prompt": prompt,
                   "cwd": os.environ["CLAUDE_PROJECT_DIR"]})
    W.drain()
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def _wait_loaded(model, timeout=5.0):
    end = time.time() + timeout
    while R.loaded(model) is None and time.time() < end:
        time.sleep(0.01)
    assert R.loaded(model) is not None


def test_warm_high_by_default_never_loads_or_scores_a_reranker(fake, warm_project, monkeypatch):
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    _serve("s1", "p1", "zebra service canary deploy lane")
    _serve("s2", "p2", "zebra service canary deploy lane")
    assert _FakeQwenReranker.made == [] and _FakeMiniLM.calls == []
    assert R.status(_SMALL) == {}
    hb = W.server_states()
    assert not [r for r in hb if isinstance(r.get("high"), dict) and r["high"].get("rerank")]


def test_warm_high_reranks_with_the_06b_once_asked_for_and_loaded(fake, warm_project, monkeypatch):
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    monkeypatch.setenv("HIPPO_RERANK", "1")
    prompt = "zebra service canary deploy lane"
    _serve("s1", "p1", prompt)  # kicks the background load
    _wait_loaded(_SMALL)
    (rr,) = _FakeQwenReranker.made
    before = len(rr.calls)
    out = _serve("s2", "p2", prompt)
    assert rr.model_id == _SMALL and len(rr.calls) == before + 1
    assert out.index("zebra_canary_deploy") < out.index("zebra_pager_rota")
    hb = W.server_states()
    rec = [r["high"]["rerank"] for r in hb if isinstance(r.get("high"), dict) and r["high"].get("rerank")]
    assert rec and rec[0]["model"] == _SMALL and rec[0]["status"] == "ready"
    assert _FakeMiniLM.calls == []


def test_warm_high_turned_off_after_loading_stops_scoring(fake, warm_project, monkeypatch):
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    monkeypatch.setenv("HIPPO_RERANK", "1")
    _serve("s1", "p1", "zebra service canary deploy lane")
    _wait_loaded(_SMALL)
    monkeypatch.setenv("HIPPO_RERANK", "0")
    before = len(_FakeQwenReranker.made[0].calls)
    _serve("s2", "p2", "zebra service canary deploy lane")
    assert len(_FakeQwenReranker.made[0].calls) == before


def test_warm_high_without_servable_weights_runs_as_before(fake, warm_project, monkeypatch):
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    monkeypatch.setenv("HIPPO_RERANK", "1")
    monkeypatch.setattr(R, "unavailable_reason", lambda m: "weights not downloaded")
    _serve("s1", "p1", "zebra service canary deploy lane")
    _serve("s2", "p2", "zebra service canary deploy lane")
    assert _FakeQwenReranker.made == []
    assert _FakeMiniLM.calls  # asked-for rerank with no Qwen weights is the MiniLM, as on lean
    assert R.status(_SMALL)["status"] == "unavailable"


def test_warm_lean_never_touches_a_qwen_reranker(fake, warm_project, monkeypatch):
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "lean")
    _serve("s1", "p1", "zebra service canary deploy lane")
    _serve("s2", "p2", "zebra service canary deploy lane")
    assert _FakeQwenReranker.made == [] and _FakeMiniLM.calls == [] and R._STATUS == {}
    assert H.status() == {}


# --------------------------------------------------------------------------- #
# The OpenBLAS pin and setup
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("profile,preset,pinned", [
    ("high", None, "1"), ("high", "8", "8"), ("lean", None, None), ("floor", None, None),
])
def test_the_warm_server_pins_one_openblas_thread_on_high(monkeypatch, profile, preset, pinned):
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", profile)
    env = {} if preset is None else {"OPENBLAS_NUM_THREADS": preset}
    H.pin_blas_threads(env)
    assert env.get("OPENBLAS_NUM_THREADS") == pinned


def test_fetch_previews_both_rerankers_and_downloads_them_with_yes(tmp_path, monkeypatch, capsys):
    from memory import embed_mlx as E

    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    calls = []
    monkeypatch.setattr(E, "fetch", lambda m: calls.append(m) or "/snap")
    monkeypatch.setattr(R, "fetch", lambda m: calls.append(m) or "/snap")
    assert setup_cli.main(["--profile", "--fetch"]) == 0
    out = capsys.readouterr().out
    assert calls == [] and "Nothing was downloaded" in out
    assert "mlx-community/Qwen3-Reranker-0.6B-mxfp8@a82f9f1c5dcb" in out and "(~0.6 GB)" in out
    assert "mlx-community/Qwen3-Reranker-4B-mxfp8@25f203a237b8" in out and "~9.0 GB in all" in out
    assert setup_cli.main(["--profile", "high", "--fetch", "--yes"]) == 0
    assert calls == ["Qwen/Qwen3-Embedding-4B", _SMALL, _BIG]


# --------------------------------------------------------------------------- #
# The real backend — only where MLX and the pinned weights already are
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("model", [_SMALL, _BIG])
def test_the_real_mlx_reranker_prefers_the_relevant_document(model):
    pytest.importorskip("mlx.core")
    pytest.importorskip("mlx_lm")
    from memory import embed_mlx as E

    root = os.environ.get("HIPPO_TEST_MLX_CACHE") or E.cache_dir()
    if not R.model_cached(model, root):
        pytest.skip(f"{model} MLX weights not downloaded")
    rr = R._MLXReranker(model, R.snapshot_dir(model, root))
    docs = ["The zebra service deploys through the canary lane first.",
            "Llama billing cutover freezes the ledger before invoice replay."]
    scores = rr.score("how do we deploy zebra", docs)
    assert scores[0] > 0.5 > scores[1]
    assert rr.score("how do we deploy zebra", docs[:1])[0] == pytest.approx(scores[0], abs=1e-3)
