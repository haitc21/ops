# Sprint 1A Contracts and Error Semantics Implementation Plan

> **OPS working copy:** Canonical source is
> `cps/docs/superpowers/plans/2026-07-17-sprint-1-contracts-operations-messaging.md`
> relative to workspace root `CMP/src`. Keep this copy byte-identical below this header.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not start Sprint 1B from this document.

**Goal:** Deliver canonical CPS message/error contracts, a verifiable OPS contract pin, and deterministic OpenStack error/retry classification without database, consumer, or provider-operation work.

**Architecture:** CPS owns Pydantic models, JSON Schemas, golden fixtures, and one checksum manifest covering both schemas and fixtures. OPS stores a byte-for-byte pin plus a second immutable copy of the CPS manifest for standalone CI. OPS maps OpenStackSDK exceptions into the pinned common error contract and produces a retry decision; RabbitMQ acknowledgement behavior is deferred with OPS-102/104 to Sprint 1B.

**Tech Stack:** CPython 3.12, Pydantic 2.13.4, FastAPI 0.139.0, OpenStackSDK 4.17.0 in OPS only, pytest, `jsonschema`, uv, ruff, mypy, detect-secrets.

## Global Constraints

- CPython `>=3.12,<3.13`; never Python 3.14.
- CPS must not depend on OpenStackSDK; OPS must not add a business database dependency.
- CPS is the only editable source of common contracts. OPS copies contracts byte-for-byte.
- Manifest covers every non-`.gitkeep` file below `fixtures/` and `jsonschema/`.
- Unknown major `schema_version` is rejected; additive unknown fields are accepted.
- Fixtures, schemas, logs, errors, and test output contain no password, token, Authorization value, CA private material, or `user_data`.
- Commands may contain `credential_reference`; events and inventory fixtures must not contain it.
- CodeGraph-first for source discovery; use RTK for supported external commands.
- Scope is CPS-101, CPS-102, OPS-101, and OPS-103 only. CPS-103..106 and OPS-102/104 require a separate Sprint 1B plan.

## File Map

### CPS

- Modify `src/cps/contracts/validate.py`: hash fixtures and JSON Schemas.
- Create `src/cps/contracts/messages/envelope.py`: envelope and version validation.
- Create `src/cps/contracts/messages/types.py`: supported message type constants.
- Create `src/cps/contracts/errors.py`: common error contract and stable codes.
- Create `src/cps/api/errors.py`: FastAPI error handlers.
- Modify `src/cps/main.py`: register handlers.
- Create `src/cps/contracts/fixtures/**`: golden fixtures.
- Create `src/cps/contracts/jsonschema/**`: generated schemas.
- Modify `src/cps/contracts/checksums.json`: canonical manifest.
- Modify/create contract and API tests listed below.

### OPS

- Modify `src/ops/contracts/validate.py`: same local manifest algorithm plus canonical-pin comparison.
- Copy CPS `fixtures/`, `jsonschema/`, and `checksums.json` into `src/ops/contracts/`.
- Create `src/ops/contracts/cps_checksums.pinned.json`: byte-for-byte CPS manifest snapshot for standalone CI.
- Modify `.husky/pre-commit`: validate the local tree and pinned manifest before a user-authorized commit.
- Create `src/ops/openstack/errors.py`: SDK exception normalization.
- Create `src/ops/openstack/retry.py`: deterministic retry decisions.
- Create contract and unit tests listed below.

---

### Task 0: Contract manifest covers fixtures and JSON Schemas

**Files:**
- Modify `cps/src/cps/contracts/validate.py`
- Modify `cps/tests/contract/test_contract_manifest.py`
- Modify `ops/src/ops/contracts/validate.py`
- Modify `ops/tests/contract/test_contract_manifest.py`

**Interfaces:**

Produces `compute_contract_checksums(base: Path) -> dict[str, str]`,
`validate_contract_tree(root: Path | None = None) -> ValidationResult`, and
`write_contract_manifest(root: Path | None = None) -> ValidationResult`. Their complete
implementations are in Step 3.

- [ ] **Step 1: Add the failing test in both repositories**

Use the package-specific import (`cps` or `ops`):

```python
def test_manifest_detects_jsonschema_change(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    schemas = tmp_path / "jsonschema"
    fixtures.mkdir()
    schemas.mkdir()
    (fixtures / "message.json").write_text("{}", encoding="utf-8")
    schema = schemas / "message.schema.json"
    schema.write_text("{}", encoding="utf-8")

    write_contract_manifest(tmp_path)
    schema.write_text('{"type": "object"}', encoding="utf-8")

    result = validate_contract_tree(tmp_path)
    assert result.ok is False
    assert result.message == "contract checksum mismatch"


def test_manifest_uses_files_key(tmp_path: Path) -> None:
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "jsonschema").mkdir()
    write_contract_manifest(tmp_path)
    manifest = json.loads((tmp_path / "checksums.json").read_text(encoding="utf-8"))
    assert manifest == {"files": {}}
```

- [ ] **Step 2: Verify RED**

Run in each repo:

```powershell
py -3.12 -m uv run pytest tests/contract/test_contract_manifest.py -q
```

Expected: the schema-change test fails because Sprint 0 hashes only `fixtures/`, or the manifest still uses the `fixtures` key.

- [ ] **Step 3: Replace the checksum implementation in both packages**

Keep the existing `ValidationResult` and `main()`, and replace the checksum/validation/writer functions with the package-adjusted version below:

```python
def _contract_files(base: Path) -> list[Path]:
    files: list[Path] = []
    for directory in ("fixtures", "jsonschema"):
        root = base / directory
        if root.exists():
            files.extend(
                path
                for path in root.rglob("*")
                if path.is_file() and path.name != ".gitkeep"
            )
    return sorted(files)


def compute_contract_checksums(base: Path) -> dict[str, str]:
    return {
        path.relative_to(base).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in _contract_files(base)
    }


def validate_contract_tree(root: Path | None = None) -> ValidationResult:
    base = root or CONTRACTS_ROOT
    manifest_path = base / "checksums.json"
    computed = compute_contract_checksums(base)
    if not manifest_path.exists():
        return ValidationResult(False, len(computed), "missing checksums.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ValidationResult(False, len(computed), "invalid checksums.json")
    if manifest.get("files") != computed:
        return ValidationResult(False, len(computed), "contract checksum mismatch")
    return ValidationResult(True, len(computed))


def write_contract_manifest(root: Path | None = None) -> ValidationResult:
    base = root or CONTRACTS_ROOT
    (base / "fixtures").mkdir(parents=True, exist_ok=True)
    (base / "jsonschema").mkdir(parents=True, exist_ok=True)
    computed = compute_contract_checksums(base)
    (base / "checksums.json").write_text(
        json.dumps({"files": computed}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return ValidationResult(True, len(computed), "manifest written")
```

- [ ] **Step 4: Refresh both empty manifests and verify GREEN**

```powershell
cd C:\work\Cloud\project\CMP\src\cps
py -3.12 -m uv run python -m cps.contracts.write_manifest
py -3.12 -m uv run pytest tests/contract -q
cd C:\work\Cloud\project\CMP\src\ops
py -3.12 -m uv run python -m ops.contracts.write_manifest
py -3.12 -m uv run pytest tests/contract -q
```

Expected: both contract suites pass and both manifests contain `{"files": {}}` before Task 1 adds contracts.

- [ ] **Step 5: Commit independently in both repos**

```powershell
Leave files unstaged and propose: `fix(contracts): checksum fixtures and jsonschema`.
Wait for explicit current-turn authorization before staging or committing.
```

---

### Task 1: CPS-101 canonical envelope, schemas, and golden fixtures

**Files:**
- Create `cps/src/cps/contracts/messages/__init__.py`
- Create `cps/src/cps/contracts/messages/envelope.py`
- Create `cps/src/cps/contracts/messages/types.py`
- Create `cps/src/cps/contracts/fixtures/commands/connection_validate.json`
- Create `cps/src/cps/contracts/fixtures/events/operation_progress.json`
- Create `cps/src/cps/contracts/fixtures/events/operation_completed.json`
- Create `cps/src/cps/contracts/fixtures/events/operation_failed.json`
- Create `cps/src/cps/contracts/fixtures/events/inventory_batch.json`
- Create `cps/src/cps/contracts/jsonschema/message_envelope.schema.json`
- Create `cps/tests/contract/test_envelope_contract.py`
- Modify `cps/src/cps/contracts/checksums.json`

**Interfaces:** `MessageEnvelope`, `parse_schema_version()`, `assert_supported_major()`, and the five constants below.

- [ ] **Step 1: Add dependencies and lock them**

Add `jsonschema>=4.25,<5` to CPS dev dependencies and run:

```powershell
py -3.12 -m uv lock
py -3.12 -m uv sync --frozen --all-extras
```

Expected: `uv.lock` changes and `py -3.12 -m uv run python -c "import jsonschema"` exits 0.

- [ ] **Step 2: Write the failing contract tests**

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from cps.contracts.messages.envelope import MessageEnvelope, assert_supported_major

ROOT = Path(__file__).resolve().parents[2] / "src" / "cps" / "contracts"
FIXTURES = ROOT / "fixtures"
SCHEMA_PATH = ROOT / "jsonschema" / "message_envelope.schema.json"
FIXTURE_PATHS = (
    FIXTURES / "commands" / "connection_validate.json",
    FIXTURES / "events" / "operation_progress.json",
    FIXTURES / "events" / "operation_completed.json",
    FIXTURES / "events" / "operation_failed.json",
    FIXTURES / "events" / "inventory_batch.json",
)


@pytest.mark.parametrize("fixture_path", FIXTURE_PATHS, ids=lambda path: path.stem)
def test_every_fixture_validates_with_pydantic_and_jsonschema(fixture_path: Path) -> None:
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    MessageEnvelope.model_validate(raw)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(raw)


@pytest.mark.parametrize("fixture_path", FIXTURE_PATHS)
def test_fixtures_have_no_inline_secrets(fixture_path: Path) -> None:
    text = fixture_path.read_text(encoding="utf-8").lower()
    for forbidden in ("password", "token", "authorization", "user_data", "private_key"):
        assert forbidden not in text


@pytest.mark.parametrize("fixture_path", FIXTURE_PATHS[1:])
def test_events_omit_credential_reference(fixture_path: Path) -> None:
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert "credential_reference" not in raw


def test_command_contains_credential_reference() -> None:
    raw = json.loads(FIXTURE_PATHS[0].read_text(encoding="utf-8"))
    assert raw["credential_reference"] == "66666666-6666-4666-8666-666666666666"


def test_unknown_major_rejected_and_unknown_minor_field_accepted() -> None:
    with pytest.raises(ValueError, match="unsupported major"):
        assert_supported_major("2.0")
    raw = json.loads(FIXTURE_PATHS[0].read_text(encoding="utf-8"))
    raw["future_minor_field"] = {"safe": True}
    MessageEnvelope.model_validate(raw)
```

- [ ] **Step 3: Verify RED**

```powershell
py -3.12 -m uv run pytest tests/contract/test_envelope_contract.py -q
```

Expected: import or fixture-path failure.

- [ ] **Step 4: Implement envelope and message types**

`envelope.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def parse_schema_version(version: str) -> tuple[int, int]:
    parts = version.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"invalid schema version: {version}")
    return int(parts[0]), int(parts[1])


def assert_supported_major(version: str, *, supported_major: int = 1) -> None:
    major, _minor = parse_schema_version(version)
    if major != supported_major:
        raise ValueError(f"unsupported major schema version: {version}")


class MessageEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message_id: UUID
    message_type: str = Field(min_length=1)
    schema_version: str
    occurred_at: datetime
    correlation_id: UUID
    causation_id: UUID | None = None
    operation_id: UUID
    idempotency_key: str | None = None
    provider_id: UUID
    provider_connection_id: UUID
    credential_reference: UUID | None = None
    trace_context: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        assert_supported_major(value)
        return value
```

`types.py`:

```python
CONNECTION_VALIDATE = "openstack.connection.validate"
OPERATION_PROGRESS = "cloud.operation.progress"
OPERATION_COMPLETED = "cloud.operation.completed"
OPERATION_FAILED = "cloud.operation.failed"
INVENTORY_BATCH = "cloud.inventory.batch"
```

- [ ] **Step 5: Create all five fixtures**

Use the envelope from the approved design and synthetic UUIDs. The command uses `credential_reference`; each event omits that key entirely. Payloads are:

```json
{"auth_url":"https://example.test:5000/v3","user_domain_name":"Default","project_domain_name":"Default","project_id":"project-synthetic","region_name":"RegionOne","interface":"public"}
```

```json
{"progress":25,"message":"validating service catalog"}
```

```json
{"result":{"status":"VALID","capabilities":{"compute":true,"network":true,"image":true,"volume":true}}}
```

```json
{"error":{"code":"PROVIDER_AUTHENTICATION_FAILED","message":"OpenStack authentication failed","category":"AUTHENTICATION","retryable":false,"provider":"OPENSTACK","provider_service":"identity","provider_request_id":"req-synthetic","details":{},"occurred_at":"2026-07-17T00:00:03Z"}}
```

```json
{"sync_id":"77777777-7777-4777-8777-777777777777","resource_type":"instance","sequence":1,"is_last":true,"items":[]}
```

For all fixtures use `schema_version: "1.0"`, RFC3339 UTC times, and consistent operation/correlation/causation IDs. The event `causation_id` equals the command `message_id`.

- [ ] **Step 6: Export schema, refresh manifest, and verify GREEN**

```powershell
py -3.12 -m uv run python -c "import json; from pathlib import Path; from cps.contracts.messages.envelope import MessageEnvelope; p=Path('src/cps/contracts/jsonschema/message_envelope.schema.json'); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(MessageEnvelope.model_json_schema(),indent=2)+chr(10),encoding='utf-8')"
py -3.12 -m uv run python -m cps.contracts.write_manifest
py -3.12 -m uv run pytest tests/contract -q
```

Expected: all five fixtures validate twice; the manifest lists six files or more.

- [ ] **Step 7: Commit**

```powershell
Leave files unstaged and propose: `feat(CPS-101): add canonical message contracts`.
Wait for explicit current-turn authorization before staging or committing.
```

---

### Task 2: CPS-102 common error contract and API mappings

**Files:**
- Create `cps/src/cps/contracts/errors.py`
- Create `cps/src/cps/api/errors.py`
- Modify `cps/src/cps/main.py`
- Create `cps/src/cps/contracts/fixtures/errors/provider_authentication_failed.json`
- Create `cps/src/cps/contracts/jsonschema/common_error.schema.json`
- Create `cps/tests/contract/test_error_contract.py`
- Create `cps/tests/unit/api/test_error_handlers.py`

**Stable mappings:** validation 422 `INVALID_REQUEST`; not found 404 `NOT_FOUND`; conflict 409 `CONFLICT`; unsupported capability 422 `CAPABILITY_UNSUPPORTED`; provider error 502 `PROVIDER_ERROR`; timeout 504 `OPERATION_TIMEOUT`; unexpected error 500 `INTERNAL_ERROR`.

- [ ] **Step 1: Write failing model and API tests**

`test_error_contract.py`:

```python
def test_authentication_error_fixture_is_safe() -> None:
    path = Path("src/cps/contracts/fixtures/errors/provider_authentication_failed.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    error = CommonError.model_validate(raw)
    assert error.code == "PROVIDER_AUTHENTICATION_FAILED"
    assert error.retryable is False
    assert not ({"password", "token", "authorization"} & set(error.details))
```

`test_error_handlers.py`:

```python
@pytest.mark.parametrize(
    ("exc", "status_code", "code"),
    (
        (ResourceNotFoundError("missing"), 404, "NOT_FOUND"),
        (DomainConflictError("conflict"), 409, "CONFLICT"),
        (CapabilityUnsupportedError("unsupported"), 422, "CAPABILITY_UNSUPPORTED"),
        (ProviderOperationError("provider failed"), 502, "PROVIDER_ERROR"),
        (OperationTimeoutError("timed out"), 504, "OPERATION_TIMEOUT"),
    ),
)
def test_domain_errors_use_common_envelope(exc: Exception, status_code: int, code: str) -> None:
    app = create_app(Settings(environment="test", _env_file=None))

    @app.get("/_test/error")
    async def raise_error() -> None:
        raise exc

    response = TestClient(app, raise_server_exceptions=False).get("/_test/error")
    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    assert response.headers["x-correlation-id"]


def test_validation_and_internal_errors_use_common_envelope() -> None:
    app = create_app(Settings(environment="test", _env_file=None))

    @app.get("/_test/validation")
    async def validation(value: int) -> dict[str, int]:
        return {"value": value}

    @app.get("/_test/internal")
    async def internal() -> None:
        raise RuntimeError("unsafe internal detail")

    client = TestClient(app, raise_server_exceptions=False)
    invalid = client.get("/_test/validation")
    internal_response = client.get("/_test/internal")
    assert (invalid.status_code, invalid.json()["error"]["code"]) == (422, "INVALID_REQUEST")
    assert (internal_response.status_code, internal_response.json()["error"]["code"]) == (500, "INTERNAL_ERROR")
    assert "unsafe internal detail" not in internal_response.text
```

- [ ] **Step 2: Verify RED**

```powershell
py -3.12 -m uv run pytest tests/contract/test_error_contract.py tests/unit/api/test_error_handlers.py -q
```

Expected: missing error modules/classes.

- [ ] **Step 3: Implement the error model and exception types**

```python
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorCategory(StrEnum):
    VALIDATION = "VALIDATION"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    CAPABILITY = "CAPABILITY"
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    QUOTA = "QUOTA"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    PROVIDER = "PROVIDER"
    INTERNAL = "INTERNAL"


class CommonError(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: str
    message: str
    category: ErrorCategory
    retryable: bool
    provider: str | None = None
    provider_service: str | None = None
    provider_request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DomainError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"
    category = ErrorCategory.INTERNAL
    retryable = False


class ResourceNotFoundError(DomainError):
    status_code, code, category = 404, "NOT_FOUND", ErrorCategory.NOT_FOUND


class DomainConflictError(DomainError):
    status_code, code, category = 409, "CONFLICT", ErrorCategory.CONFLICT


class CapabilityUnsupportedError(DomainError):
    status_code, code, category = 422, "CAPABILITY_UNSUPPORTED", ErrorCategory.CAPABILITY


class ProviderOperationError(DomainError):
    status_code, code, category = 502, "PROVIDER_ERROR", ErrorCategory.PROVIDER


class OperationTimeoutError(DomainError):
    status_code, code, category = 504, "OPERATION_TIMEOUT", ErrorCategory.TIMEOUT
    retryable = True
```

- [ ] **Step 4: Implement FastAPI handlers and register them**

```python
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from cps.contracts.errors import CommonError, DomainError, ErrorCategory


def _response(request: Request, error: CommonError, status_code: int) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", str(uuid4()))
    return JSONResponse(
        status_code=status_code,
        content={"error": error.model_dump(mode="json"), "correlation_id": correlation_id},
        headers={"x-correlation-id": correlation_id},
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, _exc: RequestValidationError) -> JSONResponse:
        error = CommonError(
            code="INVALID_REQUEST",
            message="Request validation failed",
            category=ErrorCategory.VALIDATION,
            retryable=False,
        )
        return _response(request, error, 422)

    @app.exception_handler(DomainError)
    async def domain_handler(request: Request, exc: DomainError) -> JSONResponse:
        error = CommonError(
            code=exc.code,
            message=str(exc),
            category=exc.category,
            retryable=exc.retryable,
        )
        return _response(request, error, exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_handler(request: Request, _exc: Exception) -> JSONResponse:
        error = CommonError(
            code="INTERNAL_ERROR",
            message="Internal service error",
            category=ErrorCategory.INTERNAL,
            retryable=False,
        )
        return _response(request, error, 500)
```

Register it in `create_app()` after middleware and before returning the app:

```python
register_error_handlers(app)
```

- [ ] **Step 5: Export schema, create fixture, refresh manifest, and verify GREEN**

```powershell
py -3.12 -m uv run python -c "import json; from pathlib import Path; from cps.contracts.errors import CommonError; p=Path('src/cps/contracts/jsonschema/common_error.schema.json'); p.write_text(json.dumps(CommonError.model_json_schema(),indent=2)+chr(10),encoding='utf-8')"
py -3.12 -m uv run python -m cps.contracts.write_manifest
py -3.12 -m uv run pytest tests/contract/test_error_contract.py tests/unit/api/test_error_handlers.py -q
```

Create the authentication fixture from the `operation_failed` payload error in Task 1. Expected: all mappings pass and the manifest includes the error fixture and schema.

- [ ] **Step 6: Commit**

```powershell
Leave files unstaged and propose: `feat(CPS-102): add common error contract and API mappings`.
Wait for explicit current-turn authorization before staging or committing.
```

---

### Task 3: OPS-101 byte-for-byte contract pin and standalone quality guard

**Files:**
- Copy CPS contract assets to `ops/src/ops/contracts/`
- Copy `cps/src/cps/contracts/errors.py` to `ops/src/ops/contracts/errors.py`
- Create `ops/src/ops/contracts/cps_checksums.pinned.json`
- Modify `ops/src/ops/contracts/validate.py`
- Create `ops/tests/contract/test_pin_against_cps.py`
- Modify `ops/.husky/pre-commit`

- [ ] **Step 1: Write the failing pin test**

```python
from pathlib import Path

from ops.contracts.validate import assert_matches_cps_canonical


def test_local_contract_tree_matches_pinned_cps_manifest() -> None:
    contracts = Path("src/ops/contracts")
    assert_matches_cps_canonical(
        contracts / "cps_checksums.pinned.json",
        ops_root=contracts,
    )
```

- [ ] **Step 2: Verify RED**

```powershell
py -3.12 -m uv run pytest tests/contract/test_pin_against_cps.py -q
```

Expected: missing pinned manifest or helper.

- [ ] **Step 3: Implement the comparison helper**

```python
def assert_matches_cps_canonical(
    cps_checksums: Path,
    *,
    ops_root: Path | None = None,
) -> None:
    base = ops_root or CONTRACTS_ROOT
    local_manifest = base / "checksums.json"
    if not cps_checksums.is_file():
        raise AssertionError(f"missing pinned CPS manifest: {cps_checksums}")
    if not local_manifest.is_file():
        raise AssertionError(f"missing OPS manifest: {local_manifest}")
    if local_manifest.read_bytes() != cps_checksums.read_bytes():
        raise AssertionError("OPS manifest differs from pinned CPS manifest")
    result = validate_contract_tree(base)
    if not result.ok:
        raise AssertionError(result.message)
```

- [ ] **Step 4: Copy contracts from the workspace root and create the immutable pin**

```powershell
cd C:\work\Cloud\project\CMP\src
$source = "cps\src\cps\contracts"
$target = "ops\src\ops\contracts"
Copy-Item "$source\checksums.json" "$target\checksums.json" -Force
Copy-Item "$source\checksums.json" "$target\cps_checksums.pinned.json" -Force
Copy-Item "$source\errors.py" "$target\errors.py" -Force
robocopy "$source\fixtures" "$target\fixtures" /MIR
if ($LASTEXITCODE -gt 7) { throw "robocopy fixtures failed: $LASTEXITCODE" }
robocopy "$source\jsonschema" "$target\jsonschema" /MIR
if ($LASTEXITCODE -gt 7) { throw "robocopy jsonschema failed: $LASTEXITCODE" }
```

- [ ] **Step 5: Add the standalone quality guard**

After local contract validation in the OPS Husky hook, add:

```yaml
- name: Verify CPS contract pin
  run: >-
    uv run python -c "from pathlib import Path;
    from ops.contracts.validate import assert_matches_cps_canonical;
    p=Path('src/ops/contracts');
    assert_matches_cps_canonical(p/'cps_checksums.pinned.json', ops_root=p)"
```

This contains no machine-specific path. Updating `cps_checksums.pinned.json` is allowed only in the same reviewed change that copies a new canonical CPS manifest.

- [ ] **Step 6: Verify GREEN and commit**

```powershell
cd C:\work\Cloud\project\CMP\src\ops
py -3.12 -m uv run python -m ops.contracts.validate_contracts
py -3.12 -m uv run pytest tests/contract -q
Stop with the relevant files unstaged and propose `feat(OPS-101): pin canonical CPS contracts`; only stage or commit after explicit user authorization in the current turn.
```

Expected: local tree validates and its manifest bytes equal the pinned canonical snapshot.

---

### Task 4: OPS-103 OpenStack error normalization and retry decisions

**Files:**
- Create `ops/src/ops/openstack/__init__.py`
- Create `ops/src/ops/openstack/errors.py`
- Create `ops/src/ops/openstack/retry.py`
- Create `ops/tests/unit/openstack/test_error_normalization.py`
- Create `ops/tests/unit/openstack/test_retry_policy.py`

**Interfaces:**

Produces `RetryDecision`, `normalize_openstack_exception()`, and `classify_retry()`.
Their complete implementations are in Steps 4 and 5.

`RetryDecision` deliberately does not select RabbitMQ ack/requeue/DLQ. That belongs to OPS-102/104 in Sprint 1B, where retry publishing and attempt headers can be implemented atomically with acknowledgement policy.

- [ ] **Step 1: Write failing normalization tests**

```python
@pytest.mark.parametrize(
    ("exc", "code", "category", "retryable"),
    (
        (os_exc.HttpException("auth", http_status=401), "PROVIDER_AUTHENTICATION_FAILED", "AUTHENTICATION", False),
        (os_exc.ForbiddenException("forbidden"), "PROVIDER_FORBIDDEN", "AUTHORIZATION", False),
        (os_exc.ResourceNotFound("missing"), "PROVIDER_RESOURCE_NOT_FOUND", "NOT_FOUND", False),
        (os_exc.ConflictException("conflict"), "PROVIDER_CONFLICT", "CONFLICT", False),
        (os_exc.HttpException("limited", http_status=429), "PROVIDER_RATE_LIMITED", "RATE_LIMIT", True),
        (os_exc.HttpException("unavailable", http_status=503), "PROVIDER_UNAVAILABLE", "PROVIDER", True),
        (TimeoutError("timeout"), "PROVIDER_TIMEOUT", "TIMEOUT", True),
        (ConnectionError("network"), "PROVIDER_NETWORK_ERROR", "NETWORK", True),
    ),
)
def test_normalization(exc, code: str, category: str, retryable: bool) -> None:
    error = normalize_openstack_exception(exc, service="compute")
    assert (error.code, error.category.value, error.retryable) == (code, category, retryable)
    assert error.provider == "OPENSTACK"
    assert error.provider_service == "compute"
    assert "response" not in error.details
```

Add a separate fake exception with `request_id = "req-safe"` and `response.text = "secret-body"`; assert request ID is retained and `secret-body` is absent from `model_dump_json()`.

- [ ] **Step 2: Write failing deterministic retry tests**

```python
def test_retry_uses_retry_after_when_present() -> None:
    error = CommonError(code="PROVIDER_RATE_LIMITED", message="limited", category="RATE_LIMIT", retryable=True)
    decision = classify_retry(error, attempt=1, retry_after=17.0)
    assert decision == RetryDecision(retryable=True, exhausted=False, delay_seconds=17.0)


def test_retry_uses_exponential_backoff_and_jitter() -> None:
    error = CommonError(code="PROVIDER_TIMEOUT", message="timeout", category="TIMEOUT", retryable=True)
    decision = classify_retry(error, attempt=3, random_unit=0.5)
    assert decision.delay_seconds == 5.0  # base 1 * 2**2 + jitter in [0,2]


def test_non_retryable_and_exhausted_have_no_delay() -> None:
    fatal = CommonError(code="PROVIDER_FORBIDDEN", message="forbidden", category="AUTHORIZATION", retryable=False)
    transient = CommonError(code="PROVIDER_TIMEOUT", message="timeout", category="TIMEOUT", retryable=True)
    assert classify_retry(fatal, attempt=1) == RetryDecision(False, False, None)
    assert classify_retry(transient, attempt=5, max_attempts=5) == RetryDecision(False, True, None)
```

- [ ] **Step 3: Verify RED**

```powershell
py -3.12 -m uv run pytest tests/unit/openstack/test_error_normalization.py tests/unit/openstack/test_retry_policy.py -q
```

Expected: missing OPS OpenStack error modules.

- [ ] **Step 4: Implement normalization**

```python
from __future__ import annotations

from openstack import exceptions as os_exc

from ops.contracts.errors import CommonError, ErrorCategory


def _request_id(exc: BaseException) -> str | None:
    direct = getattr(exc, "request_id", None)
    if isinstance(direct, str) and direct:
        return direct
    many = getattr(exc, "request_ids", None)
    if isinstance(many, (list, tuple)) and many and isinstance(many[0], str):
        return many[0]
    return None


def normalize_openstack_exception(
    exc: BaseException,
    *,
    service: str | None = None,
) -> CommonError:
    status = getattr(exc, "http_status", None)
    if status == 401:
        code, category, retryable = "PROVIDER_AUTHENTICATION_FAILED", ErrorCategory.AUTHENTICATION, False
    elif status == 403 or isinstance(exc, os_exc.ForbiddenException):
        code, category, retryable = "PROVIDER_FORBIDDEN", ErrorCategory.AUTHORIZATION, False
    elif status == 404 or isinstance(exc, (os_exc.ResourceNotFound, os_exc.NotFoundException)):
        code, category, retryable = "PROVIDER_RESOURCE_NOT_FOUND", ErrorCategory.NOT_FOUND, False
    elif status == 409 or isinstance(exc, os_exc.ConflictException):
        code, category, retryable = "PROVIDER_CONFLICT", ErrorCategory.CONFLICT, False
    elif status == 429:
        code, category, retryable = "PROVIDER_RATE_LIMITED", ErrorCategory.RATE_LIMIT, True
    elif isinstance(status, int) and 500 <= status <= 599:
        code, category, retryable = "PROVIDER_UNAVAILABLE", ErrorCategory.PROVIDER, True
    elif isinstance(exc, (TimeoutError, os_exc.ResourceTimeout)):
        code, category, retryable = "PROVIDER_TIMEOUT", ErrorCategory.TIMEOUT, True
    elif isinstance(exc, ConnectionError):
        code, category, retryable = "PROVIDER_NETWORK_ERROR", ErrorCategory.NETWORK, True
    else:
        code, category, retryable = "PROVIDER_INTERNAL_ERROR", ErrorCategory.PROVIDER, False
    return CommonError(
        code=code,
        message="OpenStack provider request failed",
        category=category,
        retryable=retryable,
        provider="OPENSTACK",
        provider_service=service,
        provider_request_id=_request_id(exc),
        details={},
    )
```

- [ ] **Step 5: Implement deterministic retry calculation**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryDecision:
    retryable: bool
    exhausted: bool
    delay_seconds: float | None


def classify_retry(
    error: CommonError,
    *,
    attempt: int,
    max_attempts: int = 5,
    retry_after: float | None = None,
    random_unit: float = 0.5,
) -> RetryDecision:
    if attempt < 1 or max_attempts < 1 or not 0.0 <= random_unit <= 1.0:
        raise ValueError("invalid retry parameters")
    if not error.retryable:
        return RetryDecision(False, False, None)
    if attempt >= max_attempts:
        return RetryDecision(False, True, None)
    if retry_after is not None:
        return RetryDecision(True, False, max(0.0, retry_after))
    exponential = min(60.0, float(2 ** (attempt - 1)))
    jitter = exponential * 0.5 * random_unit
    return RetryDecision(True, False, exponential + jitter)
```

- [ ] **Step 6: Verify GREEN and commit**

```powershell
py -3.12 -m uv run pytest tests/unit/openstack -q
py -3.12 -m uv run ruff check src tests
py -3.12 -m uv run mypy
Leave files unstaged and propose: `feat(OPS-103): normalize OpenStack errors and classify retries`.
Wait for explicit current-turn authorization before staging or committing.
```

---

### Task 5: Sprint 1A verification and evidence

- [ ] **Step 1: Verify CPS**

```powershell
cd C:\work\Cloud\project\CMP\src\cps
py -3.12 -m uv sync --frozen --all-extras
py -3.12 -m uv run ruff format --check src tests
py -3.12 -m uv run ruff check src tests
py -3.12 -m uv run mypy
py -3.12 -m uv run pytest -q
py -3.12 -m uv run python -m cps.contracts.validate_contracts
py -3.12 -m uv run python -m detect_secrets scan --baseline .secrets.baseline --exclude-files "(?i)(.*\.venv/.*|.*uv\.lock$|.*\.git/.*)"
rtk git diff --check
rtk docker build -t cps:sprint1a .
```

- [ ] **Step 2: Verify OPS**

```powershell
cd C:\work\Cloud\project\CMP\src\ops
py -3.12 -m uv sync --frozen --all-extras
py -3.12 -m uv run ruff format --check src tests
py -3.12 -m uv run ruff check src tests
py -3.12 -m uv run mypy
py -3.12 -m uv run pytest -q
py -3.12 -m uv run python -m ops.contracts.validate_contracts
py -3.12 -m uv run python -c "from pathlib import Path; from ops.contracts.validate import assert_matches_cps_canonical; p=Path('src/ops/contracts'); assert_matches_cps_canonical(p/'cps_checksums.pinned.json', ops_root=p)"
py -3.12 -m uv run python -m detect_secrets scan --baseline .secrets.baseline --exclude-files "(?i)(.*\.venv/.*|.*uv\.lock$|.*\.git/.*)"
rtk git diff --check
rtk docker build -t ops:sprint1a .
```

- [ ] **Step 3: Update evidence and commit documentation**

Update both `plan/sprints/sprint-1.md` files with exact test counts, contract manifest SHA-256, known limitations, and retrospective. Commit in each repository using `docs: record Sprint 1A evidence`.

## Commit Boundaries

1. CPS Task 0 manifest algorithm.
2. OPS Task 0 manifest algorithm.
3. CPS-101 canonical contracts.
4. CPS-102 common errors and API mappings.
5. OPS-101 pinned contracts.
6. OPS-103 error normalization/retry classification.
7. Evidence commit in each repository.

## Self-review

- Every committed 1A story is delivered completely; no story is split across 1A/1B.
- Every fixture is validated by both Pydantic and exported JSON Schema.
- OPS standalone CI compares its live manifest with a pinned CPS manifest and validates every pinned file.
- CPS-102 covers validation, not-found, conflict, capability, provider, timeout, and unexpected errors.
- Retry classification is deterministic and does not pretend that RabbitMQ backoff exists before Sprint 1B.
- No database, topology, consumer, ack/requeue/DLQ, or OpenStack request code is included.
- Sprint 1B requires a new reviewed plan for CPS-103..106 and OPS-102/104.
