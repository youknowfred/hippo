"""Wikilink parse/resolve core for agent-memory files (Tier 3 of the activation roadmap).

The dependency-leaf half of the ``links`` family (module-size ratchet split, GRF-6 round;
``links.py`` remains the façade and re-exports every name here — import from
``memory.links`` unless you are inside the family). Parses ``[[name]]`` markers across the
corpus into an adjacency graph and resolves each target to a memory file — slug-normalizing
so ``_``/``-`` variants and a dropped category prefix resolve (e.g.
``[[151-avenue-a-is-standard-size]]`` → ``feedback_151_avenue_a_is_standard_size``) WITHOUT
falsely resolving genuinely-absent targets (``[[ship-roadmap]]``,
``[[decision-question-coverage]]``).

Node identity is the filename STEM (``foo``, never ``foo.md``) — a deliberate clean break
(GRA-2, one-canonical-name invariant): every other module that joins against graph output
(staleness / soak / archive / telemetry) keys by stem, so basename output forced each
consumer to strip ``.md`` by hand before every join, and two of them got it subtly
different. ``files``, ``adjacency`` (keys AND values), ``raw_targets``, ``unresolved`` and
every query method speak stems; nothing in graph output carries a ``.md`` suffix.

Resolution aliases per file (all normalized to lower-hyphen):
  1. the full filename stem               (always registered — filenames are unique)
  2. the stem with its FIRST ``_``/``-`` segment stripped
  3. the frontmatter ``name:`` slug

Tiering (COR-9): a full-stem claim (tier 1) always beats a soft claim (tiers 2–3) — a
soft alias that collides with an existing full-stem alias is simply not registered and
never poisons the full-stem claim. WITHIN the soft tier, aliases are registered
UNCONDITIONALLY so that two DIFFERENT files claiming the same soft alias land in
``_ambiguous`` and ``resolve()`` refuses both (previously the alphabetically-first file
silently won and ``[[target]]`` resolved to the wrong memory with no signal). The same
file claiming one alias twice (its stripped stem == its ``name:`` slug) is NOT a
collision. Ambiguous claimants are tracked so the linter can name both files.

Typed edges (GRA-4, corpus format 2): additive frontmatter relations —
``supersedes: [name]``, ``contradicts: [name]``, ``refines: [name]`` — each a list of
memory names/stems, read top-level OR under ``metadata:`` (the ``cited_paths`` read
convention). ``[[wikilinks]]`` remain the untyped edge; typed relations live in their OWN
structures (``typed``/``typed_inbound``), never merged into ``adjacency`` itself. RET-13
(owner-directed): recall's 1-hop expansion (GRA-1) now ALSO reads ``refines``/
``derives-from`` directly out of these typed structures (both directions) as an
additional traversal source, alongside the untyped adjacency — so a memory related only
via one of those frontmatter relations, with no body wikilink, is reachable too.
``supersedes``/``contradicts`` are deliberately excluded from that traversal, unchanged —
recall consumes them only for the dedicated demotion/annotation logic GRA-4 already
built (see recall.py's ``_typed_relation_maps``/``_expand_neighbors``). Targets resolve
through the SAME alias tiers as wikilinks (one resolution path — ``resolve()``);
unresolved typed targets land in ``typed_unresolved`` for the linter. Typed edges
round-trip through links.json so recall reads them O(1) with zero corpus re-reads.

``planned:`` declarations (GRF-6) ride the same one-parse pass: ``parse_planned`` reads
the deliberate-forward-reference key and ``LinkGraph.planned_raw`` carries it per stem —
lint metadata for ``lint_links._classify_planned``, deliberately NOT an edge and NOT a
typed relation (it must never enter adjacency, typed maps, or recall traversal).

Read-only throughout; the family's one write primitive (``add_typed_relation``) stays in
the façade. Never raises into a caller.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Set, Tuple

from .markdown_code import strip_code
from .provenance import _iter_memory_files, parse_frontmatter

_WIKILINK_RE = re.compile(r"\[\[([^\]\[]+?)\]\]")

# COR-20: code spans and fenced blocks are NOT link surface. A memory that merely
# WRITES ABOUT the convention ("append a `[[wikilink]]` into one side") used to mint a
# phantom edge to a memory named "wikilink" — found dogfooding this repo's own corpus,
# where FOUR of six dangling targets were prose (`[[child]]`, `[[wikilink]]`,
# `[[wikilinks]]`), reported as broken references forever. The masker moved to
# markdown_code.py at COR-21, when the threat lint's HTML-comment class turned out to
# need the identical treatment; the rationale for both now lives there.

# GRA-4: the closed set of typed frontmatter relations. Order is the render order every
# consumer (lint report, recall annotations) uses, so output stays deterministic.
# "derives-from" (DRM-6): derivation provenance — a dream-generated schema/hypothesis
# PARENT declares the child memories it was abstracted from (`derives-from: [a, b]` on
# the parent; hand-authored derivations are welcome to use it too). Like `refines`: no
# penalty, no annotation (being derived-from isn't evidence a memory is wrong or
# disputed) -- but RET-13 seeds both into recall's 1-hop graph expansion as an
# additional traversal source, and it JOINS decision chains (history._CHAIN_RELATIONS),
# so provenance walks and DRM-5 reward propagation follow derivation lineage exactly
# like supersede/refine lineage.
TYPED_RELATIONS = ("supersedes", "contradicts", "refines", "derives-from")

# --------------------------------------------------------------------------- #
# DRM-2/DRM-3: the machine-managed dream:links block — the ONE canonical grammar.
#
# /dream (memory/dream.py) auto-applies its Tier-A edges as stamped lines inside a
# delimited block appended to a memory's BODY:
#
#     <!-- dream:links -->
#     [[other-memory]] <!-- dream: bridge · pass=p7 · edge=p7-e2 · cofire=0.71 · q="…" -->
#     <!-- dream: refines other-memory · pass=p7 · edge=p7-e3 · cofire=0.68 -->
#     <!-- /dream:links -->
#
# Bridge/completion lines carry a real ``[[wikilink]]`` (an untyped edge the normal
# ``parse_wikilinks`` pass reads); a refines line is a PURE COMMENT stamp — its actual edge
# lives in frontmatter via ``add_typed_relation`` — deliberately bracket-free so the
# wikilink regex can never read it as an untyped edge. ``doc_text`` (name + description)
# is never touched, so the semantic index is stable; only the adjacency graph changes.
#
# ``HIPPO_DREAM`` gates whether the graph ADMITS these edges (default: yes — an applied
# edge is live in recall immediately, DRM-2's whole point). The DRM-3 A/B harness sets
# ``HIPPO_DREAM=0`` on its OFF arm to measure the same corpus without them: the block is
# stripped before wikilink parsing and dream-stamped refines targets are dropped from the
# typed maps. The links.json cache records which view it was built under and reads as a
# MISS on mismatch, so an admitted-view cache can never silently serve a filtered arm (or
# vice versa).
# --------------------------------------------------------------------------- #
DREAM_BLOCK_OPEN = "<!-- dream:links -->"
DREAM_BLOCK_CLOSE = "<!-- /dream:links -->"
_DREAM_BLOCK_RE = re.compile(
    re.escape(DREAM_BLOCK_OPEN) + r".*?" + re.escape(DREAM_BLOCK_CLOSE) + r"\n?",
    re.DOTALL,
)
# The refines stamp: ``<!-- dream: refines <target> · … -->`` (target = first token).
_DREAM_REFINES_STAMP_RE = re.compile(r"<!--\s*dream:\s*refines\s+(\S+)")


def dream_edges_admitted() -> bool:
    """Whether the graph admits dream-discovered edges (``HIPPO_DREAM``; default TRUE).

    Only an explicit falsy value filters — mirrors ``recall._salience_enabled``'s parsing so
    ``HIPPO_DREAM=0``/``false`` is the opt-out and junk values stay on the default.
    """
    return os.environ.get("HIPPO_DREAM", "").strip() not in ("0", "false", "False")


def strip_dream_edges(text: str) -> Tuple[str, List[str]]:
    """``(text with every dream:links block removed, [refines targets those blocks stamp])``.

    The DRM-3 OFF-arm filter: removing the block removes the bridge/completion wikilinks;
    the returned stamp targets let the typed-edge pass drop the matching ``refines``
    frontmatter entries (the frontmatter itself is indistinguishable from hand-authored —
    the stamp is what marks it dream-discovered). Pure; never raises.
    """
    refines: List[str] = []
    for block in _DREAM_BLOCK_RE.findall(text or ""):
        refines.extend(m.group(1) for m in _DREAM_REFINES_STAMP_RE.finditer(block))
    return _DREAM_BLOCK_RE.sub("", text or ""), refines


def normalize_slug(s: str) -> str:
    """Lowercase; unify ``_``, spaces and runs of separators to single ``-``; trim."""
    s = (s or "").strip().lower()
    if s.endswith(".md"):
        s = s[:-3]
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


def _strip_first_segment(stem: str) -> Optional[str]:
    """``feedback_151_avenue...`` -> ``151-avenue...`` (normalized); None if no segment."""
    norm = normalize_slug(stem)
    parts = norm.split("-", 1)
    return parts[1] if len(parts) == 2 and parts[1] else None


def parse_wikilinks(text: str) -> List[str]:
    """Ordered, de-duped ``[[target]]`` targets in ``text`` (``|display`` + ``#anchor`` stripped).

    COR-20: code spans (`` `[[x]]` ``) and fenced blocks are stripped first — they are
    documentation ABOUT links, not links. DRM-2's machine-managed ``dream:links`` block is
    deliberately fence-free, so its stamped edges are unaffected.
    """
    seen: Set[str] = set()
    out: List[str] = []
    for m in _WIKILINK_RE.finditer(strip_code(text)):
        raw = m.group(1).strip()
        raw = raw.split("|", 1)[0].split("#", 1)[0].strip()
        if raw and raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out


def parse_typed_relations(fm: dict) -> Dict[str, List[str]]:
    """``{relation: [raw targets]}`` from parsed frontmatter — only non-empty relations.

    GRA-4: each relation key is read top-level FIRST, then under ``metadata:`` — the exact
    ``cited_paths``/``source_commit`` convention ``staleness.read_provenance`` establishes
    (the corpus uses both frontmatter schemas, and a top-level-only read would make typed
    edges silently inert for the nested one). Values are lists of memory names/stems; a
    bare string is tolerated as a one-element list (``supersedes: old-memory`` is the
    natural single-target hand-authored form, mirroring ``_extract_invalid_after``'s
    scalar tolerance). Non-string items are dropped; order is preserved, de-duped. Pure;
    never raises.
    """
    out: Dict[str, List[str]] = {}
    if not isinstance(fm, dict):
        return out
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    for rel in TYPED_RELATIONS:
        val = fm.get(rel)
        if val is None:
            val = (meta or {}).get(rel)
        if isinstance(val, str):
            val = [val]
        if not isinstance(val, list):
            continue
        seen: Set[str] = set()
        targets: List[str] = []
        for t in val:
            if not isinstance(t, str) or not t.strip():
                continue
            t = t.strip()
            if t not in seen:
                seen.add(t)
                targets.append(t)
        if targets:
            out[rel] = targets
    return out


# GRF-6: the deliberate-forward-reference marker key. NOT a typed relation — a planned
# declaration is lint metadata about a not-yet-written target, never an edge: it must
# never enter adjacency, the typed maps, or recall traversal, so it lives outside
# TYPED_RELATIONS on purpose.
PLANNED_KEY = "planned"


def parse_planned(fm: dict) -> List[str]:
    """``planned:`` targets from parsed frontmatter — the GRF-6 deliberate-forward-reference
    declaration (``planned: [not-yet-written]``, top-level or under ``metadata:``, a bare
    string tolerated as a one-element list — exactly ``parse_typed_relations``' two-schema
    read). A declared target says "this ``[[wikilink]]`` dangles ON PURPOSE until the
    memory is written": the link lint reclassifies a matching dangling wikilink on the
    SAME source out of the advisory ``dangling`` class into the informational ``planned``
    class (``lint_links._classify_planned``; archived and cross-tier targets are never
    maskable there). Non-string items dropped; order preserved, de-duped. Pure; never
    raises.
    """
    if not isinstance(fm, dict):
        return []
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    val = fm.get(PLANNED_KEY)
    if val is None:
        val = (meta or {}).get(PLANNED_KEY)
    if isinstance(val, str):
        val = [val]
    if not isinstance(val, list):
        return []
    seen: Set[str] = set()
    out: List[str] = []
    for t in val:
        if not isinstance(t, str) or not t.strip():
            continue
        t = t.strip()
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


class LinkGraph:
    """Resolved wikilink adjacency over the memory corpus. All nodes are STEMS."""

    def __init__(self, memory_dir: str, texts: Optional[Dict[str, str]] = None):
        self.memory_dir = memory_dir
        # GRA-6: ``texts`` lets a caller that ALREADY read the corpus (build_index reads
        # every file's full text anyway) construct the graph with ZERO extra file reads.
        # Keys are stems or filenames ({filename-or-stem: text}); ``None`` keeps the
        # original read-from-disk behavior.
        self._texts_in = texts
        self.files: List[str] = []  # stems (unique — one flat dir, one stem per file)
        self._alias_to_stem: Dict[str, str] = {}
        # alias -> claimant stems, so lint can NAME the colliders (COR-9). Membership
        # checks ("alias in self._ambiguous") read identically to the old Set form.
        self._ambiguous: Dict[str, Set[str]] = {}
        # Aliases claimed at full-stem tier (pass 1). A soft claim colliding with one of
        # these is silently dropped — full-stem beats soft, and the losing soft claim
        # must never mark the winning full-stem alias ambiguous.
        self._stem_tier: Set[str] = set()
        self.raw_targets: Dict[str, List[str]] = {}  # stem -> raw [[targets]]
        self.adjacency: Dict[str, Set[str]] = {}  # stem -> resolved target stems
        self.unresolved: Dict[str, List[str]] = {}  # stem -> raw targets that didn't resolve
        # Reverse adjacency, built ONCE alongside the forward pass — the single place in
        # the codebase that inverts the graph (GRA-2 acceptance criterion). archive.py and
        # the audit skill consume it via inbound()/isolates() instead of re-inverting.
        self._inbound: Dict[str, Set[str]] = {}  # stem -> stems that link TO it
        # Typed edges (GRA-4) — SPARSE (a stem appears only when it carries the relation),
        # deliberately separate from ``adjacency`` so typed relations never leak into the
        # untyped 1-hop expansion. ``_typed_inbound`` is the transpose, built in the same
        # pass as ``_inbound`` (one inversion per direction, ever).
        self.typed_raw: Dict[str, Dict[str, List[str]]] = {}  # stem -> {rel: raw targets}
        self.typed: Dict[str, Dict[str, Set[str]]] = {}  # stem -> {rel: resolved stems}
        self.typed_unresolved: Dict[str, Dict[str, List[str]]] = {}  # stem -> {rel: misses}
        self._typed_inbound: Dict[str, Dict[str, Set[str]]] = {}  # stem -> {rel: sources}
        # GRF-6: per-stem ``planned:`` declarations (deliberate forward references) —
        # lint metadata riding the same one-parse pass, deliberately NEVER an edge
        # structure. Round-trips through links.json (schema v5) so the CACHED lint path
        # classifies planned refs with zero file reads.
        self.planned_raw: Dict[str, List[str]] = {}  # stem -> declared planned targets
        self._build()

    # -- construction ----------------------------------------------------- #
    def _register_alias(self, alias: str, stem: str, *, allow_collision: bool) -> None:
        if not alias:
            return
        if alias in self._alias_to_stem:
            if self._alias_to_stem[alias] != stem and not allow_collision:
                # Two DIFFERENT files claim this alias -> ambiguous; record BOTH claimants
                # (linter names them) and drop the alias post-build so it can't
                # false-resolve. Same-file re-claims (stripped stem == name slug) fall
                # through the != check — not a collision.
                self._ambiguous.setdefault(alias, {self._alias_to_stem[alias]}).add(stem)
            return
        self._alias_to_stem[alias] = stem

    def _register_soft_alias(self, alias: str, stem: str) -> None:
        """Tier 2/3 registration (COR-9): unconditional WITHIN the soft tier.

        Full-stem beats soft: an alias already claimed at full-stem tier is skipped
        outright (no registration, no ambiguity — the soft loser must not poison the
        full-stem claim). Everything else registers unconditionally so same-tier
        collisions across different files land in ``_ambiguous`` instead of letting
        the alphabetically-first file silently win (the pre-COR-9 bug: the
        ``not in _alias_to_stem`` guard skipped registration entirely, so the
        collision was never even SEEN).
        """
        if not alias or alias in self._stem_tier:
            return
        self._register_alias(alias, stem, allow_collision=False)

    def _read_texts(self) -> Dict[str, str]:
        """Read every memory file into ``{stem: text}`` (unreadable files skipped)."""
        texts: Dict[str, str] = {}
        for path in _iter_memory_files(self.memory_dir):
            stem = os.path.splitext(os.path.basename(path))[0]
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    texts[stem] = fh.read()
            except Exception:
                continue
        return texts

    def _build(self) -> None:
        # GRA-6: alias/edge construction runs from an in-memory mapping so a caller that
        # already read the corpus (build_index) pays zero extra file reads. Cold path
        # (no texts supplied) reads from disk exactly as before.
        if self._texts_in is not None:
            # Normalize caller keys to stems ({filename-or-stem: text}); dict iteration
            # preserves the caller's insertion order, and build_index inserts in
            # _iter_memory_files order — so cached-vs-cold graphs carry the same node order.
            texts = {
                (k[:-3] if k.endswith(".md") else k): (v or "")
                for k, v in self._texts_in.items()
            }
        else:
            texts = self._read_texts()
        # DRM-3: the HIPPO_DREAM=0 arm sees the corpus WITHOUT dream-discovered edges —
        # blocks stripped before wikilink parsing, stamped refines targets remembered so
        # the typed pass below can drop exactly those frontmatter entries. Default
        # (admitted) is a no-op: applied dream edges are live like any hand edge.
        dream_refines_filtered: Dict[str, Set[str]] = {}
        if not dream_edges_admitted():
            stripped: Dict[str, str] = {}
            for stem, text in texts.items():
                clean, stamped = strip_dream_edges(text)
                stripped[stem] = clean
                if stamped:
                    dream_refines_filtered[stem] = {normalize_slug(t) for t in stamped}
            texts = stripped
        self.files = list(texts.keys())

        # Pass 1: full-stem aliases (unique by construction) — highest-confidence tier.
        # Every alias claimed here is immune to soft-tier interference below.
        for stem in self.files:
            slug = normalize_slug(stem)
            self._register_alias(slug, stem, allow_collision=False)
            self._stem_tier.add(slug)

        # Pass 2: prefix-stripped + name-slug aliases — soft tier, registered
        # UNCONDITIONALLY (COR-9) so same-tier collisions become ambiguous instead of
        # silently resolving to whichever file sorts first. The parsed frontmatter is kept
        # for the typed-relation pass below — one parse per file, not two.
        fms: Dict[str, dict] = {}
        for stem in self.files:
            stripped = _strip_first_segment(stem)
            if stripped:
                self._register_soft_alias(stripped, stem)
            fm = parse_frontmatter(texts.get(stem, ""))
            fms[stem] = fm
            pl = parse_planned(fm)
            if pl:
                self.planned_raw[stem] = pl
            name = fm.get("name") if isinstance(fm, dict) else None
            if isinstance(name, str):
                self._register_soft_alias(normalize_slug(name), stem)

        for amb in self._ambiguous:
            self._alias_to_stem.pop(amb, None)

        # Edges — forward AND reverse in the same pass, so inbound-degree queries never
        # need a second O(V+E) inversion anywhere else.
        for stem in self.files:
            self._inbound.setdefault(stem, set())
        for stem in self.files:
            targets = parse_wikilinks(texts.get(stem, ""))
            self.raw_targets[stem] = targets
            resolved: Set[str] = set()
            missed: List[str] = []
            for t in targets:
                s = self.resolve(t)
                if s and s != stem:
                    resolved.add(s)
                    self._inbound[s].add(stem)
                elif s is None:
                    missed.append(t)
            self.adjacency[stem] = resolved
            if missed:
                self.unresolved[stem] = missed

            # Typed edges (GRA-4): SAME resolve() path as wikilinks — one resolution path,
            # so a typed target enjoys/suffers exactly the alias tiers and ambiguity
            # refusals a [[wikilink]] does. Self-targets are dropped (a memory cannot
            # supersede/contradict/refine itself), unresolved targets recorded for lint.
            raw_rels = parse_typed_relations(fms.get(stem, {}))
            if stem in dream_refines_filtered and raw_rels.get("refines"):
                # DRM-3 OFF arm: drop the refines targets this stem's dream stamps mark as
                # dream-discovered; hand-authored refines entries are untouched.
                kept = [
                    t
                    for t in raw_rels["refines"]
                    if normalize_slug(t) not in dream_refines_filtered[stem]
                ]
                if kept:
                    raw_rels["refines"] = kept
                else:
                    raw_rels.pop("refines")
            if raw_rels:
                self.typed_raw[stem] = raw_rels
            for rel, raws in raw_rels.items():
                for t in raws:
                    s = self.resolve(t)
                    if s and s != stem:
                        self.typed.setdefault(stem, {}).setdefault(rel, set()).add(s)
                        self._typed_inbound.setdefault(s, {}).setdefault(rel, set()).add(stem)
                    elif s is None:
                        self.typed_unresolved.setdefault(stem, {}).setdefault(rel, []).append(t)

    # -- queries ---------------------------------------------------------- #
    def resolve(self, target: str) -> Optional[str]:
        """Resolve a ``[[target]]`` (or filename/stem) to a corpus STEM, or None."""
        slug = normalize_slug(target)
        if slug in self._ambiguous:
            return None
        return self._alias_to_stem.get(slug)

    def ambiguous_claimants(self, target: str) -> List[str]:
        """Sorted claimant stems when ``target`` is an ambiguous alias; ``[]`` otherwise.

        The linter's window into WHY a target refused to resolve — an ambiguous
        ``[[target]]`` is a different failure from a dangling one (two files claim it
        vs. nobody does), and the fix is different (rename/disambiguate vs. create),
        so lint must be able to name both claimants.
        """
        return sorted(self._ambiguous.get(normalize_slug(target), set()))

    def resolved_via_stem(self, target: str) -> bool:
        """True when ``target`` matches a file's canonical full stem (not a soft alias)."""
        s = self.resolve(target)
        return bool(s) and normalize_slug(s) == normalize_slug(target)

    def _node(self, name: str) -> Optional[str]:
        """Resolve ``name`` (alias, stem, or filename) to a graph node stem, or None."""
        return self.resolve(name) or (name if name in self.adjacency else None)

    def outbound(self, name: str) -> Set[str]:
        """Stems ``name`` links TO. Accepts any resolvable alias; ``set()`` if unknown."""
        s = self._node(name)
        return set(self.adjacency.get(s, set())) if s else set()

    def inbound(self, name: str) -> Set[str]:
        """Stems that link TO ``name`` — "what refers to this memory?".

        Accepts any resolvable alias (like ``outbound()``); ``set()`` for an unknown name.
        Backed by the reverse adjacency built once in ``_build()`` — callers must never
        re-invert ``adjacency`` themselves.
        """
        s = self._node(name)
        return set(self._inbound.get(s, set())) if s else set()

    def typed_outbound(self, name: str, relation: str) -> Set[str]:
        """Stems ``name`` declares ``relation`` toward (e.g. the memories it supersedes).

        GRA-4. Accepts any resolvable alias, like ``outbound()``; ``set()`` for an unknown
        name or a relation ``name`` does not carry.
        """
        s = self._node(name)
        return set(self.typed.get(s, {}).get(relation, set())) if s else set()

    def typed_inbound(self, name: str, relation: str) -> Set[str]:
        """Stems that declare ``relation`` TOWARD ``name`` — the consumer-shaped direction.

        GRA-4: ``typed_inbound(y, "supersedes")`` answers recall's exact question ("who
        supersedes y?" — those sources are y's successors); ``typed_inbound(y,
        "contradicts")`` names y's conflict annotations. Backed by the transpose built
        once in ``_build()`` — callers must never re-invert ``typed`` themselves.
        """
        s = self._node(name)
        return set(self._typed_inbound.get(s, {}).get(relation, set())) if s else set()

    def all_typed_edges(self, relation: str) -> List[Tuple[str, str]]:
        """Every resolved ``(src, tgt)`` pair carrying ``relation``, corpus-wide, sorted.

        GOV-1's enumerator — the per-node accessors above answer "who relates to THIS
        stem?", but nothing walked the typed map corpus-wide, so a live ``contradicts``
        pair was only ever visible when both sides co-surfaced in one recall. Directional:
        ``(a, b)`` means ``a`` DECLARES the relation toward ``b`` (a mutual declaration
        yields both tuples — the consumer decides whether to collapse). Deliberately NOT
        built on ``typed_unresolved``: that map records targets ``resolve()`` could not map
        to a corpus file (dangling or ambiguous — the linter's concern), not edges awaiting
        a verdict. Sorted for deterministic producer/doctor output; ``[]`` for a relation
        no stem carries.
        """
        return sorted(
            (src, tgt) for src, rels in self.typed.items() for tgt in rels.get(relation, set())
        )

    def traverse(self, name: str, hops: int = 1) -> Set[str]:
        """Stems reachable from ``name`` within ``hops`` outbound edges (excludes ``name``)."""
        start = self._node(name)
        if not start or hops < 1:
            return set()
        seen: Set[str] = {start}
        frontier: Set[str] = {start}
        for _ in range(hops):
            nxt: Set[str] = set()
            for node in frontier:
                for tgt in self.adjacency.get(node, set()):
                    if tgt not in seen:
                        seen.add(tgt)
                        nxt.add(tgt)
            if not nxt:
                break
            frontier = nxt
        seen.discard(start)
        return seen

    def orphans(self) -> List[str]:
        """Stems with zero OUTBOUND resolved links (sorted).

        Orphans-vs-isolates: an orphan points at nothing but may still be pointed AT
        (a well-cited leaf note is an orphan, and that's healthy); an isolate has no
        edges in EITHER direction — genuinely disconnected from the graph. Archive/audit
        gating cares about inbound degree, never orphan-hood alone.
        """
        return sorted(s for s in self.files if not self.adjacency.get(s))

    def isolates(self) -> List[str]:
        """Stems with zero inbound AND zero outbound edges (sorted) — fully disconnected.

        Strictly a subset of ``orphans()``; see the orphans() docstring for the
        distinction. This is the "graph-isolated watch-list" primitive the audit skill
        reports (informational only — never archive-eligible on its own).
        """
        return sorted(
            s for s in self.files if not self.adjacency.get(s) and not self._inbound.get(s)
        )

    # -- GRA-8: graph observability (read-only, deterministic) ------------- #
    def _out_neighbors(self, stem: str) -> Set[str]:
        """Distinct stems ``stem`` points at — untyped wikilinks ∪ every typed relation."""
        out = set(self.adjacency.get(stem, set()))
        for tgts in self.typed.get(stem, {}).values():
            out |= tgts
        return out

    def _in_neighbors(self, stem: str) -> Set[str]:
        """Distinct stems pointing AT ``stem`` — inbound wikilinks ∪ inbound typed relations."""
        into = set(self._inbound.get(stem, set()))
        for srcs in self._typed_inbound.get(stem, {}).values():
            into |= srcs
        return into

    def undirected_neighbors(self, stem: str) -> Set[str]:
        """All neighbors of ``stem`` with edge direction ignored (self excluded)."""
        return (self._out_neighbors(stem) | self._in_neighbors(stem)) - {stem}

    def connected_components(self) -> List[List[str]]:
        """Weakly-connected components over ALL edges (wikilink + typed, direction ignored).

        Each component is a sorted stem list; components are ordered largest-first, then by
        their first stem, so identical corpora render byte-identically. A fragmented map (many
        small components) means memories that don't cross-reference — the headline "inspect your
        graph" number GRA-8 exposes and the component count the audit scorecard rolls up. An
        isolated note is its own singleton component.
        """
        seen: Set[str] = set()
        comps: List[List[str]] = []
        for start in sorted(self.files):
            if start in seen:
                continue
            stack = [start]
            seen.add(start)
            comp: List[str] = []
            while stack:
                cur = stack.pop()
                comp.append(cur)
                for nb in self.undirected_neighbors(cur):
                    if nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
            comps.append(sorted(comp))
        comps.sort(key=lambda c: (-len(c), c[0] if c else ""))
        return comps

    def degrees(self) -> List[Tuple[str, int, int, int]]:
        """Per-stem ``(stem, out, in, total)`` degree, sorted by total desc then stem.

        ``out``/``in`` count distinct neighbors in each direction; ``total`` is the undirected
        neighbor count (a reciprocal pair is one total-degree, not two). Wikilink and typed
        edges both count. Deterministic ordering for stable CLI/report output.
        """
        rows = [
            (s, len(self._out_neighbors(s)), len(self._in_neighbors(s)), len(self.undirected_neighbors(s)))
            for s in self.files
        ]
        rows.sort(key=lambda r: (-r[3], r[0]))
        return rows

    def _all_edges(self) -> List[Tuple[str, str, str]]:
        """Every resolved edge as ``(src, tgt, kind)`` — ``kind`` is ``"link"`` or a relation."""
        edges: List[Tuple[str, str, str]] = []
        for src in sorted(self.adjacency):
            for tgt in sorted(self.adjacency[src]):
                edges.append((src, tgt, "link"))
        for src in sorted(self.typed):
            for rel in sorted(self.typed[src]):
                for tgt in sorted(self.typed[src][rel]):
                    edges.append((src, tgt, rel))
        return edges

    def export(self, fmt: str) -> str:
        """Serialize the graph as ``json`` | ``dot`` | ``mermaid`` (deterministic). GRA-8.

        ``json`` is the machine-readable form (nodes, edges, component count); ``dot`` and
        ``mermaid`` render in Graphviz / any Mermaid viewer — a screenshot-able "here is my
        memory graph" artifact for a tool that markets itself graph-backed.
        """
        fmt = (fmt or "").lower()
        nodes = sorted(self.files)
        edges = self._all_edges()
        if fmt == "json":
            import json as _json

            return _json.dumps(
                {
                    "files": nodes,
                    "edges": [{"src": s, "tgt": t, "type": k} for s, t, k in edges],
                    "components": len(self.connected_components()),
                },
                indent=2,
                ensure_ascii=False,
            )
        if fmt == "dot":
            lines = ["digraph hippo {"]
            for n in nodes:
                lines.append(f'  "{n}";')
            for s, t, k in edges:
                lines.append(f'  "{s}" -> "{t}";' if k == "link" else f'  "{s}" -> "{t}" [label="{k}"];')
            lines.append("}")
            return "\n".join(lines)
        if fmt == "mermaid":
            ids = {n: f"n{i}" for i, n in enumerate(nodes)}
            lines = ["graph LR"]
            for n in nodes:
                lines.append(f'  {ids[n]}["{n}"]')
            for s, t, k in edges:
                lines.append(f"  {ids[s]} --> {ids[t]}" if k == "link" else f"  {ids[s]} -->|{k}| {ids[t]}")
            return "\n".join(lines)
        raise ValueError(f"unknown export format: {fmt!r} (expected json|dot|mermaid)")
