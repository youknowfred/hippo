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
    os.makedirs(data, exist_ok=True)  # a fixture before this one may have put the daemon's socket dir in it
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
# MLX's buffer cache is capped before any model loads
# --------------------------------------------------------------------------- #
class _FakeMX:
    def __init__(self):
        self.limits = []

    def set_cache_limit(self, n):
        self.limits.append(n)
        return 0


@pytest.mark.parametrize("raw, mb", [(None, 512), ("", 512), ("256", 256), ("0", 0),
                                     ("-5", 512), ("lots", 512), (" 1024 ", 1024)])
def test_the_cache_cap_reads_whole_megabytes_else_the_default(monkeypatch, raw, mb):
    if raw is None:
        monkeypatch.delenv(E.CACHE_ENV, raising=False)
    else:
        monkeypatch.setenv(E.CACHE_ENV, raw)
    assert E.cache_limit_bytes() == mb * 2**20


def test_capping_sets_the_limit_and_never_fails_a_load(monkeypatch):
    monkeypatch.delenv(E.CACHE_ENV, raising=False)
    mx = _FakeMX()
    assert E.cap_mlx_cache(mx) == 512 * 2**20
    assert mx.limits == [512 * 2**20]
    assert E.cap_mlx_cache(object()) is None  # an MLX without the call: no cap, no error


def test_both_loaders_cap_the_cache_before_they_load():
    import inspect

    from memory import rerank_mlx as R

    for src in (inspect.getsource(E._MLXEmbedder._load), inspect.getsource(R._MLXReranker._load)):
        assert "cap_mlx_cache(mx)" in src
        assert src.index("cap_mlx_cache(mx)") < src.index("load_model(Path(snap)")


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
    import mlx.core as mx

    assert mx.get_cache_memory() <= E.cache_limit_bytes()  # the cap held through the embeds


# --------------------------------------------------------------------------- #
# HCR-9: whole-memory rows in the high space
# --------------------------------------------------------------------------- #
def test_the_high_twin_embeds_each_whole_file_and_lean_keeps_its_description_rows(fakes, project):
    lean = B.build_index(project["md"], project["idx"])
    with E.using_space(_SPACE):
        high = B.build_index(project["md"], project["high"], allow_download=False)
    assert high["row_text"] == "whole" and "row_text" not in lean
    docs = _FakeQwen.made[0].docs
    whole = open(os.path.join(project["md"], "zebra_deploy_runbook.md"), encoding="utf-8").read()
    assert whole in docs and whole.startswith("---\nname: zebra_deploy_runbook")
    assert not any(d.startswith("zebra deploy runbook. ") for d in docs)  # no description row
    # both manifests carry the whole-file key, so the twin can be compared with lean
    assert [e["row_hash"] for e in high["entries"]] == [e["row_hash"] for e in lean["entries"]]
    assert high["entries"][0]["row_hash"] != high["entries"][0]["hash"]
    assert H.fresh(B.load_index(project["idx"]), B.load_index(project["high"]), _SPACE)


def test_a_whole_row_is_re_embedded_only_when_its_file_changes(fakes, project):
    B.build_index(project["md"], project["idx"])
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is True
    qwen = _FakeQwen.made[0]
    n = len(qwen.docs)
    with E.using_space(_SPACE):
        B.build_index(project["md"], project["high"], allow_download=False)
    assert len(qwen.docs) == n  # nothing changed: every row reused
    # a frontmatter-only edit: the description, and so the lean row, is untouched
    path = os.path.join(project["md"], "llama_billing_cutover.md")
    text = open(path, encoding="utf-8").read()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace("  type: project\n", "  type: project\n  confidence: verified\n"))
    lean = B.refresh_index(project["md"], project["idx"])  # SessionStart's refresh sees the new key
    assert [e["name"] for e in lean["entries"]] == ["llama_billing_cutover", "zebra_deploy_runbook"]
    assert not H.fresh(B.load_index(project["idx"]), B.load_index(project["high"]), _SPACE)
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is True
    assert len(qwen.docs) == n + 1 and "confidence: verified" in qwen.docs[-1]


def test_a_twin_built_with_description_rows_is_not_served_and_heals(fakes, project, monkeypatch):
    from memory import build_index_dense as BD

    B.build_index(project["md"], project["idx"])
    with monkeypatch.context() as before:  # how every twin was built before HCR-9
        before.setattr(BD, "WHOLE_ROW_MODELS", frozenset())
        with E.using_space(_SPACE):
            old = B.build_index(project["md"], project["high"], allow_download=False)
    assert "row_text" not in old and old["dense_ready"]
    lean, twin = B.load_index(project["idx"]), B.load_index(project["high"])
    assert not H.fresh(lean, twin, _SPACE)  # same entries, same model: the rows embed the other text
    qwen = _FakeQwen.made[0]
    n = len(qwen.docs)
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is True
    assert B.load_index(project["high"]).manifest["row_text"] == "whole"
    new = qwen.docs[n:]
    assert len(new) == len(_MEMS) and all(d.startswith("---\n") for d in new)  # entries only; chunk rows reused


def test_a_lean_manifest_from_before_the_whole_file_key_still_pairs_with_its_twin(fakes, project):
    B.build_index(project["md"], project["idx"])
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is True
    path = os.path.join(project["idx"], "manifest.json")
    manifest = json.load(open(path, encoding="utf-8"))
    for e in manifest["entries"]:
        del e["row_hash"]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    assert H.fresh(B.load_index(project["idx"]), B.load_index(project["high"]), _SPACE)
    again = B.refresh_index(project["md"], project["idx"])  # the next refresh adds the key, embedding nothing
    assert all(e.get("row_hash") for e in again["entries"])


def test_one_session_builds_a_twin_and_the_others_wait_for_it(fakes, project):
    B.build_index(project["md"], project["idx"])
    held = H._build_lock(project["high"])  # another session's server, mid-build
    assert held is not None and H._build_lock(project["high"]) is None
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is False
    assert _FakeQwen.made == [] and B.load_index(project["high"]) is None  # nothing embedded here
    held.close()
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is True


def test_a_twin_build_cut_short_resumes_where_it_stopped(fakes, project, monkeypatch):
    for i in range(5):
        _write_mem(project["md"], f"otter_note_{i}", f"Otter note {i} — the {i}th thing the otter team wrote down.")
    B.build_index(project["md"], project["idx"])
    monkeypatch.setattr(H, "_PASS_ROWS", 2)
    real, calls = B.build_index, []

    def dies_on_the_third_pass(*a, **kw):
        calls.append(kw.get("max_new_rows"))
        if len(calls) == 3:
            raise RuntimeError("the session ended")
        return real(*a, **kw)

    monkeypatch.setattr(B, "build_index", dies_on_the_third_pass)
    with pytest.raises(RuntimeError):
        H.refresh_tier(project["md"], project["idx"], _SPACE)
    assert calls == [2, 2, 2]
    partial = B.load_index(project["high"])
    assert not partial.dense_ready and H._rows_done(partial, _SPACE) == 4  # two passes kept
    assert H._build_lock(project["high"]) is not None  # the lock went with the failed build
    monkeypatch.setattr(B, "build_index", real)
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is True
    done = B.load_index(project["high"])
    embedded = _FakeQwen.made[0].docs
    assert len(embedded) == len(done.entries) + len(done.body_chunks) == len(set(embedded))  # none twice


def test_a_model_that_embeds_nothing_stops_the_pass_loop(fakes, project, monkeypatch):
    B.build_index(project["md"], project["idx"])
    E.high_embedder(_SPACE)
    monkeypatch.setattr(_FakeQwen, "embed", lambda self, texts: (_ for _ in ()).throw(RuntimeError("metal")))
    calls = []
    real = B.build_index
    monkeypatch.setattr(B, "build_index", lambda *a, **kw: calls.append(1) or real(*a, **kw))
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is False
    assert len(calls) == 2  # the second pass added no row: stop until the next wake


def test_a_lean_index_behind_the_files_costs_one_pass_a_wake_not_a_chase(fakes, project, monkeypatch):
    """The twin is built from the files. When a file changed after the lean index was last
    refreshed, the twin can match the files and still not match lean: rebuilding it again
    changes nothing, so the loop stops until lean's next refresh."""
    B.build_index(project["md"], project["idx"])
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is True
    stale_lean = open(os.path.join(project["idx"], "manifest.json"), "rb").read()
    _write_mem(project["md"], "zebra_deploy_runbook", "How the zebra service is deployed now — blue/green.")
    B.build_index(project["md"], project["idx"])  # lean and the twin both move to the new file...
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is True
    with open(os.path.join(project["idx"], "manifest.json"), "wb") as fh:
        fh.write(stale_lean)  # ...and lean is behind the files again (as after a mid-session edit)
    calls = []
    real = B.build_index
    monkeypatch.setattr(B, "build_index", lambda *a, **kw: calls.append(1) or real(*a, **kw))
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is False
    assert len(calls) == 1


def test_an_empty_tier_does_not_keep_high_from_serving(fakes, project, monkeypatch, tmp_path):
    """`hippo init` creates the private tier (`.claude/memory.local`) empty, and SessionStart
    indexes it: a manifest with no entries, which can never be dense-ready. Recall skips an
    empty tier on every path; the high plan waited for its twin forever, so on a project with
    an empty private tier warm recall never served high (v1.48.0 and v1.49.0)."""
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    empty = str(tmp_path / "private-tier")
    os.makedirs(empty)
    monkeypatch.setenv("HIPPO_LOCAL_MEMORY_DIR", empty)
    B.build_index(project["md"], project["idx"])
    tiers = H._tiers(project["md"])
    assert empty in [c for c, _i in tiers]
    empty_idx = next(i for c, i in tiers if c == empty)
    B.build_index(empty, empty_idx)  # SessionStart's refresh of the empty tier
    assert len(B.load_index(empty_idx)) == 0
    _serve_warm("s1", "p1")
    _wait_fresh(project)
    assert "zebra_deploy_runbook" in _serve_warm("s2", "p2", "zebra canary lane deploy steps")
    assert _heartbeat("s2")["high"]["engaged"] is True
    # and the loop builds nothing for it: no twin directory, no passes
    assert H.refresh_tier(empty, empty_idx, _SPACE) is True
    assert not os.path.exists(os.path.join(H.high_index_dir(empty_idx), "manifest.json"))


# --------------------------------------------------------------------------- #
# EMB-1: the model served by the shared daemon, and the daemon going away
# --------------------------------------------------------------------------- #
def test_a_warm_server_serves_high_through_the_shared_daemon_and_survives_its_loss(
        fakes, project, monkeypatch):
    import signal
    import tempfile

    from memory import embed_daemon as D

    data = tempfile.mkdtemp(prefix="hd")  # a short dir: the socket's main path
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", data)
    monkeypatch.delenv(D.ENABLE_ENV, raising=False)
    monkeypatch.setattr(D, "SPAWN_ARGS", ["--fake"])
    monkeypatch.setattr(E, "BACKEND", E._default_backend)
    monkeypatch.setenv("HIPPO_RETRIEVAL_PROFILE", "high")
    try:
        B.build_index(project["md"], project["idx"])
        _serve_warm("s1", "p1")
        _wait_fresh({"idx": project["idx"], "high": project["high"]})
        _serve_warm("s2", "p2")
        hb = _heartbeat("s2")["high"]
        first = hb["daemon_pid"]
        assert hb["engaged"] is True and first != os.getpid() and hb["resident_bytes"] == 1234

        os.kill(first, signal.SIGKILL)  # the daemon goes (an idle exit looks the same)
        end = time.time() + 5
        while D.ping() is not None and time.time() < end:
            time.sleep(0.02)
        assert "zebra_deploy_runbook" in _serve_warm("s3", "p3")  # found it gone: one prompt
        assert "zebra_deploy_runbook" in _serve_warm("s4", "p4")  # lean, and asks for a revive
        assert _heartbeat("s4")["high"]["engaged"] is False
        end = time.time() + 15
        while time.time() < end and not (H.status().get("status") == "ready"
                                          and H.status().get("daemon_pid") not in (None, first)):
            time.sleep(0.02)
        assert "zebra_deploy_runbook" in _serve_warm("s5", "p5")
        hb = _heartbeat("s5")["high"]
        assert hb["engaged"] is True and hb["daemon_pid"] != first
    finally:
        info = D.ping()
        if info:
            os.kill(info["pid"], signal.SIGKILL)
        import shutil

        shutil.rmtree(data, ignore_errors=True)


def test_a_twin_is_never_rebuilt_while_its_shared_daemon_is_gone(fakes, project):
    """EMB-1: every embed would fail and the build would write the twin back without its
    rows; the catch-up waits for a served call to revive the daemon instead."""
    B.build_index(project["md"], project["idx"])
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is True
    _write_mem(project["md"], "quokka_pager_rota", "Quokka pager rota.")
    time.sleep(0.01)
    B.build_index(project["md"], project["idx"])
    before = open(os.path.join(project["high"], B._MANIFEST_NAME), "rb").read()
    E._LOADED[_SPACE].alive = False
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is False
    assert open(os.path.join(project["high"], B._MANIFEST_NAME), "rb").read() == before
    assert E.loaded(_SPACE) is None


# --------------------------------------------------------------------------- #
# Digest sections: lean's sub-index only, never embedded for the high twin
# --------------------------------------------------------------------------- #
_FOLDED = """---
name: folded-ops
description: "Folded digest of old ops notes"
metadata:
  type: project
---
### quokka-pager-rota

Quokka pager rota for the winter on-call weeks and the handover rules

The rota swaps on Tuesdays after the standup; marmalade wombat is a phrase that lives only
in this section body, far below the digest's description and its opening lines.

### ibis-cache-flush

Ibis cache flush order for the edge nodes before a schema change
"""


def _fold(project):
    """Give the project a declared fold digest with two sections; returns the texts the
    lean sub-index embeds for them (each section's row and its opening chunk)."""
    from memory import index_sections as S

    with open(os.path.join(project["md"], "folded-ops.md"), "w", encoding="utf-8") as fh:
        fh.write(_FOLDED)
    with open(os.path.join(project["md"], "hippo.json"), "w", encoding="utf-8") as fh:
        json.dump({"fold_digests": ["folded-*.md"]}, fh)
    B.build_index(project["md"], project["idx"])
    sm = S.read_manifest(project["idx"])
    assert sm["dense_ready"] and sm["model"] == B.DEFAULT_MODEL and sm["count"] == 2
    return [e["doc_text"] for e in sm["entries"]] + [c["text"] for c in sm["body_chunks"]]


def _section_files(index_dir):
    return sorted(n for n in os.listdir(index_dir) if n.startswith("sections."))


def _bytes(index_dir, names):
    return {n: open(os.path.join(index_dir, n), "rb").read() for n in names}


def test_a_high_twin_embeds_no_digest_section_and_keeps_no_section_sub_index(fakes, project):
    """Recall reads digest sections from the lean index dir and skips them on a high call
    (``recall_sections``: ``sec.model != idx.model``), so a sub-index under ``high/`` is
    never read. The twin's build must not put a section through the big model for one."""
    section_texts = _fold(project)
    assert len(section_texts) == 3  # two section rows and the one opening chunk long enough
    lean_files = _section_files(project["idx"])
    lean_before = _bytes(project["idx"], lean_files)
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is True
    qwen = _FakeQwen.made[0]
    high = B._read_manifest_json(project["high"])
    assert not set(section_texts) & set(qwen.docs)
    assert len(qwen.docs) == len(high["entries"]) + len(high["body_chunks"])  # the twin's own rows, once each
    assert _section_files(project["high"]) == []
    # the twin's main manifest still names the slugs, as lean's does: a served call reads
    # that record off the index it was handed to tell whether a prompt names a section
    assert high["sections"] == B._read_manifest_json(project["idx"])["sections"]
    assert sorted(high["sections"]["slugs"]) == ["ibis-cache-flush", "quokka-pager-rota"]
    # the no-op refresh path reaches the sub-index too (build_index's other call site)
    with E.using_space(_SPACE):
        assert B.refresh_index(project["md"], project["high"]) is not None
    assert not set(section_texts) & set(qwen.docs) and _section_files(project["high"]) == []
    # lean's sub-index is not the twin's to touch
    assert _section_files(project["idx"]) == lean_files
    assert _bytes(project["idx"], lean_files) == lean_before


def test_a_twin_holding_an_old_section_sub_index_stays_fresh_and_sheds_it_at_its_next_build(fakes, project):
    """Twins built before this fix hold a ``sections.json`` that names lean's model over
    2,560-wide rows. It does not make the twin stale (no rebuild for it); the next build the
    twin needs anyway removes it, and re-embeds only what changed."""
    _fold(project)
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is True
    from memory import index_sections as S

    old_matrix = "sections.0123456789abcdef.npy"
    stale = dict(S.read_manifest(project["idx"]), dim=2560, dense_file=old_matrix)
    assert stale["model"] == B.DEFAULT_MODEL  # what the old build recorded for its 2,560-wide rows
    with open(os.path.join(project["high"], "sections.json"), "w", encoding="utf-8") as fh:
        json.dump(stale, fh)
    np.save(os.path.join(project["high"], old_matrix), np.zeros((3, 2560), dtype="float32"))
    held = _section_files(project["high"])
    assert {old_matrix, "sections.json"} <= set(held)
    qwen = _FakeQwen.made[0]
    n = len(qwen.docs)
    main_before = open(os.path.join(project["high"], B._MANIFEST_NAME), "rb").read()
    assert H.fresh(B.load_index(project["idx"]), B.load_index(project["high"]), _SPACE)
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is True
    # fresh: no build ran, so nothing was embedded, rewritten or removed
    assert len(qwen.docs) == n and _section_files(project["high"]) == held
    assert open(os.path.join(project["high"], B._MANIFEST_NAME), "rb").read() == main_before
    _write_mem(project["md"], "quokka_pager_rota", "Quokka pager rota.")
    time.sleep(0.01)
    B.build_index(project["md"], project["idx"])
    lean_files = _section_files(project["idx"])
    assert H.refresh_tier(project["md"], project["idx"], _SPACE) is True
    assert _section_files(project["high"]) == []
    assert len(qwen.docs) == n + 1 and qwen.docs[-1].startswith("---\nname: quokka_pager_rota")
    assert _section_files(project["idx"]) == lean_files and len(lean_files) == 2
