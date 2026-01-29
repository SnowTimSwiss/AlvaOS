# AlvaOS Release Process

This document describes how to create and publish a new AlvaOS release.

## Version Numbering

AlvaOS follows **Semantic Versioning 2.0.0** (semver):

```
MAJOR.MINOR.PATCH
```

- **MAJOR** - Incompatible API changes or major architectural changes
- **MINOR** - New features, backward compatible
- **PATCH** - Bug fixes, backward compatible

**Examples:**
- `v1.0.0` - First stable release
- `v1.1.0` - Added new feature (e.g. multi-user support)
- `v1.1.1` - Fixed bug in storage management
- `v2.0.0` - Breaking change (e.g. new API version)

**Pre-release versions:**
- `v1.0.0-alpha.1` - Early testing
- `v1.0.0-beta.1` - Feature complete, testing
- `v1.0.0-rc.1` - Release candidate

## Release Channels

### Stable (default)
- Production-ready releases
- Recommended for all users
- Tags: `v1.0.0`, `v1.1.0`, etc.
- Thoroughly tested

### Beta
- Preview of upcoming features
- For testing and feedback
- Tags: `v1.1.0-beta.1`
- May have minor issues

### Dev
- Latest code from `main` branch
- Unstable, not recommended
- Built nightly (future)

## Release Checklist

### 1. Pre-Release

- [ ] All planned features implemented
- [ ] All tests passing (when test suite exists)
- [ ] Documentation updated
- [ ] CHANGELOG.md updated
- [ ] No critical bugs
- [ ] Beta tested (for major releases)

### 2. Version Bump

Update version in:
- [ ] `README.md` (if mentioned)
- [ ] `CHANGELOG.md` (add new section)
- [ ] Any hardcoded version strings

### 3. Create Release

**On GitHub:**

1. Go to **Releases** → **Draft a new release**

2. **Choose a tag:**
   - Create new tag: `v1.0.0`
   - Target: `main` branch

3. **Release title:**
   - Format: `AlvaOS v1.0.0`

4. **Description:**
   ```markdown
   ## What's New

   - Added Buddy Backup pairing
   - Improved storage pool management
   - Fixed Docker container restart issue

   ## Upgrade Instructions

   For existing AlvaOS installations:
   1. Go to Settings → System → Updates
   2. Click "Check for Updates"
   3. Click "Update to v1.0.0"
   4. System will restart automatically

   For new installations:
   - Download `alvaos-installer-1.0.0.iso` below
   - Write to USB and boot

   ## Downloads

   - **Installer ISO**: For new installations
   - **System Package**: For manual updates

   ## Full Changelog

   See [CHANGELOG.md](CHANGELOG.md) for all changes.
   ```

5. **Publish release**

### 4. Automated Build

GitHub Actions will automatically:
1. Build `alvaos-installer-1.0.0.iso`
2. Build `alvaos-system_1.0.0_amd64.deb`
3. Generate checksums
4. Attach files to the release

**Wait for builds to complete** (usually 10-15 minutes)

### 5. Verification

Download and verify the release artifacts:

```bash
# Download files
wget https://github.com/SnowTimSwiss/AlvaOS/releases/download/v1.0.0/alvaos-installer-1.0.0.iso
wget https://github.com/SnowTimSwiss/AlvaOS/releases/download/v1.0.0/checksums.txt

# Verify checksum
sha256sum -c checksums.txt

# Test installer in VM
qemu-system-x86_64 -cdrom alvaos-installer-1.0.0.iso -m 2048 -boot d
```

### 6. Post-Release

- [ ] Announce release (GitHub Discussions, Reddit, etc.)
- [ ] Update documentation website (if exists)
- [ ] Monitor for critical bugs
- [ ] Prepare hotfix if needed

## Release Artifacts

Each release includes:

### 1. Installer ISO
- **File**: `alvaos-installer-{version}.iso`
- **Purpose**: Fresh installations
- **Size**: ~400-500 MB
- **Bootable**: BIOS + UEFI

### 2. System Package
- **File**: `alvaos-system_{version}_amd64.deb`
- **Purpose**: Updates for existing installations
- **Size**: ~50-100 MB
- **Contains**: Backend + Frontend + Scripts

### 3. Checksums
- **File**: `checksums.txt`
- **Content**: SHA256 hashes of all files
- **Purpose**: Verify download integrity

## Hotfix Releases

For critical bugs in production:

1. **Branch from release tag:**
   ```bash
   git checkout -b hotfix/1.0.1 v1.0.0
   ```

2. **Fix the bug**
   - Keep changes minimal
   - Only fix critical issue

3. **Create hotfix release:**
   - Tag: `v1.0.1`
   - Fast-track through testing
   - Publish ASAP

4. **Merge back:**
   ```bash
   git checkout main
   git merge hotfix/1.0.1
   ```

## Beta Testing Process

Before major releases (`v1.0.0`, `v2.0.0`):

### 1. Create Beta Release
- Tag: `v1.0.0-beta.1`
- Mark as "Pre-release" on GitHub
- Announce in community

### 2. Testing Period
- Duration: 1-2 weeks
- Gather feedback
- Fix reported issues

### 3. Release Candidate
- Tag: `v1.0.0-rc.1`
- Final testing
- No new features

### 4. Stable Release
- Tag: `v1.0.0`
- Production ready

## Communication

### Release Announcement Template

```markdown
# AlvaOS v1.0.0 Released! 🎉

We're excited to announce AlvaOS v1.0.0, the first stable release!

## Highlights

- ✨ Full Buddy Backup support
- 🗄️ Btrfs storage pool management
- 🐳 Docker app store with 50+ apps
- 🎨 Beautiful dark-mode Web UI

## Download

👉 [Download AlvaOS v1.0.0](https://github.com/SnowTimSwiss/AlvaOS/releases/tag/v1.0.0)

## Upgrade

Existing users can update via:
Settings → System → Updates

## What's Next

We're already working on v1.1.0 with exciting features like...

## Thanks

Thank you to everyone who contributed and tested!

---

Questions? Open an issue or discussion on GitHub.
```

## Rollback Plan

If a release has critical issues:

1. **Mark release as "Pre-release"** on GitHub
2. **Pin previous stable version** in README
3. **Fix issue** in new release
4. **Re-release** with bumped version

Users can downgrade manually:
```bash
sudo apt install alvaos-system=1.0.0
sudo systemctl restart alvaos-backend
```

## Long-Term Support (Future)

Once AlvaOS matures:

- **LTS releases** - Every 2 years, supported for 2+ years
- **Regular releases** - Every 3-6 months, supported for 6 months

Example:
- `v2.0 LTS` - Released 2026, supported until 2028
- `v2.1` - Released 2026, new features
- `v2.2` - Released 2027, new features
- `v3.0 LTS` - Released 2028, next LTS

---

**Philosophy:** Releases should be predictable, well-tested, and easy to upgrade. Users should never fear updating their NAS.
