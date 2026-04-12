# AGENTS.md

## General

- Keep changes small and focused
- Follow existing patterns
- Do not introduce new tools or dependencies without justification
- Update tests when behavior changes

## Python Agents

### Tooling

- **Package manager:** `uv`
- **Formatter:** `black` (default settings)

### Code Rules

- Use **type hints** for all code
- Use the standard `logging` module (no `print()`)
- Prefer explicit error handling

### Testing

- Use `pytest`

## Frontend Agents

### Tooling

- **Package manager:** `pnpm`
- **Build tool:** `Vite`
- **UI:** `shadcn/ui`

### Language & Style

- **TypeScript:** `strict: true`
- No `any` unless explicitly justified
- Single quotes
- No semicolons

### Patterns

- Functional components and hooks only
- Prefer functional and immutable patterns
- Avoid class-based React components

## Version Control

- Clear, descriptive commit messages
- Do not commit secrets or build artifacts
