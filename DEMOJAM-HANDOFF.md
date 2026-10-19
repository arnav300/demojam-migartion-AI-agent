# DemoJam handoff: Ansible Migration Copilot

**Start here in a new session.** This file is tracked in Git. The planning files (`docs/CLAUDE.md`, `docs/TASKS.md`, `docs/CLUSTER-SETUP.md` and `docs/demojam/`) are gitignored on purpose and stay local. This file summarises everything needed from them.

Last updated: 2026-10-19 · branch `demojam/migration-agent` · latest code commit `89e127e`

## Goal

Win DemoJam with the original idea: an **AI layer**, using IBM Granite served by vLLM on OpenShift AI, on top of the AAP 2.4 → 2.6 migration toolkit (`aap-migrate`). The story to land:

1. The pre-migration report **predicts** failures (RC-001 … RC-011), and AI explains them (C7).
2. The migration runs, with a live dashboard (C11).
3. **Predicted vs actual** shows the prediction came true. Failures are classified by rules, and AI explains them (C8).
4. AI drafts remediation playbooks that must pass validation, with a template fallback (C10). An executive summary is added to the reports (C9).
5. Stretch: config as code for one organization (T19).

**Message: rules decide, AI explains, nothing is applied automatically.**

## Team

- The user, plus one teammate who brings up the OpenShift cluster and Granite.
- There is no "Dev B". The AAP 2.4 source, AAP 2.6 target and seed data are handled by the user.
- `remediation/templates/` was handed to Claude.

## Hard rules (from the local `docs/CLAUDE.md`)

1. Commit only to `demojam/migration-agent`. Commit messages look like `T07: <what changed>`. Never force-push.
2. Rules decide the RC code. Granite only explains, summarises and drafts.
3. AI is optional. With `AI_ENDPOINT` unset, everything behaves as before.
4. Never write to the AAP 2.4 source.
5. Every model answer is parsed into a pydantic schema (`ai/schemas.py`). An invalid answer is retried once, then dropped. Raw model text is never rendered.
6. Generated playbooks must pass `--syntax-check` (and `ansible-lint` if installed), or the template is used.
7. No secrets in code, fixtures, logs or reports.
8. Ask before adding a dependency. `ansible-core` was approved.

Working loop: `git pull --rebase`, implement with unit tests, run `make lint test-unit`, show the diff, then get approval before committing (unless the user authorises autonomous commits).

## Where things are

| Path | What |
| --- | --- |
| `src/aap_migration/ai/` | `client.py` (with cache and RC check), `cache.py`, `schemas.py`, `prompts.py`, `explain.py` (C7), `rc_map.py` and `classify.py` (C8), `narrative.py` (C9), `remediate.py` (C10), `validation/` (an `ansible.controller` → `awx.awx` shim, used for syntax checks only) |
| `src/aap_migration/analysis/prediction_check.py` | Predicted vs actual (rules only) |
| `src/aap_migration/dashboard/` | C11 stdlib HTTP dashboard |
| `src/aap_migration/cac/` | T19 config-as-code export |
| `src/aap_migration/cli/commands/` | `classify_failures`, `remediate`, `dashboard`, `prediction_check` and `cac_export`; `--ai/--no-ai` on `premigration-report` and `enhanced-report` |
| `remediation/templates/RC-001…011.yml` | Hand-written fallback playbooks |
| `deploy/openshift/` | Kustomize base: BuildConfig, 3 suspended Jobs (pre-migration, migrate, post-migration), dashboard, PVC. See its README. |
| `tests/unit/{ai,cac,analysis,dashboard,deploy}/` | Unit tests. `tests/` is gitignored, so new test files need `git add -f`. |

Developer setup: Python 3.12 (the codespace default is 3.14). Run `pip install -r requirements-dev.txt && pip install -e ".[remediation]"`, then `ansible-galaxy collection install awx.awx==24.6.1`. The branch baseline was 160 ruff errors and 1 failing test (`test_host_memberships`), none of them ours.

## Confidence: what is verified and what is not

**Verified here:**
- Unit tests for all new code; the model is always stubbed with `httpx.MockTransport`.
- The AI-off pre-migration HTML is byte-identical to a golden file made before the changes.
- Real `ansible-playbook` 2.18.1 syntax checks of all 11 templates. The templates also ran against an unreachable controller: guards and templating work, and the run reaches the API call.
- `kubectl kustomize` renders the deploy base.
- The `classify-failures`, `remediate` and `dashboard` CLIs were run by hand on the fixtures.
- The `uv.lock` relock adds only `ansible-core` and `resolvelib`.
- The AI-on path end to end over real HTTP against a fake OpenAI-compatible server on localhost (2026-10-19). See "Local AI end-to-end check" at the end.

**Not verified yet (first real test is on the cluster):**
- A real Granite or vLLM endpoint: JSON compliance, latency, and whether its answers pass our schemas. In particular, `ReportNarrative` rejects any digits other than AAP versions and RC codes.
- Real AAP 2.4 and 2.6, and a real migration database:
  - that `premigration-report` `resource_type` names match `migration_progress.resource_type`;
  - that the RC mapping fits real error messages.
- The image build (`uv sync --extra remediation`, `ansible-galaxy` in the Containerfile) and running under OpenShift's random UID.
- `premigration-report` and `enhanced-report` through the CLI with AI on. Both need a raw export or a migration database. The pre-migration AI path was run through a script that calls the same helpers.
- `cac-export` on a real export, and applying its output with `infra.aap_configuration` (T19 step 4).
- Remediation playbooks against a live AAP 2.6. They must be run with `ansible.controller` from Automation Hub; `awx.awx` uses `/api/v2/`.
- Gateway (`ansible.platform`) drafts can't be syntax-checked without Automation Hub collections.

## Next steps

1. The teammate finishes the cluster and Granite. Then follow the runbook below: AI-off run, AI-on rehearsal, then the stage run.
2. Send any failing log to Claude. The likely spots are listed in the runbook under "Known risks".
3. Optional: a 5-minute demo script, and T19 steps 3–4. The local fake-server check is done.

---

# Runbook (copy of the local docs/demojam/DEMO-RUNBOOK.md)

## DemoJam runbook: action items and test plan

Branch `demojam/migration-agent` @ 89e127e or later. Namespace `demojam-copilot`. All `oc` commands assume `oc project demojam-copilot`.

### 0. Local tests (you, 15 min, no cluster needed)

```bash
git pull origin demojam/migration-agent
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt && pip install -e ".[remediation]"
ansible-galaxy collection install awx.awx==24.6.1      # enables the 12 real-Ansible tests
make lint test-unit
```

Expect:
- **Tests:** about 237 passed and 1 failed. The failure, `test_host_memberships…test_imports_host_into_group`, was already on the branch before our work.
- **Lint:** 160 errors, all pre-existing.

The real-Ansible tests are skipped if `awx.awx` isn't installed.

Optional smoke test against your real AAP 2.4. It's read-only; nothing is written anywhere.

```bash
cp .env.example .env        # set SOURCE__URL (…/api/v2), SOURCE__TOKEN, TARGET__URL (…/api/controller/v2), TARGET__TOKEN
aap-migrate premigration-report --source live --no-ai --format all
## check reports/premigration-report.html: all 11 RC codes should appear for the seeded estate
```

### 1. Cluster prerequisites (teammate)

1. Granite is served (`docs/CLUSTER-SETUP.md` step 5) and answers a chat call in under 10 s.
2. **Check that the copilot namespace can reach Granite.** Network policies often block this:
   ```bash
   oc run curl-test -n demojam-copilot --rm -it --restart=Never --image=registry.access.redhat.com/ubi9/ubi -- \
     curl -sk "$AI_URL/v1/models" -H "Authorization: Bearer $AI_TOKEN"
   ```
3. Write down whether the URL is `https` with a self-signed certificate. If it is, set `AI_VERIFY_SSL=false` in step 2.
4. Check that both AAP 2.4 and AAP 2.6 respond from the cluster (`/api/v2/ping/` and `/api/gateway/v1/ping/`) with the same `curl-test` pod.

### 2. First run with AI off (you)

```bash
oc new-project demojam-copilot      # or: oc project demojam-copilot
oc create secret generic copilot-secrets \
  --from-literal=AAP_SOURCE_URL='https://<aap24>/api/v2'            --from-literal=AAP_SOURCE_TOKEN='<token>' \
  --from-literal=AAP_TARGET_URL='https://<aap26>/api/controller/v2' --from-literal=AAP_TARGET_TOKEN='<token>'
oc apply -k deploy/openshift
oc start-build copilot --follow     # watch: uv sync --extra remediation, ansible-galaxy awx.awx
```

Run the three Jobs in order, one at a time:

```bash
for j in copilot-premigration copilot-migrate copilot-postmigration; do
  oc patch job $j --type=merge -p '{"spec":{"suspend":false}}'
  oc logs -f job/$j
  oc wait --for=condition=complete job/$j --timeout=6h || break
done
```

While `copilot-migrate` runs, open the dashboard:

```bash
oc rollout restart deployment/copilot-dashboard
echo "https://$(oc get route copilot-dashboard -o jsonpath='{.spec.host}')/"
```

Copy the results off the volume: `deploy/openshift/README.md` section 6. That gives you `copilot-reports/` with `reports/`, `logs/` and `remediation/`.

### 3. Check the AI-off run

| Check | Where | Expected |
| --- | --- | --- |
| Pre-migration report | `reports/premigration-report.html` | All 11 RC codes flagged on the seeded estate. No "AI explanation" boxes and no Summary panel. |
| AAP 2.4 untouched | object counts before and after (`…/api/v2/<type>/?page_size=1` → `count`) | Identical |
| Classification | `reports/classification.csv` | Every failed row has an RC code or `UNCLASSIFIED` |
| Predicted vs actual | `reports/prediction-check.json` and the panel in `enhanced-report.html` | Recall, precision and RC-agreement numbers. **This is the demo punchline: write the numbers down.** |
| Remediation | `remediation/README.md` | One row per RC code that failed, source `template` |
| Dashboard | Route | Source, target and migrated counts move during `migrate` |

### 4. Rehearsal with AI on (this also fills the answer cache)

Reset the target and the state, but **keep `ai_cache.json`**:

```bash
aap-migrate --config config/config.yaml cleanup --help     # then clean the AAP 2.6 target
## helper pod from deploy/openshift/README.md section 6, then:
oc exec copilot-files -- sh -c 'cd /app/state && rm -rf migration_state.db exports xformed schemas reports remediation logs'
oc delete pod copilot-files
```

Add the AI keys to the Secret, then rerun:

```bash
oc delete secret copilot-secrets
oc create secret generic copilot-secrets <the four AAP keys from step 2> \
  --from-literal=AI_ENDPOINT='<granite url>' --from-literal=AI_MODEL='<id from /v1/models>' \
  --from-literal=AI_TOKEN='<token>' --from-literal=AI_VERIFY_SSL='false'
oc delete job --all && oc apply -k deploy/openshift
## then the same Job loop as step 2
```

Check the AI-on run:
- **Pre-migration HTML:** an "AI explanation" box on each RC card under *Root causes*, and a **Summary** panel at the top. Its numbers come from our data; the model writes only the sentences.
- **Logs:** look for "AI explanations added for N root causes". "No AI explanations available" means TLS, network or model trouble: check `AI_VERIFY_SSL`, then the `curl-test` from step 1.
- **`classification.json`:** `"ai_used": true`, and groups have `ai_explanation`.
- **`remediation/README.md`:** some rows with source `generated` (drafts that passed the syntax check), the rest `template`.
- **`enhanced-report.html`:** both the Summary panel and the Predicted vs actual panel.
- **`/app/state/ai_cache.json`:** exists on the volume.

### 5. Stage run

1. Reset the target and the state as in step 4. Keep the cache and the AI Secret.
2. Run the same Jobs. Every AI answer whose prompt matches the rehearsal is replayed instantly. Only prompts that changed (different failures or counts) go to Granite live.
3. Fallback if the model is down: the same run with AI off still completes, so you can show the rule-based reports.

### 6. Optional extras

- **Config as code (T19):** after an export, run `aap-migrate cac-export --org <name> --export-dir <export dir> --out cac/`. It's untested against a real export.
- **Remediation playbooks:** run them with `ansible.controller` from Automation Hub, not `awx.awx`. Start in check mode: `ansible-playbook RC-0XX.yml -e @RC-0XX.vars.yml`.

### Known risks to watch

| Risk | What you'll see | Fix |
| --- | --- | --- |
| The image build fails at the new steps | `oc start-build` log stops at `uv sync --extra remediation` or `ansible-galaxy` | Send me the log |
| AI silently off | "No AI explanations available" in the logs | `AI_VERIFY_SSL=false`, a network policy, or a wrong `AI_ENDPOINT` (no `/v1` needed) |
| Dashboard can't mount the volume | `Multi-Attach` error | `deploy/openshift/README.md` section 7 |
| Fewer than 11 RC codes flagged | The pre-migration report | Seed data gap, or a rule mismatch: send me the JSON |
| Granite answers rejected | "ai_output_invalid_dropped" in the logs | Send me a log excerpt. The schemas may need loosening for this model |

---

# Progress log (copy of the local docs/demojam/PROGRESS.md)

## DemoJam progress

### Final summary — autonomous run 2026-10-19

**Done and pushed to `origin/demojam/migration-agent`, one commit per task. No force-push.**

| Task | Commit | What |
| --- | --- | --- |
| T07 | 1e41001 | `ai/client.py`: optional chat-completions client with schema validation, one retry, and `None` on any failure |
| T08 | 4aaebfe | `ai/schemas.py` and `ai/prompts.py` |
| T03 | 59922bf | `deploy/openshift/` kustomize base (renders with `kubectl kustomize`) |
| T09 | b72a872 | C7: AI explanations per RC section in `premigration-report` (`--ai/--no-ai`, `ai_cache.json`) |
| T10 | 6806772 | C8: `classify-failures`, using the rule table `ai/rc_map.py` with AI explanations per group |
| T11 | 2c72cd6 | C9: "Summary" panel in the enhanced and pre-migration HTML. Numbers are ours; the schema rejects numbers from the model |
| T12 | 5efaa86 | C10: `remediate`, with static checks, syntax-check and ansible-lint, falling back to a template |
| T13 | f491846 | C11: `dashboard` (stdlib `http.server`) plus `deploy/openshift/dashboard.yaml` |
| T14 | cff707c | Menu option 6 "After Migration", AI variables in `.env.example`, README section "DemoJam AI layer" |

**Skipped:** none. T19 was not started, as instructed (step 1 research is in `T19-step1-aap26-export-findings.md`).

**Checks for every task:**
- `make lint test-unit` was run with Python 3.12, plus the hard-rule self-review.
- The ruff error count stayed at the baseline of 160, all pre-existing, and every new file is lint-clean.
- Unit tests: 181 passed, plus the 1 pre-existing failure (`test_host_memberships.py::...test_imports_host_into_group`), which no change of mine touched.
- No new dependencies (`httpx`, `pydantic`, `pydantic-settings`, `PyYAML`, `SQLAlchemy` and the stdlib only).
- Model calls in tests use `httpx.MockTransport` only. Nothing writes to AAP. Placeholders only, no secrets.

**Hard-rule guarantees in code:**
- **AI optional:** with `AI_ENDPOINT` unset there are no calls, and the pre-migration HTML is byte-identical to a golden file made before the change.
- **The AAP 2.4 source is never written to:** the source is only read, and the target is only read too (dashboard GETs, never writes).
- **Validated output:** every model answer is parsed into `ai/schemas.py`.
- **The model never sets an RC code:** an answer that changes the RC code is dropped.
- **Escaping:** model text is always HTML-escaped.
- **Remediation output:** a generated playbook is written only after it passes `--syntax-check` (and `ansible-lint` when installed).

#### Every assumption, in brief (details under each task)

1. **New test files are force-added** with `git add -f`, because `.gitignore` ignores `tests/` while the existing tests are tracked.
2. **T07 endpoint and parsing:** the client appends `/v1/chat/completions` without doubling `/v1`. JSON wrapped in code fences is unwrapped. A response envelope without `choices` counts as invalid output. The token is a `SecretStr`.
3. **T08 schemas:** `FindingExplanation` also accepts `STALE`, and every text field must be non-empty.
4. **T03 ConfigMap and variables:**
   - kustomize can't read `../../config/config.yaml`, so the ConfigMap uses a copy, kept in sync by a unit test.
   - The Secret keys `AAP_*` are mapped to the variables the tool reads (`SOURCE__*` and `TARGET__*`).
5. **T03 Jobs and build:**
   - The Jobs are created suspended; the README unsuspends them.
   - The BuildConfig overrides the short-name base image.
   - All paths and logs go to the PVC, because OpenShift runs with a random UID.
6. **T09 one explanation per RC code:** the spec groups by `(rc, type)` but returns a result keyed by RC code, so each RC code gets its largest group's explanation.
7. **T09 cache, rendering and scope:** the cache is keyed by the sha256 of the prompt. The AI HTML is a separate script appended only when explanations exist. `--org` limits the prompt to that organization.
8. **T10 RC mapping:** an exact key, then a prefix, then a resource-type fallback.
   - RC-011 is users and teams, because there's no IAM error key.
   - RC-007 is the `ssh_key_unlock` key.
   - The Smart/Constructed source error maps to RC-010.
9. **T10 output and DB access:** only the normalised error key and the names are sent to the model, never the raw `error_message`. SQLite is opened with `mode=ro`.
10. **T11 numbers:** the narrative schema rejects digits other than AAP versions and RC codes.
11. **T11 scope:** only the enhanced report's HTML format gets the panel.
12. **T12 inputs:** UNCLASSIFIED has no playbook. Each RC code uses its most frequent error key.
13. **T12 validation:** static checks run first (localhost only, `check_mode` from a variable, `ansible.platform`, `ansible.controller` or `ansible.builtin` modules only). Without `ansible-playbook`, the template is used. A missing template is reported as `missing`.
14. **T13 dashboard data:**
    - The export folder name is used as the API endpoint name, and a 404 shows as `—`.
    - One 5 s cache covers the whole payload.
    - A `failed` column and `/healthz` were added.
15. **T13 dashboard deployment:** `--host` defaults to 127.0.0.1, and the Deployment passes 0.0.0.0.
16. **Command registration:** the new commands were registered in `cli/main.py` in T10, T12 and T13 so they could be tested; T14 added the menu.

#### Left open

- **Ansible tools:** `ansible-playbook` and `ansible-lint` aren't installed in the codespace or the container image, so AI-drafted playbooks can only be accepted where `ansible-core` exists.
- **Remediation templates:** `remediation/templates/RC-0XX.yml` doesn't exist yet (Dev B). Until then, `remediate` reports `missing` for any RC code without a valid draft.
- **Cluster checks (Dev A):** confirm that the Jobs run, that the GitHub repo is public for the BuildConfig, and whether RWX storage is available. With RWO, the dashboard must share a node with the Job.
- **Pre-existing problems:** 160 lint errors and 1 failing unit test were on the branch before this run.

#### How a teammate tries each new command

One-time setup with Python 3.12 (the codespace default `python3` is 3.14):

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt && pip install -e .
make lint test-unit          # expect 160 pre-existing lint errors and 1 pre-existing test failure
```

With AI off (no `AI_ENDPOINT`), each command behaves exactly as before:

```bash
unset AI_ENDPOINT AI_MODEL AI_TOKEN
aap-migrate --config config/config.yaml premigration-report --no-ai
aap-migrate classify-failures --db tests/unit/ai/fixtures/failures.db --output-dir /tmp/demo --no-ai
aap-migrate remediate --from /tmp/demo/classification.json --out /tmp/demo/remediation --no-ai
aap-migrate --config config/config.yaml enhanced-report --no-ai
aap-migrate dashboard --db tests/unit/ai/fixtures/failures.db --port 8080      # then open http://127.0.0.1:8080/
```

With AI on (Granite endpoint from `docs/CLUSTER-SETUP.md` step 5; values from the cluster, never committed):

```bash
export AI_ENDPOINT='<granite-url>' AI_MODEL='<model-id>' AI_TOKEN='<token>'
aap-migrate --config config/config.yaml premigration-report --ai            # + AI explanations and Summary panel
aap-migrate classify-failures --db tests/unit/ai/fixtures/failures.db --output-dir /tmp/demo --ai
aap-migrate remediate --from /tmp/demo/classification.json --out /tmp/demo/remediation --ai   # needs ansible-core for drafts to be accepted
aap-migrate --config config/config.yaml enhanced-report --ai                # Summary panel at the top
aap-migrate --config config/config.yaml dashboard --port 8080               # no AI; target counts need --config
```

On OpenShift, follow `deploy/openshift/README.md`. Leave the `AI_*` keys out of `copilot-secrets` for an AI-off run.

---


### Baseline on demojam/migration-agent @ 91a04be (before any task)

These failures already exist on the branch. Each task below was checked against them: the ruff error count must not go up, new files must be lint-clean, and no new tests may fail. The checks ran in a Python 3.12 virtual environment with `requirements-dev.txt` and `pip install -e .`, using `make lint test-unit PYTHON=<venv>/bin/python`.

- **Lint:** 160 ruff errors in existing files.
- **Unit tests:** one failure, `tests/unit/test_host_memberships.py::TestHostGroupMembershipImporter::test_imports_host_into_group`. It expects `created` but gets `failed`, with `host_not_in_group_inventory`.
- **Python:** the default `python3` in the codespace is 3.14, but the project needs 3.12.
- **Gitignore:** `.gitignore` ignores `tests/`, but the existing test files are tracked. New test files are therefore added with `git add -f`.

---

### T07 — AI client (commit 1e41001)

- **Files:** `src/aap_migration/ai/__init__.py`, `src/aap_migration/ai/client.py`, `tests/unit/ai/test_client.py`.
- **Tests (13):**
  - AI disabled when the endpoint is unset or blank;
  - environment defaults and URL building;
  - no HTTP call when disabled;
  - valid JSON, including the request body, headers and the schema in the system prompt;
  - no auth header without a token;
  - JSON wrapped in code fences is accepted;
  - invalid then valid (one retry, with the error included);
  - invalid twice returns None;
  - timeout returns None;
  - HTTP 500 returns None;
  - a malformed response envelope counts as invalid output;
  - the token, prompt and model output never appear in logs above DEBUG.
- **Assumptions:**
  - `AI_ENDPOINT` may or may not already end in `/v1`. The client appends `/v1/chat/completions` without doubling `/v1`.
  - JSON wrapped in code fences is unwrapped before parsing.
  - A response envelope with no `choices` is treated as invalid model output, so it gets one retry.
  - `AI_TOKEN` is stored as a pydantic `SecretStr`, so it never shows up in a `repr`.
- **Open:** nothing.

### T08 — Schemas and prompts (commit see `git log --grep T08`)

- **Files:** `src/aap_migration/ai/schemas.py`, `src/aap_migration/ai/prompts.py`, `tests/unit/ai/test_schemas_prompts.py`.
- **Tests (44 parametrised):**
  - every schema accepts a valid payload;
  - every field is required;
  - out-of-range values are rejected: wrong RC code, bad confidence, more than 2 sentences in `summary`, more than 120 words, empty strings, a non-list `assumptions`;
  - boundary values are accepted;
  - each prompt builder includes the RC code, title and the facts it was given, caps samples at 5, and never leaks another group's names;
  - the narrative prompt contains only the aggregates it was given;
  - the system prompts state the rules.
- **Assumptions:**
  - `FindingExplanation.rc_code` also accepts `STALE`. The analyzer emits `STALE` findings next to RC-001…RC-011 (`RC_CATALOG["STALE"]`), and C7 has to explain them too.
  - `FailureClassification.rc_code` accepts `RC-001`…`RC-011` or `UNCLASSIFIED`. `RemediationPlaybook.rc_code` accepts `RC-001`…`RC-011` only.
  - "≤ 2 sentences" is checked by splitting on `.`, `!` or `?` followed by whitespace or the end of the text.
  - Every text field must be non-empty.
  - Prompt builders take names and counts only, and pull the RC title, description and remediation text from `RC_CATALOG`.
  - The narrative prompt tells the model not to state numbers, because the report shows our own numbers.
- **Open:** nothing.

### T03 — OpenShift manifests (commit 59922bf)

- **Files:**
  - `deploy/openshift/`: `namespace.yaml`, `secret.example.yaml`, `pvc.yaml`, `imagestream.yaml`, `buildconfig.yaml`, `job-migrate.yaml`, `job-premigration.yaml`, `kustomization.yaml`, `config.yaml` and `README.md`;
  - `.gitignore`: added `deploy/openshift/secret.yaml`;
  - `tests/unit/deploy/test_openshift_manifests.py`.
- **Tests (7):**
  - the ConfigMap source matches `config/config.yaml`;
  - the kustomization lists every manifest except the example Secret;
  - the example Secret has placeholders only, with exactly the seven keys;
  - `secret.yaml` is gitignored;
  - each Job has `backoffLimit: 0`, resource requests and limits, the PVC at `/app/state`, the ConfigMap, all Secret keys, `AI_*` marked optional and the AAP keys required;
  - the BuildConfig passes the branch zip.
- **Checks:** `kubectl kustomize deploy/openshift` (kustomize v5.8.1) renders without errors. `oc` isn't installed here.
- **Assumptions:**
  - **Config copy:** kustomize refuses files outside the base, and symlinks too (tested). So `configMapGenerator` reads a copy at `deploy/openshift/config.yaml`, and a unit test fails if the copy drifts from `config/config.yaml`.
  - **Variable mapping:** the Secret keys follow the spec (`AAP_SOURCE_URL`, …), but the tool reads `SOURCE__URL`, `SOURCE__TOKEN`, `TARGET__URL` and `TARGET__TOKEN`. The Jobs map one to the other with `secretKeyRef`.
  - **Paths:** `MIGRATION_STATE_DB_PATH` and `PATHS__*` point at the PVC under `/app/state`. Logs go to `/app/state/logs/` via `--log-file`, because OpenShift's random UID can't write to `/app/aap-bridge/logs`. `HOME` is set to `/tmp`.
  - **Full-run command:** the README's full run is `aap-migrate --config … migrate` with no subcommand. The pre-migration Job uses `premigration-report --source live`, so it doesn't need an export on the PVC first.
  - **Suspended Jobs:** the Jobs are created with `suspend: true`. `oc apply -k` would otherwise start them before `oc start-build` has produced `copilot:latest`. The README unsuspends them with `oc patch`.
  - **Base image:** the BuildConfig overrides the Containerfile's short-name `FROM ubi9/ubi` with `registry.access.redhat.com/ubi9/ubi:latest`.
  - **Image name:** the ImageStream has `lookupPolicy.local: true`, so the Jobs can use `copilot:latest`.
  - **Resources:** requests and limits are 500m/1Gi and 2/4Gi, which fits `demojam-quota`.
- **Open:**
  - Dev A still has to confirm the Jobs run on the cluster.
  - The BuildConfig assumes the GitHub repo is public. If it's private, it needs a source secret.
  - The image installs with `uv sync` from `uv.lock`; that hasn't been verified in the cluster.
  - Both Jobs share an RWO PVC, so run one at a time.

### T09 — C7 explanations for pre-migration findings (commit b72a872)

- **Files:**
  - `src/aap_migration/ai/explain.py` (new);
  - `cli/commands/premigration_report.py`: `--ai/--no-ai` and `_add_ai_explanations()`;
  - `reporting/premigration_html.py`: optional AI script;
  - `tests/unit/ai/test_explain.py`;
  - fixtures `tests/unit/ai/fixtures/premigration_inventory.json`, `make_golden.py` and `premigration_golden.html`.
- **Tests (11):**
  - grouping;
  - one call per group, with names only and only the listed fact keys;
  - samples capped at 5;
  - a cache hit avoids the call;
  - an answer that changes the RC code is dropped and not cached;
  - HTTP errors are skipped;
  - AI off makes no call;
  - **the AI-off HTML is byte-identical to the golden file**, which was generated from the renderer *before* this change;
  - the AI-on HTML adds explanations and escapes the embedded model text;
  - the command helper makes no change and writes no cache when AI is off (for `--ai`, `--no-ai` and the default);
  - `--no-ai` makes no call;
  - `--org` sends only that organization's findings to the model.
- **Assumptions:**
  - **One explanation per RC code:** the spec groups by `(rc_code, resource_type)` but returns a result keyed by RC code. `explain_findings` keeps the explanation of the largest group for each RC code, with ties going to the alphabetically first resource type. `explain_groups()` also exposes every group.
  - **STALE findings** are explained too, as their own section.
  - **Cache:** the key is the sha256 of `system + user`. Only validated answers are cached, and entries are re-validated when read. The cache lives at `<output-dir>/ai_cache.json`.
  - **Sample names** are sorted before being capped, so prompts (and cache hits) are stable.
  - **HTML:** the rule-based page is unchanged. When `ai_explanations` is present, a separate script is appended that adds an "AI explanation" box to each RC card, with every model string escaped through `esc()`.
  - **JSON:** `ai_explanations` is added to the JSON report only when AI is on and produced something.
  - **`--ai` without `AI_ENDPOINT`:** prints a warning and continues without AI.
  - **`--org`:** only that organization's findings reach the model.
- **Open:**
  - `cli/commands/premigration_report.py:195` has a pre-existing ruff F541; I left it untouched.

### T10 — C8 post-run root-cause classification (commit 6806772)

- **Files:**
  - `src/aap_migration/ai/rc_map.py` and `src/aap_migration/ai/classify.py` (new);
  - `src/aap_migration/cli/commands/classify_failures.py` (new);
  - `cli/main.py`: registers `classify-failures`; ruff's import sorting also reordered three existing imports;
  - tests: `tests/unit/ai/test_classify.py`, `tests/unit/ai/conftest.py`;
  - fixtures: `tests/unit/ai/fixtures/failures.db` and `make_failures_db.py`, which regenerates it.
- **Tests (17):**
  - the `rc_for_error` table, exact keys and prefixes, the resource-type fallback, and unknown keys;
  - the table only maps to known codes;
  - only failed rows that have a message are loaded;
  - **the state DB file is unchanged after classification**;
  - with AI off, every RC code plus one UNCLASSIFIED is counted once, with no explanations;
  - with AI on, one call per `(rc_code, error_key)` group, and only the normalised key and sample names are sent, never the raw `error_message`;
  - the model can't change an RC code; a mismatched answer is dropped;
  - `--no-ai` makes no call;
  - the CLI writes `classification.json` and `.csv`;
  - the CLI exits 2 when there's neither `--db` nor `--config`.
- **Assumptions:**
  - **Mapping order:** an exact key in `ERROR_KEY_TO_RC`, then a prefix table for keys with dynamic parts (`variables_needed_to_start (…)`, `Duplicate <type>`, `Validation: …`, `Field required (…)`), then a resource-type fallback.
  - **RC-011:** `normalize_error_key()` has no IAM label, so RC-011 comes from that resource-type fallback: users and teams.
  - **RC-007:** `normalize_error_key()` has no credential-secret label, so RC-007 maps the one credential-secret key that exists, `API: ssh_key_unlock set when key not encrypted`.
  - **Smart/Constructed inventory source error:** maps to RC-010, the deprecated-feature code, because RC-010's remediation covers converting Smart inventories.
  - **Read-only DB:** `--db` accepts a SQLAlchemy URL or a SQLite file path. SQLite is opened with `mode=ro`.
  - **Registration:** I registered the command in `cli/main.py` now so it can be used and tested. The interactive menu entry comes in T14.
  - **Outputs:** `classification.json` has `generated`, `ai_used`, `total_failed`, `per_rc` (all 11 codes plus UNCLASSIFIED), `groups` (rc_code, error_key, count, resource_types, sample_names, ai_explanation, ai_confidence) and `resources`. The CSV has one row per failed resource.
  - **Logging in tests:** CLI tests call `configure_logging()`, so the new `conftest.py` resets structlog between AI tests.
- **Open:** nothing.

### T11 — C9 executive narrative (commit 2c72cd6)

- **Files:**
  - `src/aap_migration/ai/narrative.py` (new);
  - `ai/schemas.py`: `ReportNarrative` now rejects numbers;
  - `ai/prompts.py`: the narrative prompt wording;
  - `cli/commands/migration_report_v2.py`: `--ai/--no-ai`, `_ai_summary_panel()`, and a `summary_html` slot in `_generate_enhanced_html`;
  - `cli/commands/premigration_report.py`: `resolve_ai()` and `_add_ai_summary()`;
  - `reporting/premigration_html.py`: the panel goes after the masthead when `ai_narrative` is present;
  - tests: `tests/unit/ai/test_narrative.py`, plus updated helper tests in `test_explain.py`.
- **Tests (16):**
  - pre-migration aggregates and enhanced aggregates: totals, per-RC counts and top failing organizations;
  - AI off returns None;
  - **the prompt equals the aggregates exactly**, with no resource names;
  - a model answer containing numbers is rejected after one retry;
  - AAP versions and RC codes are allowed;
  - other numbers are rejected;
  - the panel's numbers come from the aggregates, and model text is escaped;
  - the pre-migration HTML is still byte-identical to the golden file with AI off;
  - the panel sits right after the masthead with AI on;
  - **the enhanced HTML with the panel removed is byte-identical to the AI-off HTML**;
  - the enhanced helper returns "" with AI off;
  - `resolve_ai` covers the default, `--ai` and `--no-ai`.
- **Assumptions:**
  - **No model numbers:** `ReportNarrative` fails validation if a field contains digits other than AAP versions (`2.4`, `2.6`, `2.x`) or RC codes. That makes "numbers never come from the model" a schema guarantee, not only a prompt instruction. A failing answer is retried once, then the panel is left out.
  - **Aggregates:** `objects`, `organizations`, outcome or status totals, the success percentage (pre-migration), per-type results, per-RC counts and the top 5 failing organizations.
    - Pre-migration RC counts come from `resources_by_rc`, including STALE.
    - Post-migration RC counts come from `rc_for_error()` on failed rows (the T10 table).
  - **Panel layout:** a headline from the model, then three rows. Each row is a label, one line of our own numbers, and one model sentence.
    - Pre-migration labels: "Ready to migrate", "Predicted to fail", "What to do next".
    - Enhanced labels: "What migrated", "What failed", "What to do next".
  - **Enhanced report:** only the HTML format gets the panel; Markdown and CSV are unchanged.
  - **`--org`:** the aggregates cover only the filtered organization or organizations.
  - **JSON:** the pre-migration JSON carries `ai_narrative` only when AI produced one.
- **Open:** nothing.

### T12 — C10 remediation playbooks (commit 5efaa86)

- **Files:**
  - `src/aap_migration/ai/remediate.py` (new);
  - `src/aap_migration/cli/commands/remediate.py` (new);
  - `cli/main.py`: registers `remediate`;
  - `tests/unit/ai/test_remediate.py`.
- **Tests (17):**
  - one input per RC code, with UNCLASSIFIED excluded, the most frequent error key, and merged sorted sample names;
  - **generated and valid**: static checks, syntax-check and lint pass, so the playbook and vars are written and the README says "generated";
  - **generated but invalid**: a syntax-check failure falls back to the template;
  - a lint failure falls back to the template;
  - ansible-lint not installed: lint is skipped and the draft is still accepted;
  - ansible-playbook not installed: the template is used;
  - static checks reject `awx.awx` modules, hosts other than localhost, a missing `check_mode` variable, and invalid YAML, all before any tool runs;
  - blocks are accepted;
  - the model changing the RC code means the template is used;
  - **AI off**: templates are used and no tool runs;
  - `--no-ai` makes no call;
  - a missing template is reported as `missing`;
  - the CLI works end to end with AI off.
- **Assumptions:**
  - **Static checks** run before the tools: every play targets `localhost`, `check_mode` comes from `remediation_check_mode`, and every task module comes from `ansible.platform.`, `ansible.controller.` or `ansible.builtin.` (builtin is allowed for debug, assert and similar).
  - **Vars file:** each playbook gets `RC-0XX.vars.yml` with `remediation_check_mode: true` (the "generated vars" that default to check mode) and `remediation_targets`, which holds at most 5 names. The AAP connection comes only from environment variables.
  - **No `ansible-playbook`:** rule 6 requires a passing syntax-check, so a draft that can't be checked is not written, and the template is used instead. `ansible-lint` alone is optional, as the spec allows.
  - **Tool commands:** the checks run in a temporary directory: `ansible-playbook --syntax-check -i localhost, -e @vars <pb>`, then `ansible-lint --offline --nocolor <pb>`, each with a 120 s timeout. No playbook is ever executed.
  - **Missing templates:** `remediation/templates/` doesn't exist yet (Dev B's folder, and I didn't edit it). A missing template is reported as source `missing` in `README.md`, and no playbook is written for that RC code.
  - **Prompt input:** one model call per RC code, using its most frequent error key and up to 5 sample names drawn from all of that RC code's groups.
- **Open:**
  - `remediation/templates/RC-001.yml` … `RC-011.yml` need to exist (Dev B). Until then, with AI off or an invalid draft, everything is reported as `missing`.
  - Neither `ansible-playbook` nor `ansible-lint` is installed in this codespace or in the container image. With AI on, a generated draft can only be accepted where `ansible-core` is installed.

### T13 — C11 live dashboard (commit f491846)

- **Files:**
  - `src/aap_migration/dashboard/__init__.py`, `counts.py` and `server.py` (new);
  - `src/aap_migration/cli/commands/dashboard.py` (new);
  - `cli/main.py`: registers `dashboard`;
  - `ai/classify.py`: `_database_url` was renamed to the public `read_only_database_url`, so the dashboard can reuse it;
  - `deploy/openshift/dashboard.yaml`: Deployment, Service and Route;
  - `deploy/openshift/kustomization.yaml` and `deploy/openshift/README.md`: new section 7. I also fixed the test filename in the kustomization comment.
  - `tests/unit/dashboard/test_dashboard.py`.
- **Tests (9):**
  - source counts from the export directory, which skips broken files and top-level files;
  - state counts by status, and the DB file is unchanged;
  - target counts: GET only, `page_size=1`, a bearer header, a 404 becomes `null` (an export-only type), and a 500 or timeout becomes `null`;
  - the provider combines the three sources and **caches for 5 s** (fake clock);
  - the provider works without a target;
  - a real `ThreadingHTTPServer` on port 0 serves `/`, `/api/counts`, `/healthz`, and a 404;
  - the OpenShift manifest: a read-only PVC mount and claim, `--host 0.0.0.0`, resources set, and the Route points at the Service.
  - I also ran the CLI by hand and checked `/api/counts` with curl.
- **Assumptions:**
  - **Counts:**
    - **source:** the objects in `<export>/<type>/*.json`;
    - **target:** the API's `count` from `GET <TARGET__URL>/<type>/?page_size=1`, where the export folder name is used as the endpoint name;
    - **migrated:** `completed` rows in `migration_progress`.
  - **Failed count:** I also show `failed` from the state DB. It isn't in the spec, but it's useful in the demo.
  - **Caching:** one cache for the whole `/api/counts` payload, with a 5 s TTL. This covers the "target cached 5 s" requirement and also stops the page re-reading the export on every request.
  - **CLI options:** `--host` defaults to `127.0.0.1` locally; the Deployment passes `0.0.0.0`. `--export-dir` and `--db` override the config. Without `--config`, target counts show as `—`.
  - **`/healthz`:** added for the readiness and liveness probes.
  - **Logs:** the Deployment writes logs to `/tmp/dashboard.log`, because the PVC is mounted read-only.
  - **Node placement:** the PVC is RWO, so the Deployment prefers to run on the same node as a copilot Job pod (`podAffinity`). The README explains what to do about a `Multi-Attach` error.
- **Open:**
  - With an RWO PVC, the dashboard and a Job on different nodes can't share it. An RWX storage class would remove the problem; Dev A should check on the cluster.
  - If the dashboard starts before `copilot:latest` exists, it needs `oc rollout restart` (documented in the README).

### T14 — Wiring and docs (commit cff707c)

- **Files:**
  - `cli/menu.py`: new main-menu option **6. After Migration**, which opens a submenu (enhanced report, classify failures, remediation playbooks, live dashboard) and shows whether AI is on or off;
  - `.env.example`: `AI_ENDPOINT=`, `AI_MODEL=`, `AI_TOKEN=` (empty) and `AI_TIMEOUT_SECONDS=30`;
  - `README.md`: new "DemoJam AI layer" section, placed before "Performance Targets";
  - `tests/unit/ai/test_wiring.py`.
- **Tests (9):**
  - all new commands are registered;
  - each command has its `--ai` or `--port` option;
  - `.env.example` has the AI variables with empty values;
  - main-menu option 6 opens the submenu;
  - the submenu runs the exact commands.
- **Assumptions:**
  - The new commands were already registered in `cli/main.py` as part of T10, T12 and T13, so they could be tested there. T14 adds the menu and the docs.
  - The pre-migration submenu is unchanged: `premigration-report` already defaults to AI on when `AI_ENDPOINT` is set.
- **Open:** nothing.

---

## Follow-up run 2026-10-19: "win DemoJam" priorities (approved by the user: "Yes, do all of it")

### T19 step 2 — config as code (commit 5e015f3)

- **Command:** `aap-migrate cac-export --org <name> [--export-dir] [--out cac/]` writes `infra.aap_configuration` var files for ONE organization.
- **Variable names:** checked against collection release 4.10.0.
- **Content rules:** references use names, never IDs; secrets become `{{ vault_* }}` placeholders; Jinja text in the data is marked `!unsafe`.
- **Tests:** 14, in `tests/unit/cac/test_exporter.py`.
- **Open:** never applied to AAP 2.6 (step 4). Steps 3 (Granite `ADVICE.md`) and 4 (apply, then `aap-migrate validate`) are not started.

### AI answer cache for the live demo (commit 00100b7, ID T07)

- **Files:** new `ai/cache.py`; `AIClient(cache_path=…)`; `AI_CACHE_FILE` (in `.env.example`, the README, and both Jobs as `/app/state/ai_cache.json`).
- **Behaviour:** every validated AI answer (C7, C8, C9 and C10) is cached under a key built from the model, schema, system prompt and user prompt. One rehearsal fills the cache, and the demo replays it instantly.
- **RC code rule:** now enforced inside the client with `check=same_rc_code(...)`. An answer that changes the RC code gets one corrective retry that names the expected code. It is never cached.
- **Tests:** client tests for replay, the model being part of the key, `AI_CACHE_FILE` taking precedence, and the check retry with nothing cached. The T09 cache tests are updated.

### Predicted vs actual (commit c48a491, ID T11)

- **What:** `analysis/prediction_check.py`, the `aap-migrate prediction-check --prediction premigration-report.json [--db]` command, `enhanced-report --prediction …` (a panel under the header), and menu option 6 → 5.
- **Rules only, no AI.** Each resource is matched on `(resource_type, source_id)` and put in one category: confirmed, missed, false alarm or not migrated.
- **Metrics:** recall, precision and RC agreement. The actual RC code comes from the same rules as `classify-failures`.
- **Tests:** 9, in `tests/unit/analysis/test_prediction_check.py`.
- **Assumption:** pre-migration `resource_type` names match `migration_progress.resource_type`; both use API endpoint names. STALE findings don't count as predicted failures. Predicted failures are the resources with outcome `will_fail` or `at_risk`.

### ansible-core in the image (commit 3d2c1e9, ID T12; dependency approved by the user)

- **Package:** `pyproject.toml` has a new extra, `remediation = ["ansible-core==2.18.1"]`. `uv.lock` was relocked with uv 0.8.4, keeping format revision 3; only `ansible-core` and `resolvelib` were added.
- **Image:** the Containerfile now runs `uv sync --extra remediation` and installs `awx.awx==24.6.1` into `/app/aap-bridge/collections` (`ANSIBLE_COLLECTIONS_PATH`).
- **Finding:** `ansible.controller` and `ansible.platform` are **not on public Galaxy** (Automation Hub only). `--syntax-check` fails on modules it can't resolve.
- **Fix: a validation shim.** `src/aap_migration/ai/validation/ansible_collections/ansible/controller/meta/runtime.yml` routes `ansible.controller.*` to `awx.awx.*` (the same modules). It is searched **last**, so a real `ansible.controller` always takes precedence.
- **Verified:** a real `ansible-playbook` 2.18.1 run against `awx.awx` accepts a valid draft and rejects an unknown module. The test is skipped where the tools are missing.
- **Open:**
  - Drafts that use `ansible.platform` modules (mostly RC-011, Gateway identity) still fall back to the template, unless Automation Hub collections are added to the image. That would need a Hub token as a build secret (Dev A).
  - `ansible-lint` is not in the image; lint is skipped with a warning, as the spec allows.
  - The image build itself is not verified until `oc start-build` runs.

### Remediation templates (commit 800a621, ID T12; the user handed `remediation/templates/` to Claude)

- **Files:** `remediation/templates/RC-001.yml` … `RC-011.yml`. The generated `README.md` now says to run them with `ansible.controller` from Automation Hub; the image's `awx.awx` calls `/api/v2/`, which is only good for syntax checks.
- **Three kinds:**
  - **Fix on 2.6:** RC-001 (sync projects), RC-004 (enable prompt-on-launch flags on the job templates), RC-009 (recreate instance groups as container groups).
  - **Create from operator-filled values, guarded by an assert on `CHANGE_ME`:** RC-002 (job templates with an inventory, or with prompt-on-launch), RC-003 (schedules whose `extra_data` answers the required survey questions), RC-006 (inventory sources with corrected values).
  - **Guide only, no changes:** RC-005, RC-007, RC-008, RC-010, RC-011.
- **Why they create rather than fix:** a failed object never reached 2.6, so it can't be edited there. Each template ends by pointing to `aap-migrate retry failed --resource-type …`.
- **Verified:**
  - all 11 pass our static checks and a real `ansible-playbook --syntax-check` (ansible-core 2.18.1, awx.awx 24.6.1 and the shim);
  - with placeholder values, every fill-in template stops at its assert with a clear message;
  - with values filled in, every template gets past its assert and reaches the API (connection refused on the test port, as intended);
  - the guide templates run to completion.
- **Tests:** 25 new tests in `tests/unit/ai/test_remediate.py`:
  - a template exists for every RC code;
  - every template passes the static checks;
  - every template passes a real syntax check (skipped without the tools; all 11 passed here with them);
  - the real templates are copied end to end.
- **Open:**
  - None of the templates has run against a live AAP 2.6 yet.
  - RC-011 needs `ansible.platform` (Automation Hub) for any automated Gateway fix.

### Local AI end-to-end check (2026-10-19)

- **Setup:** a fake OpenAI-compatible chat server on `127.0.0.1` read the requested schema from the system prompt and returned a valid answer. The real `aap-migrate` CLI ran against it with `AI_ENDPOINT`, `AI_MODEL`, `AI_TOKEN` and `AI_CACHE_FILE` set, on the `failures.db` and `premigration_inventory.json` fixtures. The script stays local and is not committed.
- **`classify-failures`:** AI explanations for all 12 groups. The `Authorization` header was sent on every call.
- **`remediate`:** 11 of 11 drafts had source `generated`, so each passed the static checks and a real `ansible-playbook --syntax-check`.
- **Cache replay:** with the server stopped, the same run gave the same output entirely from `ai_cache.json`. This is the stage-run fallback.
- **Invalid answers** (`rc_code` RC-999): 0 of 12 groups explained and 11 of 11 playbooks from `template`. 46 calls means 23 prompts with one retry each. Nothing was cached.
- **Pre-migration with AI:** explanations for RC-001, RC-002 and STALE, and the Summary panel rendered in the HTML.
- **Secrets:** the token string appears in neither the HTML nor the cache file.
- **Not covered:**
  - real-model behaviour (Granite);
  - the CLI wrappers of `premigration-report` and `enhanced-report`, which need an export or a migration database.
