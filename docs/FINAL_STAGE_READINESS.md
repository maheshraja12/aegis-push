# AEGIS V3 — Final-Stage Readiness

This document defines the implementation boundary before deployment. Deployment remains a manual, separately approved step.

## Current verified scope

- Python-based patch analysis and execution prototype.
- AST/bytecode checks and a process-based timeout regression test are present in the current project history.
- Dashboard hardening is merged in PR #1; latest CI evidence (41 tests, Docker build, guarded-container smoke test): https://github.com/maheshraja12/aegis-push/actions/runs/38263854656

## Required engineering gates before calling this release-ready

### 1. Execution isolation
- Treat AST/bytecode deny-lists as defense-in-depth only; they are not a security boundary.
- Execute untrusted patches only in an OS/container sandbox with least privilege, read-only root filesystem, explicit writable temp directory, dropped capabilities, syscall restrictions, and no network by default.
- Enforce CPU, memory, process-count, file-size, and wall-clock limits externally, not only with Python-level accounting.
- Terminate the whole process group on timeout and clean up temporary resources.

### 2. Patch validation and recovery
- Validate patch format and allowed file paths before applying changes.
- Reject path traversal, symlinks escaping the workspace, oversized patches, and changes to protected files.
- Apply changes to a disposable worktree; run configured checks there.
- Preserve the original revision and produce a deterministic rollback plan.
- Never deploy or modify production systems automatically in this stage.

### 3. Secrets and network
- Keep credentials out of logs, prompts, test fixtures, and generated patches.
- Default to no outbound network for patch execution.
- Use explicit allow-lists for any required external integrations.
- Redact secrets and sensitive filesystem paths from API responses.

### 4. Reliability and observability
- Use structured logs with correlation IDs and redaction.
- Make operations idempotent where possible.
- Add bounded retries with backoff and clear failure states.
- Persist an auditable record of input revision, generated patch hash, validation results, approver, and rollback reference.

### 5. Release gate
Do not label this production-ready until all applicable security, integration, recovery, and reproducibility checks have actually passed in CI and have been reviewed. A green unit-test run alone is not sufficient.

## Deployment status

**Not deployed.** Deployment configuration and execution are intentionally left for the project owner to direct after reviewing the final readiness report.

## Completion tracking

This file is a readiness checklist, not proof that each item is implemented. Mark an item complete only after the corresponding code, automated test, and evidence link are available.
