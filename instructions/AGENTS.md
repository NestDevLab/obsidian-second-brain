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
