# Releasing to PyPI

capsule is published to PyPI as **`capscorp`**. The distribution name differs from
the repository (`capsule-corp`) and from the command (`capsule`); only the command is
something you type regularly.

Releases use **trusted publishing**, so no API token is ever created or stored. PyPI
verifies the release came from this repository's workflow via OpenID Connect.

## One-time setup

You only do this once. Steps 1–4 are on the PyPI website.

### 1. Sign in to PyPI

<https://pypi.org/account/login/> — create an account if you don't have one. Two-factor
authentication is mandatory for publishing, so enable it if prompted.

### 2. Open the pending-publisher form

Go to <https://pypi.org/manage/account/publishing/>.

Because `capscorp` does not exist on PyPI yet, use the **"Add a new pending
publisher"** section at the bottom of that page — not the per-project form, which only
appears once a project exists. A pending publisher reserves the name and lets the very
first upload create the project.

### 3. Fill in exactly these values

| Field             | Value          |
| ----------------- | -------------- |
| PyPI Project Name | `capscorp`     |
| Owner             | `lenzpracher`  |
| Repository name   | `capsule-corp` |
| Workflow name     | `release.yml`  |
| Environment name  | `pypi`         |

The environment name matters: `release.yml` declares `environment: name: pypi`, and
PyPI will reject the upload if the two disagree.

Click **Add**.

### 4. (Optional) Add the GitHub environment

<https://github.com/lenzpracher/capsule-corp/settings/environments> → **New
environment** → name it `pypi`.

This is optional — the workflow works without it — but it gives you a place to add a
required reviewer later, so a release cannot go out without an explicit approval.

## Cutting a release

Everything from here is in the repository.

1. Bump the version in `pyproject.toml`:

   ```toml
   [project]
   version = "0.2.0"
   ```

2. Commit it, and tag with a matching `v` prefix:

   ```bash
   git commit -am "Release 0.2.0"
   git tag v0.2.0
   git push && git push --tags
   ```

3. The `release` workflow builds an sdist and a wheel, runs `twine check`, and
   publishes. It **fails deliberately** if the tag and the packaged version disagree,
   so a mistyped tag cannot publish something nobody can correlate with the repo.

Watch it at <https://github.com/lenzpracher/capsule-corp/actions/workflows/release.yml>.

## Afterwards

Once the first release lands, the installer picks it up automatically — it already
prefers PyPI and only falls back to git:

```bash
curl -fsSL https://lenzpracher.github.io/capsule-corp/install.sh | sh
```

To install straight from a branch instead, set `CAPSULE_CORP_REF`:

```bash
CAPSULE_CORP_REF=my-branch sh install.sh
```

## Versioning

`__version__` is read from installed package metadata, so `pyproject.toml` is the
single source of truth. This matters more than it looks: the version is recorded in
every capsule's provenance, so a version that drifts from what was actually released
makes a capsule's own record wrong.
