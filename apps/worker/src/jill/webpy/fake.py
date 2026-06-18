"""In-memory stand-in for ``WebPyClient`` — mirrors the server's upsert/idempotency
semantics so pipeline logic can be tested offline (no Django, no network).

Idempotency keys here mirror the server's natural keys:
  * candidate   → linkedin_url
  * lead edge   → (role, to_candidate, kind, from_company, from_candidate, depth)
  * score       → (candidate, role)
  * outreach    → (candidate, role, channel)
"""

from __future__ import annotations

from itertools import count

from .client import Upserted


class FakeWebPy:
    def __init__(self) -> None:
        self._ids = count(1)
        self.candidates: dict[str, dict] = {}      # url -> row
        self.edges: dict[tuple, dict] = {}
        self.enrichments: dict[int, dict] = {}     # candidate -> row
        self.scores: dict[tuple, dict] = {}
        self.outreach: dict[tuple, dict] = {}
        self.targets: list[dict] = []
        self.runs: dict[int, dict] = {}

    def _new(self, **fields) -> dict:
        return {"id": next(self._ids), **fields}

    def upsert_candidate(self, **fields) -> Upserted:
        url = fields["linkedin_url"]
        if url in self.candidates:
            self.candidates[url].update(fields)
            return Upserted(self.candidates[url], created=False)
        row = self._new(**fields)
        self.candidates[url] = row
        return Upserted(row, created=True)

    def create_lead_edge(self, **fields) -> Upserted:
        key = (
            fields.get("role"), fields.get("to_candidate"), fields.get("kind"),
            fields.get("from_company"), fields.get("from_candidate"),
            fields.get("depth", 0),
        )
        if key in self.edges:
            return Upserted(self.edges[key], created=False)
        row = self._new(**fields)
        self.edges[key] = row
        return Upserted(row, created=True)

    def upsert_enrichment(self, **fields) -> Upserted:
        cand = fields["candidate"]
        created = cand not in self.enrichments
        self.enrichments[cand] = self._new(**fields) if created else {
            **self.enrichments[cand], **fields
        }
        return Upserted(self.enrichments[cand], created=created)

    def upsert_score(self, **fields) -> Upserted:
        key = (fields["candidate"], fields["role"])
        created = key not in self.scores
        self.scores[key] = self._new(**fields) if created else {
            **self.scores[key], **fields
        }
        return Upserted(self.scores[key], created=created)

    def create_outreach(self, **fields) -> Upserted:
        key = (fields["candidate"], fields["role"], fields["channel"])
        if key in self.outreach:
            return Upserted(self.outreach[key], created=False)
        row = self._new(status="draft", **fields)
        self.outreach[key] = row
        return Upserted(row, created=True)

    def create_target(self, **fields) -> Upserted:
        for t in self.targets:
            if t.get("name") == fields.get("name") and \
                    t.get("role") == fields.get("role"):
                return Upserted(t, created=False)
        row = self._new(**fields)
        self.targets.append(row)
        return Upserted(row, created=True)

    def finalize_run(self, run_id: int, **fields) -> Upserted:
        row = {**self.runs.get(run_id, {"id": run_id}), **fields}
        self.runs[run_id] = row
        return Upserted(row, created=run_id not in self.runs)

    # Cross-run state — in-memory tests run a single workflow, so there's nothing
    # already evaluated/scanned. (The Django client reads it from the DB.)
    def evaluated_candidate_ids(self, role_id: int) -> set[int]:
        return set()

    def scanned_companies(self, role_id: int) -> list[str]:
        return []

    def mark_company_scanned(self, target_id: int) -> None:
        pass
