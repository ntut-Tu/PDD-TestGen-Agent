# PDD test-agent

Python service that generates JUnit test cases for pull requests (story US-002). It reads
pull request data only through the git-gateway internal API, assembles a size-bounded prompt,
asks Gemini for JUnit files and checks every generated file with a Java parser.
Only Java repositories built with Maven or Gradle are supported.

## Run locally

```bash
cd agent
uv sync
# write my-values.yaml with the required values (see Configuration)
uv run pdd-agent --config my-values.yaml        # or: uv run python -m pdd_agent --config ...
uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pytest -q --cov
```

Container: `docker build -t pdd/test-agent:latest agent`. The image runs as a non-root user,
listens on `0.0.0.0:8000`, keeps its database in the `/data` volume and starts with
`--config /etc/pdd/test-agent/values.yaml` (mount your configuration there, read-only).

## Architecture

One service in layers. The HTTP backend and the agents are separate packages, so a new agent
(US-003 sandbox fix loop, US-005 review chat) is added without touching the web layer.

```text
src/pdd_agent/
├─ domain/         jobs.py, errors.py (+ quota, time helpers): pure data and rules, no HTTP status
├─ agents/         base.py (Agent protocol); testgen/ = US-002: agent, selection, context, prompt, javacheck
├─ ports/          source.py (SourceRepository), llm.py (LlmClient, LlmPrompt, LlmUsage)
├─ llm/            registry.py, google_genai.py, usage/ (rate limiter, retry wrapper implementing LlmClient)
├─ integrations/   git_gateway.py (implements SourceRepository), pdd_backend.py (project membership)
├─ storage/        SQLite: db.py, migrations.py, jobs.py and usage.py repositories
├─ service/        job use cases, authorization, quota, reuse, execution, worker pool, wiring (container.py)
├─ api/            FastAPI app factory, routes, schemas, JWT auth, error -> HTTP mapping
├─ config/         schema.py, loader.py, values.yaml
└─ cli.py, __main__.py
```

Dependency rule: `api -> service -> agents -> ports <- (llm, integrations, storage)`; `domain` (and
`config`) sit at the bottom. `agents/` imports only `domain`, `ports` and itself, never `fastapi`,
`httpx`, `sqlite3` or `jwt`; domain errors carry a code and a message, and only `api/errors.py` maps
them to HTTP statuses. `uv run lint-imports` enforces this with the contracts in `pyproject.toml`
(`[tool.importlinter]`), and `tests/test_architecture.py` runs the same check in the test suite.

Tests mirror the layers: `tests/unit/<layer>/`, HTTP flows in `tests/api/`. The testgen agent is
tested end to end with in-memory fakes of its ports (`tests/unit/agents/testgen/test_agent.py`).

### Adding an agent

1. Create `agents/<name>/` with an `agent.py` whose class implements `Agent` (`async run(task) -> output`)
   and takes its ports in the constructor. Reuse `SourceRepository` and `LlmClient`; if it needs a new
   capability, define a protocol in `ports/` and implement it in `integrations/` (or `llm/`, `storage/`).
2. Raise `DomainError` subclasses for failures. Do not import from `api`, `service`, `storage`,
   `integrations`, `llm` or `config`: pass settings in as plain dataclasses (see `ContextBudget`).
3. Wire it in `service/container.py`, add the use case in `service/`, and expose it in `api/routes.py`.
4. Add `tests/unit/agents/<name>/` using fake ports, then run the full check above.

## Configuration

Hierarchical YAML in Helm-values style. The commented defaults live in
[`src/pdd_agent/config/values.yaml`](src/pdd_agent/config/values.yaml) and are embedded in the package.
`--config <file>` can be repeated; later files deep-merge over earlier ones (maps merge, scalars
and lists replace). Unknown keys and invalid values are rejected; all problems are reported
together with their YAML path (for example `llm.apiKey: required`). There are no environment
variables. Durations are written `500ms`, `10s`, `1m30s`, `24h`.

| YAML path | Default | Description |
| --- | --- | --- |
| `server.listenAddr` | `0.0.0.0:8000` | Bind address (host:port) |
| `server.logLevel` | `info` | `debug`, `info`, `warn` or `error` |
| `pdd.jwtSecret` | required | Secret the PDD backend signs login JWTs with (same as git-gateway). Take it from the backend deployment configuration |
| `pdd.backend.url` | `https://pdd-backend:5005` | PDD backend, used for `GET /project/` |
| `pdd.backend.insecureTLS` | `false` | Skip TLS verification (self-signed backend) |
| `pdd.backend.timeout` | `10s` | Backend request timeout |
| `pdd.backend.cacheTTL` | `30s` | Reuse time of a membership lookup per token |
| `gitGateway.url` | `http://pdd-git-gateway:8080` | git-gateway base URL |
| `gitGateway.internalToken` | required | Equals `security.internalToken` of git-gateway |
| `llm.provider` | `google_genai` | Model provider; the only supported value for now |
| `llm.model` | `gemini-3.8-flash` | Model name; must be available to your API key in Google AI Studio (otherwise jobs fail with `model_unavailable`) |
| `llm.apiKey` | required | Create one at <https://aistudio.google.com/apikey> |
| `llm.baseURL` | empty | Provider API base URL; empty selects the official one (`https://generativelanguage.googleapis.com` for `google_genai`) |
| `llm.requestTimeout` | `120s` | Timeout of one generation request |
| `limits.requestsPerMinute` | `8` | Gemini requests per 60 seconds, retries included |
| `limits.requestsPerDay` | `200` | Gemini requests per day (resets at midnight Pacific time) |
| `limits.maxAttempts` | `3` | Attempts per generation on 429, 5xx and network errors |
| `limits.baseDelay` / `limits.maxDelay` | `1s` / `30s` | Exponential backoff with full jitter |
| `limits.maxRetryAfter` | `60s` | Longest `Retry-After` that is waited for |
| `limits.reuseWindow` | `24h` | Window in which identical requests reuse a result |
| `context.maxChars` | `200000` | Total character budget of the prompt context |
| `context.maxFiles` | `40` | Maximum number of context files |
| `context.maxFileChars` | `40000` | Maximum characters of a file or of one file's diff |
| `jobs.concurrency` | `2` | Jobs processed at the same time |
| `storage.dataDir` | `/data` | Directory of `test-agent.db` (SQLite) |

## HTTP API

Behind Traefik at `/test-agent` (prefix stripped). All `/api/v1` endpoints need
`Authorization: Bearer <PDD JWT>`: HS256 signature, expiry, `sub` and `oauth` are verified, and
project access is checked against the PDD backend. Errors have the form
`{"error": {"code", "message"}}`: 400 `bad_request`, 401 `unauthorized`, 403 `forbidden`,
404 `not_found`, 429 `quota_exhausted` (with `Retry-After`), 422 (`unsupported_language`, `path_not_found`, `invalid_path`,
`no_target_files`), 502 `upstream_failed`.

| Endpoint | Description |
| --- | --- |
| `POST /api/v1/projects/{projectId}/repos/{repoId}/pulls/{number}/generations` | Body `{"productionPaths": [], "testPaths": []}`; returns `202 {"job": Job}` |
| `GET  /api/v1/projects/{projectId}/repos/{repoId}/pulls/{number}/generations?limit=10` | `{"jobs": [Job]}`, newest first |
| `GET  /api/v1/generations/{jobId}` | `{"job": Job}`; the creator or a member of the job's project |
| `GET  /api/v1/quota` | `{requestsPerDay, usedToday, remainingToday, requestsPerMinute, resetAt}` |
| `GET  /healthz` | Liveness |

Paths may be files or directories of the pull request head tree. Empty `productionPaths` means
the Java files changed by the pull request; empty `testPaths` means `src/test/java`. Directories
expand to `.java` production files, or to `*Test.java`, `*Tests.java` and `*IT.java` files under
`src/test`. A production path that does not exist is rejected with 422; a missing test path is
ignored. The repository must have `pom.xml`, `build.gradle` or `build.gradle.kts` at its root,
otherwise creation fails synchronously with 422 `unsupported_language`.

A job moves `queued` -> `running` -> `succeeded` | `failed` and has this shape:

```json
{
  "id": "...", "status": "succeeded", "createdAt": "...", "updatedAt": "...", "account": "alice",
  "pull": {"projectId": 7, "repoId": 3, "number": 5, "headSha": "..."},
  "context": {"productionPaths": [], "testPaths": [], "includedFiles": [], "omittedFiles": [], "truncated": false},
  "result": {"files": [{"path": "", "content": "", "syntaxValid": true, "syntaxError": null}],
             "model": "gemini-3.8-flash", "usage": {"promptTokens": 0, "outputTokens": 0}, "reused": false},
  "error": null
}
```

Failed jobs carry `error.code`: `gateway_failed`, `gemini_failed`, `model_unavailable`, `invalid_response`,
`output_truncated`, `rate_limited`, `quota_exhausted`, `pull_changed`, `interrupted`, `internal` and the 422 codes above.

## How it works

1. Creation validates the pull request through git-gateway (files, tree at the head SHA, build
   file, path selection) and persists a `queued` job, then answers 202.
2. A worker (`jobs.concurrency` run at once) assembles the context by priority: PR diff (at most
   half of `maxChars`), changed production files, other selected production files, test examples
   closest by package first. `context.includedFiles`, `omittedFiles` and `truncated` report what
   was cut.
3. The prompt asks for JUnit 4 or 5 (detected from the build file and existing tests), Mockito only
   when present, in `src/test/java/<package>/<Class>Test.java`, as strict JSON enforced with
   Gemini `responseMimeType` and `responseSchema`.
4. Each generated file is parsed with `javalang`. A file is marked `syntaxValid: false` with a
   reason when it does not parse, does not declare the type named like the file, has a package that
   differs from its directory, or lies outside `src/test/`. Note that `javalang` implements Java 8
   syntax, so newer constructs (records, text blocks, switch expressions) are reported as errors;
   the prompt asks the model to avoid them.

Jobs live in SQLite (`<dataDir>/test-agent.db`) and survive restarts. At startup `queued` jobs are
re-queued; `running` jobs are failed with code `interrupted` because they may already have used a
Gemini request, and the user submits them again.

## Gemini usage control (NFR-001)

Every Gemini request goes through `ManagedLlmClient` (`llm/usage/`):

- **Limits.** Each attempt (retries included) is recorded in SQLite. When `requestsPerMinute`
  requests were started in the last 60 seconds, the job stays `running` and waits until a slot is
  free (it is never rejected for the per-minute limit). When `requestsPerDay` is used up, job
  creation answers `429 quota_exhausted` with a `Retry-After` header and the reset time; a job that
  meets the exhausted quota later fails with the same code. The day starts at midnight Pacific time,
  when Google resets the daily quota. The counter is persisted, so restarts do not reset it.
- **Defaults.** Google documents no figure for `gemini-3.8-flash` outside Google AI Studio
  (<https://ai.google.dev/gemini-api/docs/rate-limits>, checked 2026-10-24: it only states that
  "Requests per day (RPD) quotas reset at midnight Pacific time"; per-project limits are shown at
  <https://aistudio.google.com/rate-limit>). The defaults 8 per minute and 200 per day are therefore
  conservative placeholders, not 80% of a verified figure. Look up your limits in AI Studio and set
  both values to about 80% of them.
- **Retry.** 429, 5xx and network errors are retried up to `maxAttempts` with exponential backoff and
  full jitter. A `Retry-After` header (seconds or HTTP date) replaces the backoff; one longer than
  `maxRetryAfter` ends the retries. A persistent 429 fails the job with `rate_limited`.
- **Usage.** The prompt size is capped by `context.*`. Tokens of each attempt are stored per job in
  `gemini_usage`, and the job result carries the final `usage`.
- **Reuse.** A job with the same project, repository, head SHA, sorted production and test paths and
  model as a succeeded job created within `reuseWindow` is completed immediately with that result
  (`result.reused: true`, zero usage, no Gemini call, no quota needed).
