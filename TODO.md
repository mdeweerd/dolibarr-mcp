# TODO — steps currently in progress

Only active work lives here. Update after each completed step; purge when done (completed work is
recorded in git history and CHANGELOG.md).

## In progress

- [ ] Commit `SPECIFICATION.md`, `PLAN.md` and `TODO.md` to the repository (this change).
- [ ] Keep `backups/` copies of the three planning docs in sync when they are edited.
- [ ] Issue #37 sub-issues on branch `feat/issue-37`, one dedicated agent + one commit each, test-first
  (integration test red, then fix, then green). Done: 5, 8, 12, 13, 2, 1, 3, 4, 6, 7. Remaining: 9 (agent
  running), 10 (fichinter), 11 (agenda events).
- [ ] Every sub-issue agent loads the `ponytail` skill first (dedicated first turn), then receives the
  task on the next turn so the skill stays cached. Standing consigne in
  `.agents/skills/dolibarr-mcp-dev/docs.md` and `~/.agents/AGENTS.md`.
- [ ] Every sub-issue agent PUSHES `feat/issue-37` after committing and verifies the GitHub `integration`
  run for its commit (gh run list/watch); on CI failure caused by its change, it fixes within its own
  commit and re-pushes. Consigne in the skill docs.md.
- [ ] Done: local PHP+Docker integration instance documented in the project skill (`test.md`) and shared
  across agents (one instance, fixed env vars) — commit 91f000d.
- [ ] Resolve the failing GitHub `integration` runs on `feat/issue-37` (runs 45912087672 and 45915075361:
  `/socialcontributions` returns 501 on the pinned Dolibarr 21.0.0) with a dedicated agent BEFORE
  treating CI-green as a completion condition. Done for sub-issue 3 tests (commit 654872c); STILL OPEN:
  test_issue37_07_document_moduleparts.py::test_tax_social_charge\_\* also hits /socialcontributions and
  fails on 21.0.0 (runs 45917159343, 45918300539) — a dedicated agent must add the same skip guard INSIDE
  the causing commit 2599928 (rebase + amend + force-push).
- [ ] Every commit must be green on its own: fix each problem in the commit that introduced it.
- [ ] GitHub integration workflow green is a completion condition for every sub-issue task.
- [ ] `.github/workflows/integration.yml`: run the matrix on two Dolibarr versions (21.0.0 and 24.0.4)
  with a per-version cache key, one PHP install pinned to 8.3 (max of the smallest version, also
  supported by 24.0.4), both versions on the same runner when possible. GitHub-side only.
- [x] Matrix CI: DONE — integration.yml runs 21.0.0 + 24.0.4, per-version cache keys, PHP 8.3 pin, run
  45921870201 green on both legs. Finding: `/socialcontributions` exists only on upstream `develop`, in
  NO release ≤ 24.0.4, so the 4 social-contribution tests SKIP (not fail) on both legs; they activate
  automatically once a release carrying the endpoint is pinned. No separate 24.0.4 verification agent
  needed for released versions.
- [ ] When all sub-issues are done: refresh the local skill and `SPECIFICATION.md`, then commit
  `.gitignore`, `SPECIFICATION.md`, `.agents/` (and the planning docs) and push `feat/issue-37`.
- [x] MCP tools for supplier recurring invoice templates (`llx_facture_fourn_rec` /
  `llx_facture_fourn_det_rec`, classes `FactureFournisseurRec` / `FactureFournisseurLigneRec`): list per
  supplier, read, and update (template title/label, `ref_supplier`, line unit prices and quantities).
  Dolibarr 21 exposes no REST endpoint for these objects (only customer templates via
  `/invoices/templates`); each generated draft copies title/label, `ref_supplier` and line
  prices/quantities from the template, and the template is editable only via the UI
  (`fourn/facture/card-rec.php`). Use case: annual rent revision (ILAT, 1 December) — the December draft
  is generated with the pre-revision amount and the template cannot be updated via the API. DONE (FR-8):
  tools call the would-be `supplierinvoices/templates` route and degrade with an actionable error where
  the endpoint is absent; no DB layer added.

## Notes

- Anything not yet actionable goes to the GitHub issue tracker, not here.
- Reference FR numbers from SPECIFICATION.md when picking up work.
