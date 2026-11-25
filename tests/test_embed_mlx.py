"""HCR-2: the high profile's MLX embedder (Qwen3-Embedding) behind the dense seam.

Everything here runs on Linux CI with a deterministic fake backend in place of MLX (the
way test_build_index fakes fastembed). One test drives the real backend, and it skips
unless MLX is installed and the pinned weights are already on disk.
"""

from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pytest

from memory import build_index as B
from memory import embed_mlx as E
from memory import recall_rank as RR
from memory import recall_warm as W
from memory import recall_warm_high as H
from memory import setup_cli

_MEMS = {
    "zebra_deploy_runbook": "How the zebra service is deployed — canary lane first, then the pager path.",
    "llama_billing_cutover": "Llama billing cutover checklist — ledger freeze, invoice replay, rollback.",
}
_PROMPT = "how do we deploy the zebra service canary lane"
_SPACE = "Qwen/Qwen3-Embedding-4B"


def _bag(text: str, dim: int, salt: str) -> np.ndarray:
    v = np.zeros(dim, dtype="float32")
    for tok in B.tokenize(text):
        v[int.from_bytes(f"{salt}{tok}".encode()[-8:], "little") % dim] += 1.0
    return v


class _FakeLean:
    """bge-small's stand-in: 16 dims."""

    def embed(self, texts):
        return iter([_bag(t, 16, "lean") for t in texts])

    query_embed = embed


class _FakeQwen:
    """The MLX embedder's stand-in: the real class's shape, native width, no MLX."""

    made: list = []

    def __init__(self, model, dim, snap):
        self.model_id, self.dim, self.snap = model, dim, snap
        self.load_ms, self.resident_bytes = 7.0, 4 * 2**30
        self.docs: list = []
        self.queries: list = []
        _FakeQwen.made.append(self)

    def _vec(self, text):
        full = _bag(text, E.HIGH_MODELS[self.model_id]["native_dim"], "qwen")
        return full[: self.dim]

    def embed(self, texts):
        self.docs.extend(texts)
        return iter([self._vec(t) for t in texts])

    def query_embed(self, texts):
        self.queries.extend(texts)
        return iter([self._vec(t) for t in texts])


def _write_mem(md, name, desc):
    with open(os.path.join(md, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(f'---\nname: {name}\ndescription: "{desc}"\nmetadata:\n  type: project\n---\n{desc}\n')


@pytest.fixture
def fakes(monkeypatch):
    """Lean's fastembed and high's MLX both faked; the high model reads as servable."""
    _FakeQwen.made.clear()
    for allow in (True, False):
        B._MODEL_CACHE[(B.DEFAULT_MODEL, allow)] = _FakeLean()
    monkeypatch.setattr(E, "BACKEND", _FakeQwen)
    monkeypatch.setattr(E, "unavailable_reason", lambda model: None)
    E._LOADED.clear()
    H._reset_for_tests()
    yield
    H._reset_for_tests()
    E._LOADED.clear()


@pytest.fixture
def project(tmp_path, monkeypatch):
    project = str(tmp_path / "project")
    md = os.path.join(project, ".claude", "memory")
    os.makedirs(md)
    for name, desc in _MEMS.items():
        _write_mem(md, name, desc)
    with open(os.path.join(md, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# Memory Index\n\n## User\n")
    data = str(tmp_path / "data")
    os.makedirs(data)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", data)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", project)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
    # Ranking knobs a developer's shell may carry (a project's settings export them into
    # its sessions): these tests assert what the shipped floor and seed count serve.
    for knob in ("HIPPO_DENSE_FLOOR", "HIPPO_GRAPH_SEEDS"):
        monkeypatch.delenv(knob, raising=False)
    idx = B.default_index_dir(md)
    return {"project": project, "md": md, "idx": idx, "data": data, "high": H.high_index_dir(idx)}


@pytest.fixture(autouse=True)
def _fresh_warm_state():
    W._SESSIONS.clear()
    W._CACHE._rows.clear()
    W._DEFERRED.clear()
    W._STATE.update(busy=False, last_sweep=time.time(), served=False)
    yield
    W._SESSIONS.clear()
    W._CACHE._rows.clear()
    W._DEFERRED.clear()
    W._STATE.update(busy=False, last_sweep=0.0, served=False)


def _serve_warm(sid, pid, prompt=_PROMPT):
    """The hook already claimed this prompt ``warm``; the server reads it and serves."""
    d = W.warm_dir(create=True)
    assert W._create_claim(W.claim_path(d, sid, pid), "warm")
    out = W.serve({"session_id": sid, "prompt_id": pid, "prompt": prompt,
                   "cwd": os.environ["CLAUDE_PROJECT_DIR"]})
    W.drain()
    return out


def _heartbeat(sid):
    with open(W.heartbeat_path(W.warm_dir(), sid), encoding="utf-8") as fh:
        return json.load(fh)


def _wait_fresh(p, space=_SPACE, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if H.fresh(B.load_index(p["idx"]), B.load_index(p["high"]), space):
            return
        time.sleep(0.02)
    raise AssertionError("the high index never caught up with the lean one")


# --------------------------------------------------------------------------- #
# Which space
# --------------------------------------------------------------------------- #
def test_space_ids_round_trip_and_reject_junk():
    assert E.space_id(_SPACE) == _SPACE
    assert E.space_id(_SPACE, 2560) == _SPACE
    assert E.space_id(_SPACE, 1024) == f"{_SPACE}@1024"
    assert E.parse_space(f"{_SPACE}@1024") == (_SPACE, 1024)
    assert E.parse_space(_SPACE) == (_SPACE, 2560)
    assert E.parse_space("Qwen/Qwen3-Embedding-8B") == ("Qwen/Qwen3-Embedding-8B", 4096)
    for junk in (None, B.ENGLISH_DEFAULT_MODEL, f"{_SPACE}@2560", f"{_SPACE}@8", f"{_SPACE}@x", "Qwen/Other"):
        assert E.parse_space(junk) is None


def test_model_and_dim_resolve_env_then_preset_then_default(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    assert E.resolve_high_space() == _SPACE  # the 4B, full width
    (tmp_path / "model.json").write_text(json.dumps(
        {"high_embed_model": "Qwen/Qwen3-Embedding-8B", "high_embed_dim": 2048}))
    assert E.resolve_high_space() == "Qwen/Qwen3-Embedding-8B@2048"
    monkeypatch.setenv(E.MODEL_ENV, _SPACE)
    monkeypatch.setenv(E.DIM_ENV, "1024")
    assert E.resolve_high_space() == f"{_SPACE}@1024"
    monkeypatch.setenv(E.MODEL_ENV, "Qwen/Not-A-Model")  # ignored: falls to the preset
    monkeypatch.setenv(E.DIM_ENV, "99999")  # out of range: falls to the preset
    assert E.resolve_high_space() == "Qwen/Qwen3-Embedding-8B@2048"


def test_no_space_is_active_outside_using_space():
    assert E.active_space() is None
    with E.using_space(_SPACE):
        assert E.active_space() == _SPACE
    assert E.active_space() is None
    with pytest.raises(ValueError):
        with E.using_space(B.ENGLISH_DEFAULT_MODEL):
            pass


def test_run_bounded_carries_the_active_space_into_its_worker():
    with E.using_space(_SPACE):
        assert B.run_bounded(E.active_space, 5.0) == _SPACE
    assert B.run_bounded(E.active_space, 5.0) is None


def _bare_embedder(ran):
    import concurrent.futures

    emb = object.__new__(E._MLXEmbedder)
    emb.dim = 4
    emb._pool = concurrent.futures.ThreadPoolExecutor(1)
    emb._ids = lambda texts: [[1, 2, 3] for _ in texts]
    emb._forward = lambda batch: (ran.append(len(batch)), np.zeros((len(batch), 4), dtype="float32"))[1]
    return emb


def test_a_query_embed_abandoned_while_queued_never_reaches_the_model():
    import threading

    ran: list = []
    emb = _bare_embedder(ran)
    gate = threading.Event()
    emb._pool.submit(gate.wait)  # the one MLX thread is busy (a build slice, a model load)
    with pytest.raises(B.DenseTimeout):
        with E.abandon_on_timeout():
            B.run_bounded(lambda: list(emb.query_embed(["where is the zebra runbook"])), 0.05)
    gate.set()
    emb._pool.submit(lambda: None).result(timeout=5)  # the queue has drained
    assert ran == []
    list(emb.query_embed(["the next prompt"]))  # the next caller is served as ever
    assert ran == [1]
    emb._pool.shutdown()


def test_an_embed_inside_its_bound_and_a_document_build_are_untouched():
    ran: list = []
    emb = _bare_embedder(ran)
    with E.abandon_on_timeout():
        assert len(B.run_bounded(lambda: list(emb.query_embed(["q"])), 5.0)) == 1
    assert len(list(emb.embed(["doc one", "doc two"]))) == 2
    assert sum(ran) == 3 and E._GIVEN_UP.get() is None
    emb._pool.shutdown()


def test_the_query_path_marks_a_timed_out_embed_abandoned(monkeypatch):
    marks: list = []

    def timed_out(fn, seconds):
        marks.append(E._GIVEN_UP.get())
        raise B.DenseTimeout()

    monkeypatch.setattr(RR, "run_bounded", timed_out)
    RR._QVEC.last = None
    with pytest.raises(B.DenseTimeout):
        RR._query_vector("where is the zebra runbook")
    assert marks[0] is not None and marks[0].is_set() and E._GIVEN_UP.get() is None


def test_queries_carry_the_one_instruction_and_documents_do_not():
    emb = object.__new__(E._MLXEmbedder)
    seen = []
    emb._encode = lambda texts: (seen.append(list(texts)), np.zeros((len(texts), 4)))[1]
    list(emb.query_embed(["where is the zebra runbook"]))
    list(emb.embed(["zebra runbook body"]))
    assert seen[0] == [f"Instruct: {E.QUERY_INSTRUCTION}\nQuery:where is the zebra runbook"]
    assert seen[1] == ["zebra runbook body"]


# --------------------------------------------------------------------------- #
# Offline posture: stat before import, never a download
# --------------------------------------------------------------------------- #
def _fake_snapshot(root, model=_SPACE, files=("model.safetensors",)):
    snap = E.snapshot_dir(model, root)
    os.makedirs(snap, exist_ok=True)
    for name in ("config.json", "tokenizer.json") + tuple(files):
        open(os.path.join(snap, name), "w").close()
    return snap


def test_model_cached_needs_a_complete_set_of_weight_files(tmp_path):
    root = str(tmp_path / "mlx")
    assert not E.model_cached(_SPACE, root)
    _fake_snapshot(root, files=())
    assert not E.model_cached(_SPACE, root)  # config and tokenizer alone are not a model
    snap = _fake_snapshot(root, files=("model-00001-of-00002.safetensors",))
    assert not E.model_cached(_SPACE, root)  # a partial download is not a cache
    open(os.path.join(snap, "model-00002-of-00002.safetensors"), "w").close()
    assert E.model_cached(_SPACE, root)


def test_a_stale_shard_index_does_not_hide_a_complete_unsharded_model(tmp_path):
    """The real 4B mxfp8 snapshot: one model.safetensors, an index naming two shards."""
    root = str(tmp_path / "mlx")
    snap = _fake_snapshot(root)
    with open(os.path.join(snap, "model.safetensors.index.json"), "w") as fh:
        json.dump({"weight_map": {"a": "model-00001-of-00002.safetensors"}}, fh)
    assert E.model_cached(_SPACE, root)


def test_unavailable_reasons_in_order_without_importing_mlx(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    for mod in [m for m in sys.modules if m == "mlx" or m.startswith(("mlx.", "mlx_lm"))]:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setattr(E, "apple_silicon", lambda: False)
    assert "Apple Silicon" in E.unavailable_reason(_SPACE)
    monkeypatch.setattr(E, "apple_silicon", lambda: True)
    why = E.unavailable_reason(_SPACE)
    assert "not downloaded" in why and "--fetch" in why and "4.2 GB" in why
    _fake_snapshot(E.cache_dir())
    monkeypatch.setattr(E, "mlx_installed", lambda: False)
    assert "MLX is not installed" in E.unavailable_reason(_SPACE)
    monkeypatch.setattr(E, "mlx_installed", lambda: True)
    assert E.unavailable_reason(_SPACE) is None
    assert "mlx" not in sys.modules and "mlx_lm" not in sys.modules


def test_an_unservable_space_raises_dense_unavailable_never_loads(monkeypatch):
    monkeypatch.setattr(E, "unavailable_reason", lambda model: "MLX is not installed (x)")
    monkeypatch.setattr(E, "BACKEND", lambda *a: pytest.fail("must not load"))
    E._LOADED.clear()
    with pytest.raises(B.DenseUnavailable) as exc:
        E.high_embedder(_SPACE)
    assert exc.value.reason == "model_not_warmed"


# --------------------------------------------------------------------------- #
# Lean and floor: byte-identical, MLX never imported
# --------------------------------------------------------------------------- #
_HIGH_KEYS = {"embed_backend", "embed_weights", "native_dim", "mrl_dim"}


@pytest.mark.parametrize("profile", ["lean", "high"])
def test_the_spawned_path_stays_lean_on_every_profile(fakes, project, monkeypatch, profile):
    from memory import recall as R

    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", profile)
    B.build_index(project["md"], project["idx"])
    manifest = B._read_manifest_json(project["idx"])
    assert manifest["model"] == B.DEFAULT_MODEL and manifest["dense_ready"]
    assert not _HIGH_KEYS & set(manifest)  # no new keys: lean's manifest shape is unchanged
    hits = R.recall(_PROMPT, memory_dir=project["md"])
    assert hits and hits[0]["name"] == "zebra_deploy_runbook"
    assert not os.path.exists(project["high"])
    assert _FakeQwen.made == []
    assert "mlx" not in sys.modules and "mlx_lm" not in sys.modules


def test_floor_never_reaches_any_embedder(fakes, project, monkeypatch):
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "floor")
    manifest = B.build_index(project["md"], project["idx"])
    assert manifest["model"] is None and not manifest["dense_ready"]
    H.kick(project["md"])
    assert H.status() == {} and _FakeQwen.made == []


# --------------------------------------------------------------------------- #
# The per-profile index: lean and high coexist, neither tears the other
# --------------------------------------------------------------------------- #
def test_a_high_index_is_a_bound_sibling_the_lean_build_never_touches(fakes, project):
    B.build_index(project["md"], project["idx"])
    with E.using_space(_SPACE):
        high = B.build_index(project["md"], project["high"], allow_download=False)
    assert high["model"] == _SPACE and high["dense_ready"] and high["dim"] == 2560
    assert high["mrl_dim"] == 2560 and high["native_dim"] == 2560 and high["embed_backend"] == "mlx"
    assert high["embed_weights"].startswith("mlx-community/Qwen3-Embedding-4B-mxfp8@")
    high_files = set(os.listdir(project["high"]))
    lean_before = open(os.path.join(project["idx"], "manifest.json"), "rb").read()
    B.build_index(project["md"], project["idx"], force=True)  # a lean rebuild + its retire pass
    assert set(os.listdir(project["high"])) == high_files
    assert B._read_manifest_json(project["idx"])["model"] == B.DEFAULT_MODEL
    assert json.loads(lean_before)["model"] == B.DEFAULT_MODEL
    assert H.fresh(B.load_index(project["idx"]), B.load_index(project["high"]), _SPACE)


def test_an_mrl_width_is_its_own_space(fakes, project, monkeypatch):
    monkeypatch.setenv(E.DIM_ENV, "64")
    space = E.resolve_high_space()
    assert space == f"{_SPACE}@64"
    with E.using_space(_SPACE):
        B.build_index(project["md"], project["high"], allow_download=False)
    with E.using_space(space):
        m = B.build_index(project["md"], project["high"], allow_download=False)
    assert m["model"] == space and m["dim"] == 64 and m["mrl_dim"] == 64
    assert _FakeQwen.made[-1].dim == 64  # re-embedded at the new width, no 2560-row reuse


def test_recall_skips_dense_on_an_index_from_another_space(fakes, project):
    with E.using_space(_SPACE):
        B.build_index(project["md"], project["high"], allow_download=False)
    idx = B.load_index(project["high"])
    why: list = []
    assert RR._dense_rank_rows(_PROMPT, idx, why_out=why) == [] and why == ["model_mismatch"]
    with E.using_space(_SPACE):
        assert RR._dense_rank_rows(_PROMPT, idx) != []


def test_a_query_vector_is_never_reused_across_spaces(fakes, project):
    with RR.reuse_query_vector(_PROMPT):
        lean_vec = RR._query_vector(_PROMPT)
        with E.using_space(_SPACE):
            high_vec = RR._query_vector(_PROMPT)
    assert lean_vec.shape == (16,) and high_vec.shape == (2560,)


# --------------------------------------------------------------------------- #
# Warm-first: only the warm server loads it; lean until the high twin catches up
# --------------------------------------------------------------------------- #
def test_warm_serves_lean_until_the_high_index_catches_up_then_serves_high(fakes, project, monkeypatch):
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    B.build_index(project["md"], project["idx"])
    out1 = _serve_warm("s1", "p1")
    assert "zebra_deploy_runbook" in out1  # served lean while the model loads
    _wait_fresh(project)
    qwen = _FakeQwen.made[0]
    assert qwen.queries == []  # nothing has queried the big model yet
    out2 = _serve_warm("s2", "p2", "zebra canary lane deploy steps")  # s2: no cooldown
    assert "zebra_deploy_runbook" in out2
    assert qwen.queries == ["zebra canary lane deploy steps"]
    hb = _heartbeat("s2")["high"]
    assert hb["status"] == "ready" and hb["engaged"] is True and hb["space"] == _SPACE
    assert hb["load_ms"] == 7.0 and hb["resident_bytes"] == 4 * 2**30
    assert len(_FakeQwen.made) == 1  # one load per process


def test_a_new_memory_serves_lean_until_its_high_row_exists(fakes, project, monkeypatch):
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    B.build_index(project["md"], project["idx"])
    _serve_warm("s1", "p1")
    _wait_fresh(project)
    _write_mem(project["md"], "quokka_pager_rota", "Quokka pager rota — who holds the quokka pager each week.")
    time.sleep(0.01)
    B.build_index(project["md"], project["idx"])  # SessionStart's lean refresh, say
    assert "quokka_pager_rota" in _serve_warm("s1", "p2", "who holds the quokka pager rota")
    assert _heartbeat("s1")["high"]["engaged"] is False  # lean served it: the twin was behind
    _wait_fresh(project)
    assert "quokka_pager_rota" in _serve_warm("s2", "p3", "who holds the quokka pager rota")
    assert _heartbeat("s2")["high"]["engaged"] is True


def test_every_tier_must_have_a_fresh_twin_before_high_serves(fakes, project, monkeypatch, tmp_path):
    tier = str(tmp_path / "user-tier")
    os.makedirs(tier)
    _write_mem(tier, "zebra_personal_note", "My own zebra deploy canary habit.")
    monkeypatch.setenv("HIPPO_USER_MEMORY_DIR", tier)
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    B.build_index(project["md"], project["idx"])
    tier_idx = B.default_index_dir(tier)
    B.build_index(tier, tier_idx)
    _serve_warm("s1", "p1")
    _wait_fresh(project)
    _wait_fresh({"idx": tier_idx, "high": H.high_index_dir(tier_idx)})
    out = _serve_warm("s2", "p2")
    assert _heartbeat("s2")["high"]["engaged"] is True
    assert "zebra_personal_note" in out and "zebra_deploy_runbook" in out


def test_a_machine_that_cannot_serve_high_stays_lean_and_says_why(fakes, project, monkeypatch):
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    monkeypatch.setattr(E, "unavailable_reason", lambda m: f"MLX is not installed ({E.INSTALL_HINT})")
    B.build_index(project["md"], project["idx"])
    assert "zebra_deploy_runbook" in _serve_warm("s1", "p1")
    hb = _heartbeat("s1")["high"]
    assert hb["status"] == "unavailable" and "MLX is not installed" in hb["reason"]
    assert not os.path.exists(project["high"]) and _FakeQwen.made == []
    meaning, note = H.doctor_meaning("dense x", "high")
    assert "not servable here" in meaning and note.startswith("High degrades to lean: MLX is not installed")


def test_a_failed_model_load_stays_lean_and_never_raises(fakes, project, monkeypatch):
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")

    def boom(*a):
        raise RuntimeError("metal device lost")

    monkeypatch.setattr(E, "BACKEND", boom)
    B.build_index(project["md"], project["idx"])
    assert "zebra_deploy_runbook" in _serve_warm("s1", "p1")
    end = time.time() + 5
    while H.status().get("status") == "loading" and time.time() < end:
        time.sleep(0.01)
    assert H.status()["status"] == "failed" and "metal device lost" in H.status()["reason"]
    assert "zebra_deploy_runbook" in _serve_warm("s2", "p2")
    assert _heartbeat("s2")["high"]["status"] == "failed"


def test_a_lean_warm_server_writes_no_high_record(fakes, project):
    B.build_index(project["md"], project["idx"])
    _serve_warm("s1", "p1")
    assert "high" not in _heartbeat("s1")
    assert _FakeQwen.made == [] and not os.path.exists(project["high"])


def test_doctor_names_a_loaded_high_server(fakes, project, monkeypatch):
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    B.build_index(project["md"], project["idx"])
    meaning, note = H.doctor_meaning("dense x", "high")
    assert "No warm server has loaded" in note
    _serve_warm("s1", "p1")
    _wait_fresh(project)
    _serve_warm("s1", "p2")
    meaning, note = H.doctor_meaning("dense x", "high")
    assert note == "" and "serving high" in meaning and "4.0 GiB resident" in meaning
    assert H.doctor_meaning("dense x", "lean") == ("dense x", "")


# --------------------------------------------------------------------------- #
# Setup: the one download, previewed with its size
# --------------------------------------------------------------------------- #
def test_fetch_previews_the_size_and_downloads_only_with_yes(tmp_path, monkeypatch, capsys):
    from memory import rerank_mlx as R

    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    calls = []
    monkeypatch.setattr(E, "fetch", lambda model: calls.append(model) or "/snap")
    monkeypatch.setattr(R, "fetch", lambda model: calls.append(model) or "/snap")
    assert setup_cli.main(["--profile", "--fetch"]) == 0
    out = capsys.readouterr().out
    assert "~4.2 GB" in out and "Nothing was downloaded" in out and calls == []
    assert setup_cli.main(["--profile", "high", "--fetch", "--yes"]) == 0
    out = capsys.readouterr().out
    assert calls[0] == _SPACE and "downloaded Qwen/Qwen3-Embedding-4B" in out


# --------------------------------------------------------------------------- #
# The real backend — only where MLX and the pinned weights already are
# --------------------------------------------------------------------------- #
def test_the_real_mlx_backend_embeds_asymmetric_normalizable_vectors(monkeypatch):
    pytest.importorskip("mlx.core")
    pytest.importorskip("mlx_lm")
    root = os.environ.get("HIPPO_TEST_MLX_CACHE") or E.cache_dir()
    if not E.model_cached(_SPACE, root):
        pytest.skip("Qwen3-Embedding-4B MLX weights not downloaded")
    emb = E._MLXEmbedder(_SPACE, 2560, E.snapshot_dir(_SPACE, root))
    docs = B._normalize_rows(list(emb.embed([
        "The zebra service deploys through the canary lane first.",
        "Llama billing cutover freezes the ledger before invoice replay.",
    ])))
    q = B._normalize_rows(list(emb.query_embed(["how do we deploy zebra"])))[0]
    assert docs.shape == (2, 2560)
    assert float(docs[0] @ q) > float(docs[1] @ q)
    solo = B._normalize_rows(list(emb.embed(["The zebra service deploys through the canary lane first."])))[0]
    assert float(solo @ docs[0]) > 0.999  # right-padded batching is exact for a causal model
