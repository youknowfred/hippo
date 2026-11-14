# Planning records

How hippo was planned: the roadmaps, the exploration rounds they came from, and two design
specs. Nothing here is needed to use hippo or to read its code; the engine's comments and the
[changelog](../CHANGELOG.md) cite these files by item id (`OSP-2`, `LDG-1`, `T16`), and this is
where those ids resolve.

**One file is live.** [`ROADMAP.v2.yaml`](ROADMAP.v2.yaml) is the single ledger of open work and
its status. Everything else is a record: ratified, shipped or closed, and kept for its
reasoning.

| File | What it is |
|---|---|
| [`ROADMAP.v2.yaml`](ROADMAP.v2.yaml) | **The live ledger**: every v2 item, its status, the open rulings |
| [`ROADMAP.v2.md`](ROADMAP.v2.md) | The road to v2.0.0: the design record behind the ledger (ratified 2026-10-03) |
| [`ROADMAP.v2.amendment-1.md`](ROADMAP.v2.amendment-1.md), [`-2.md`](ROADMAP.v2.amendment-2.md) | Amendments to it (ratified 2026-11-12 and 2026-11-13) |
| [`ROADMAP.yaml`](ROADMAP.yaml) | The first roadmap (v0.2 to v0.7), and still the home of the guiding invariants, the decisions and the non-goals |
| [`ROADMAP.v1.md`](ROADMAP.v1.md) | The road to v1.0.0, the open-source launch plan |
| [`ROADMAP.enhancements.yaml`](ROADMAP.enhancements.yaml), [`2`](ROADMAP.enhancements2.yaml), [`3`](ROADMAP.enhancements3.yaml), [`4`](ROADMAP.enhancements4.yaml), [`5`](ROADMAP.enhancements5.yaml) | The five post-v1 enhancement trains, closed |
| [`EXPLORATIONS.md`](EXPLORATIONS.md), [`2`](EXPLORATIONS2.md), [`3`](EXPLORATIONS3.md), [`4`](EXPLORATIONS4.md), [`5`](EXPLORATIONS5.md) | The idea catalogs each of those trains was drawn from |
| [`EXPLORATIONS.dream.md`](EXPLORATIONS.dream.md), [`ROADMAP.dream.yaml`](ROADMAP.dream.yaml), [`DRM-2.spec.md`](DRM-2.spec.md) | `/hippo:dream`: its design, its roadmap, and the auto-apply spec (shipped in v1.11.0) |
| [`LDG-1.spec.md`](LDG-1.spec.md) | The design round for tracker statuses at read time (shipped in v1.45.0) |

These files moved here from the repo root on 2026-11-13. Code comments and older changelog
entries name them without the folder (`ROADMAP.yaml`, `LDG-1.spec.md`); each name is unique, so
it is the file of that name in this directory.
