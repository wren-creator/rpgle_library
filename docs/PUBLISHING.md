# Publishing Guide

This library publishes three ways on every version tag:
- **npmjs.org** — public, installable with `npm install rpgle_library`
- **Go module proxy** — installable with `go get github.com/wren-creator/rpgle_library/go`, driven entirely by git tags (no separate publish step)
- **PyPI** — public, installable with `pip install rpgle_library`

---

## One-time Setup

### 1. npm Token

1. Log in at [npmjs.com](https://www.npmjs.com) and go to **Access Tokens**
2. Create a new **Automation** token
3. In your GitHub repo → **Settings → Secrets and variables → Actions**
4. Create a secret named `NPM_TOKEN` with that value

### 2. PyPI Token

1. Log in at [pypi.org](https://pypi.org) and go to **Account Settings → API tokens**
2. Create a new token scoped to the `rpgle_library` project (or account-wide, for the first release before the project exists on PyPI)
3. In your GitHub repo → **Settings → Secrets and variables → Actions**
4. Create a secret named `PYPI_TOKEN` with that value

### 3. Confirm placeholders

`package.json`, `go/go.mod`, and `python/pyproject.toml` already point at `github.com/wren-creator/rpgle_library`. If you're publishing under a different GitHub account or org, update all three — and keep `rpgle_library` as the literal name everywhere (package.json `name`, the repo folder, the Go module path, and pyproject.toml's `name`) rather than letting them drift into different spellings.

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

The GitHub Actions workflow (`.github/workflows/ci.yml`) runs `npm ci` and `npm test` on Node 22 and 24 (the install brings in `better-sqlite3` for the DISK file tests), `go test` and `go vet` for the Go module, and the Python tests (`python -m unittest discover -s tests`) on Python 3.12, 3.13 and 3.14, on every push and PR. The publish step (npm) is included in the same file but commented out until the `NPM_TOKEN` secret above is configured; uncomment it once you're ready to publish automatically on tag push.

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

## Releasing a New Version (Python)

```bash
# 1. Update the version in python/pyproject.toml (it leads JS and Go until they catch up: see "Versions today" below)

# 2. Build and upload
cd python
python -m pip install --upgrade build twine
python -m build
python -m twine upload dist/*
```

Like the npm publish step, this is left as a manual command for now — the GitHub Actions workflow doesn't run it automatically until a `python-publish` job is uncommented in `.github/workflows/ci.yml` (mirroring the currently-commented `npm-publish` job, gated the same way on the `PYPI_TOKEN` secret above).

## Manual Publishing (npm, if needed)

```bash
npm publish --access public
```

---

## The SQLite dependency

The core of every port has no dependencies, and `DISK` files are the one exception, kept out of the way on purpose:

- **npm:** `better-sqlite3` is an *optional peer dependency* (`peerDependencies` plus `peerDependenciesMeta`), so `npm install rpgle_library` installs nothing extra. The code loads it with `require` the first time a `Database` is created, and says so plainly if it is missing. It is also a dev dependency, so `npm ci` has what the tests need. `better-sqlite3` 12 supports Node 20 to 25 (13 needs Node 22), so `>=11.0.0` is the declared range and DISK files need Node 20 or newer, while `engines` still says Node 16 for the core.
- **Go:** the SQLite code is the `sqlitedb` subpackage, so only programs that import it compile `modernc.org/sqlite`. It is pinned to v1.34.5, the newest release whose `go.mod` still says Go 1.21; later ones need Go 1.23 or newer, so raise the Go floor and the pin together.
- **Python:** `sqlite3` is in the standard library.

When the dependency needs to move, check `tools/parity/check.py` and both DISK test files (`test/database.test.js`, `go/database_test.go`) first.

## Versioning Policy

This library follows [Semantic Versioning](https://semver.org/):

- **Patch** — bug fixes, documentation improvements, additional glossary/reference content
- **Minor** — new opcodes, new DDS keyword support, new examples, new API functions
- **Major** — breaking changes to the public API (function signatures, the EXFMT yield/resume shape, exports)

The JS, Go, and Python implementations are meant to behave identically for any input all three support. A behavior change in one that isn't mirrored in the others should be treated as a bug, not shipped as a version bump in only one language.

**Where that stands today:** the three ports agree on every case in `tools/parity/cases.json`, and CI checks it. What differs is features: the Python port has a full runtime (free-form RPG, calls, printing, arrays) that JS and Go don't have yet (DISK files they now share), and the root README's "Status by language" says exactly which. A feature that exists in only some ports is fine; the same input giving different answers is not.

### Checking port parity

```bash
python3 tools/parity/check.py            # exits 1 if any case differs
python3 tools/parity/check.py --verbose  # also lists the cases that agree
```

It needs `python3`, `node` and `go` on the PATH. Each port has a small runner (`run_py.py`, `run_js.js`, and `go/parity_dump_test.go`, which does nothing during a normal `go test`) that reads `tools/parity/cases.json` and writes what it got; `check.py` compares them. Error *messages* may differ between ports; whether something is an error, and every value that isn't, may not.

**When you change a behavior in one port, add a case to `cases.json` first** and watch the check fail in the ports you haven't changed yet. That failure is the point: it is how a behavior difference gets found instead of shipped. When you port a feature, add its cases the same way.

**Versions today.** The Python port is at **1.1.0**: it gained a great deal (new opcodes, DDS support and API), a minor bump under the policy above. The JS and Go ports are still **1.0.0** but have unreleased behavior fixes and, now, DISK files (see the changelog). New API that doesn't break anything is a minor bump, so publish them as **1.1.0** (the Go module is released by tag, `v1.1.0`). **Python versions:** `requires-python` is `>=3.12`; the test suite and CI run on 3.12, 3.13 and 3.14, and nothing older has been tested.
