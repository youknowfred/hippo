"""HOT-11's tear scenarios for the crash-fault lane (``tests/test_crash_faults.py``).

The folded-sections sub-index (``sections.json`` and its ``sections.<id>.npy`` matrix) is
derived and rebuildable, so both writes are **intact**: a torn write keeps the prior
sub-index byte-for-byte (the manifest still names the matrix it was written with), the
main index build still returns, and the next build heals it.
"""

from __future__ import annotations

import os


def scenarios(arm, snap, assert_unchanged):
    """``[((module, function), class, scenario)]`` for the sub-index's two write sites."""

    def _corpus(tmp_path, monkeypatch):
        from memory import build_index as B

        monkeypatch.delenv("HIPPO_DISABLE_DENSE", raising=False)
        monkeypatch.delenv("HIPPO_DISABLE", raising=False)

        def embed(texts, allow_download=True):
            import numpy as np

            out = np.zeros((len(texts), 4), dtype="float32")
            out[:, 0] = 1.0
            return out

        monkeypatch.setattr(B, "embed_documents", embed)
        md = str(tmp_path / "memory")
        idx = str(tmp_path / "idx")
        os.makedirs(md)
        with open(os.path.join(md, "hippo.json"), "w", encoding="utf-8") as fh:
            fh.write('{"fold_digests": ["folded-*.md"]}\n')
        with open(os.path.join(md, "folded-a.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: folded-a\ndescription: d\n---\n### first-slug\n\nThe first section line\n")
        B.build_index(md, idx)
        with open(os.path.join(md, "folded-a.md"), "a", encoding="utf-8") as fh:
            fh.write("\n### second-slug\n\nA section added after the first build\n")
        return B, md, idx

    def torn(func):
        def scenario(tmp_path, monkeypatch):
            """A torn sub-index write leaves the prior sub-index intact and the main build
            returns; the prior manifest still loads with the matrix it names."""
            from memory import index_sections as S

            B, md, idx = _corpus(tmp_path, monkeypatch)
            files = sorted(os.path.join(idx, n) for n in os.listdir(idx) if n.startswith("sections."))
            before = snap(*files)
            arm(monkeypatch, "index_sections", func)
            assert B.build_index(md, idx)["dense_ready"] is True
            assert_unchanged(before)
            sec = S.load_sections(idx)
            assert [e["name"] for e in sec.entries] == ["first-slug"] and sec.dense_ready

        return scenario

    return [
        (("index_sections", "_write_matrix"), "intact", torn("_write_matrix")),
        (("index_sections", "_write_manifest"), "intact", torn("_write_manifest")),
    ]
