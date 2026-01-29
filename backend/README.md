# Backend

The AlvaOS REST API server.

## Purpose

- Provides a versioned API for the Web UI
- Handles all system operations (storage, Docker, users, networking)
- Never exposes shell commands directly to clients
- Manages configuration and state

## Technology

Recommendation: **Go** (simple, single binary, good for system tooling)

Alternative: Python (easier to prototype)

## Structure

```
backend/
├── api/         # API routes and handlers
├── services/    # Business logic (storage, docker, backup)
├── models/      # Data models
└── cmd/         # Entry point
```
