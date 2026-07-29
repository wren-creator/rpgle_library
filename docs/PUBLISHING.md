# Publishing Guide

This library publishes two ways on every version tag:
- **npmjs.org** — public, installable with `npm install rpgle_library`
- **Go module proxy** — installable with `go get github.com/wren-creator/rpgle_library/go`, driven entirely by git tags (no separate publish step)

---

## One-time Setup

### 1. npm Token

1. Log in at [npmjs.com](https://www.npmjs.com) and go to **Access Tokens**
2. Create a new **Automation** token
3. In your GitHub repo → **Settings → Secrets and variables → Actions**
4. Create a secret named `NPM_TOKEN` with that value

### 2. Confirm placeholders

`package.json` and `go/go.mod` already point at `github.com/wren-creator/rpgle_library`. If you're publishing under a different GitHub account or org, update both — and keep `rpgle_library` as the literal name everywhere (package.json `name`, the repo folder, and the Go module path) rather than letting them drift into different spellings.

---

## Releasing a New Version (JS)

```bash
# 1. Update the version in package.json
npm version patch   # 1.0.0 → 1.0.1  (bug fixes)
npm version minor   # 1.0.0 → 1.1.0  (new features, backward compatible)
npm version major   # 1.0.0 → 2.0.0  (breaking changes)

# 2. Push the commit and the generated tag
git push origin main --tags
```

The GitHub Actions workflow (`.github/workflows/ci.yml`) runs tests on Node 18/20/22 and `go test ./go/...` on every push and PR. The publish step (npm) is included in the same file but commented out until the `NPM_TOKEN` secret above is configured — uncomment it once you're ready to publish automatically on tag push.

## Releasing a New Version (Go)

Go modules don't have a separate "publish" step — the module proxy (`proxy.golang.org`) picks up new versions directly from git tags:

```bash
# Tag the commit — must match the JS version for a coordinated release,
# but the Go module can also be tagged independently if it needs its
# own fix that doesn't touch the JS side.
git tag v1.0.1
git push origin v1.0.1
```

Consumers then get it with:

```bash
go get github.com/wren-creator/rpgle_library/go@v1.0.1
```

No credentials or CI step are required for this — the proxy fetches directly from the public git repo the first time anyone requests that tag.

---

## Manual Publishing (npm, if needed)

```bash
npm publish --access public
```

---

## Versioning Policy

This library follows [Semantic Versioning](https://semver.org/):

- **Patch** — bug fixes, documentation improvements, additional glossary/reference content
- **Minor** — new opcodes, new DDS keyword support, new examples, new API functions
- **Major** — breaking changes to the public API (function signatures, the EXFMT yield/resume shape, exports)

The JS and Go implementations are meant to behave identically for any input both support. A behavior change in one that isn't mirrored in the other should be treated as a bug, not shipped as a version bump in only one language.
