# Contributing to AlvaOS

Thank you for your interest in contributing to AlvaOS!

## Project Philosophy

Before diving in, please understand our core values:

- **Simplicity over complexity** - Avoid overengineering
- **Stability over features** - Conservative, proven technology
- **Transparency** - Easy to understand, audit, and modify
- **No magic** - Explicit is better than implicit

## Getting Started

### 1. Understand the Vision

Read the main [README.md](../README.md) to understand what AlvaOS is trying to achieve.

### 2. Explore the Structure

Review [docs/STRUCTURE.md](STRUCTURE.md) to understand how the repository is organized.

### 3. Pick an Area

Choose where you want to contribute:

- **Backend** - REST API, system management (Go or Python)
- **Frontend** - Web UI (Svelte/Vue)
- **Installer** - Build system improvements
- **Scripts** - Post-install automation
- **Documentation** - User guides, API docs

## Development Workflow

1. **Fork** the repository
2. **Create a branch** for your feature or fix
3. **Make your changes**
   - Follow existing code style
   - Keep commits focused and well-described
   - Test your changes
4. **Submit a Pull Request**
   - Describe what you changed and why
   - Reference any related issues

## Code Guidelines

### Backend
- Use clear, descriptive names
- Handle errors explicitly
- Log important operations
- Add unit tests for business logic

### Frontend
- Keep components simple and reusable
- Use the design system (when established)
- Test in both light and dark modes
- Ensure responsive design

### Scripts
- Make scripts idempotent (safe to run multiple times)
- Use clear error messages
- Log operations for debugging
- Document expected inputs/outputs

### Documentation
- Use clear, simple language
- Include examples where helpful
- Keep docs up to date with code changes

## Testing

Currently:
- Manual testing during early development

Future:
- Unit tests for backend
- Integration tests for installer
- E2E tests for Web UI

## Questions?

- **Open an issue** for bugs or feature requests
- **Start a discussion** for questions or ideas
- **Submit a PR** for improvements

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.

---

**Thank you for helping make AlvaOS better!** 🚀
