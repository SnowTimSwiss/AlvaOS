# AlvaOS Quick Reference

One-page reference for the AlvaOS project.

## 📦 Release Artifacts

Each GitHub Release includes:

| File | Purpose | Size |
|------|---------|------|
| `alvaos-installer-{version}.iso` | Fresh installations | ~500 MB |
| `alvaos-system_{version}_amd64.deb` | System updates | ~100 MB |
| `checksums.txt` | File verification | < 1 KB |

## 🔄 Update Flow

```
User clicks "Update" in Web UI
           ↓
Backend checks GitHub Releases API
           ↓
Downloads .deb package
           ↓
Verifies SHA256 checksum
           ↓
Installs via apt
           ↓
Restarts services
           ↓
Health check (30s timeout)
           ↓
    ┌─────┴─────┐
    ↓           ↓
 Success    Auto-rollback
```

## 🗂️ File Locations

| Path | Contents |
|------|----------|
| `/opt/alvaos/` | Binaries and webui |
| `/etc/alvaos/` | Configuration files |
| `/var/lib/alvaos/` | State and databases |
| `/srv/` | User data and shares |
| `/backup/` | Buddy backup data |

## 🔌 API Endpoints

Base URL: `http://nas-ip:8080/api/v1/`

| Endpoint | Purpose |
|----------|---------|
| `/storage` | Pool management |
| `/docker` | Container management |
| `/apps` | App store |
| `/backup` | Buddy Backup |
| `/system` | Users, updates, settings |
| `/health` | System health |

## 🛠️ Build Commands

**Build installer ISO:**
```bash
cd installer && sudo ./build.sh
```

**Build system package:**
```bash
cd scripts/package && sudo ./build-deb.sh
```

**Create release:**
1. Go to GitHub → Releases → Draft new release
2. Tag: `v1.0.0`, Target: `main`
3. Publish → CI builds everything

## 🔐 Security

- **Auth**: Local users, JWT/session
- **Network**: LAN by default, optional WireGuard
- **Backup**: WireGuard encrypted
- **Updates**: HTTPS + SHA256 verification
- **No telemetry**: Zero phone-home

## 📚 Key Documents

| Doc | What |
|-----|------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design |
| [BUDDY_BACKUP.md](BUDDY_BACKUP.md) | Backup spec |
| [UPDATE_STRATEGY.md](UPDATE_STRATEGY.md) | Update mechanism |
| [RELEASE.md](RELEASE.md) | Release process |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Developer guide |

## 💡 Design Principles

1. Stability over features
2. Simplicity over flexibility  
3. No cloud dependency
4. API-first design
5. Boring technology
6. User-controlled updates

## 🎯 Roadmap

- [x] Foundation & docs
- [ ] Backend API (Go)
- [ ] Web UI (Svelte)
- [ ] Storage management
- [ ] Docker integration
- [ ] Buddy Backup
- [ ] v1.0.0 release

## 🐛 Troubleshooting

**Build fails:**
```bash
# Clean and retry
rm -rf build/ installer/build/
sudo ./build.sh
```

**Service won't start:**
```bash
# Check logs
sudo journalctl -u alvaos-backend -f

# Restart
sudo systemctl restart alvaos-backend
```

**Rollback update:**
```bash
sudo apt install alvaos-system=1.0.0
sudo systemctl restart alvaos-backend
```

## 🔗 Links

- **Repo**: https://github.com/SnowTimSwiss/AlvaOS
- **Releases**: https://github.com/SnowTimSwiss/AlvaOS/releases
- **Issues**: https://github.com/SnowTimSwiss/AlvaOS/issues

---

**Philosophy:** Simple storage. Simple apps. Simple backups.
