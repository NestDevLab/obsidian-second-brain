# Obsidian Second Brain Agent Instructions

This repository is a portable Obsidian client and configuration layer. Keep the
upstream `eugeniughelbur/obsidian-second-brain` skill independently installable
and versioned; do not vendor or rewrite it here.

The current standalone hooks remain the default behavior. Agent Memory Fabric
(AMF) integration is an optional backend and synchronization mode, not a hard
dependency. Follow the public architecture contract in
<https://github.com/NestDevLab/agent-memory-fabric/blob/main/docs/obsidian-second-brain.md>.

## Change discipline

- Change hook and instruction sources in this repository, never generated
  runtime destinations.
- Use the checked-in Syncwheel manifest before branch, worktree, stack, push,
  or pull-request work.
- Use Agentwheel to inspect and plan OpenPack installation. Run a dry-run before
  installing into a harness.
- Preserve standalone operation when AMF is unavailable or disabled.
- Keep the package runtime-agnostic and avoid private paths, identities, hosts,
  credentials, or environment-specific topology in public artifacts.
- Add deterministic tests for behavioral changes; keep documentation aligned
  with the actual supported backends and synchronization direction.

## AMF client behavior

- Use `python3 -m obsidian_amf scan|drain|status` for revisioned document
  capture and delivery health; never treat a successful scan as proof that the
  outbox was delivered.
- Use `search` for direct SQLite or AMF contextual recall. In `shadow` mode the
  direct result is authoritative and AMF failure is diagnostic only.
- For AMF search, prefer the actor-specific owner-only context key ring and
  policy revision. A literal context token is short-lived, bound to one exact
  request, and must not be reused as configuration.
- `propose` queues a complete PAM-compatible proposal; it never writes canonical
  memory directly.
- Run `project` only after an explicit selection. Managed projections are active
  plaintext PAM records under `.amf/records/`; never copy sealed claims or edit
  those files as if they were canonical memory.
