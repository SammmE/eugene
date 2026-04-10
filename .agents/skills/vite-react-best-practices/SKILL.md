---
name: vite-react-best-practices
description: Best practices for React applications built with Vite. Use when writing, reviewing, or refactoring React apps that use Vite, including project setup, `vite.config.*`, plugins, environment variables, assets, dev server behavior, build optimization, and code splitting.
---

# Vite React Best Practices

Focus on the Vite toolchain and its impact on React code. Prioritize fast dev feedback, correct environment usage, predictable builds, and clean boundaries between Vite config and app code.

## Quick Workflow

1. Confirm the project is Vite-based (`vite.config.*`, `@vitejs/plugin-react`, `import.meta.env`).
2. Review `vite.config.*` first for build and dev-server behavior.
3. Keep app code agnostic of Vite where possible; isolate Vite specifics to config and tooling.

## Project Setup and Plugins

- Use `@vitejs/plugin-react` for React Fast Refresh and JSX transform.
- Prefer a single config file (`vite.config.ts` or `vite.config.js`) and wrap config with `defineConfig`.
- Only add plugins that have a clear build or dev benefit; every plugin affects startup time and build output.

## Environment Variables

- Expose runtime values via `import.meta.env` with the `VITE_` prefix.
- Do not leak secrets into `VITE_` variables; treat them as client-visible.
- Keep environment access centralized (one module) when multiple files need the same values.

## Assets and Static Files

- Put truly static, unprocessed assets in `public/` and reference via `/path`.
- Import assets from `src/` for hashing and bundling (`import logoUrl from './logo.svg'`).
- Avoid runtime string-built asset paths; prefer explicit imports.

## Build Optimization

- Use dynamic `import()` for large, infrequently used features.
- Split heavy dependencies with `build.rollupOptions.output.manualChunks` when bundle analysis shows a large shared chunk.
- Set `build.target` intentionally if you can drop legacy browser support; it reduces output size.
- Prefer pre-bundling fixes in `optimizeDeps.include` only when a dependency is problematic or slow to scan.

## Dev Server and Preview

- Keep dev server config explicit when needed (`server.port`, `server.proxy`, `server.strictPort`).
- Use `preview` for production-like testing instead of relying on the dev server.

## Testing and Linting

- When tests are involved in a Vite project, prefer `vitest` for closer alignment with Vite config and module resolution.
- Ensure ESLint/TS config understands `import.meta.env` and Vite aliases.
