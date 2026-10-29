# cookie-cutter-dbx

A **metadata-driven Databricks platform**: DataOps, MLOps and AIOps built from declarative dataflow specs, with every table,
product, model and endpoint described in [Ontos](https://github.com/databrickslabs/ontos)-ready metadata and published to one shared
database. This repository was generated from the [cookiecutter-dbxops](https://github.com/NitinKeshavB/cookiecutter-dbxops) template; squads are onboarded through a registry.

> **Status.** Everything is verified offline (tests, the Databricks CLI's own bundle schema, the Lakeflow Framework's own validator,
> `actionlint`, `shellcheck`). **Nothing has run against a real workspace, database, GitHub organisation or Ontos instance.**
> Read [`context/06_Operations/Known_Gaps.md`](context/06_Operations/Known_Gaps.md) before relying on it, and
> [`Prerequisites_Runbook.md`](context/06_Operations/Prerequisites_Runbook.md) for what must exist first.

## What you get

| | |
|---|---|
| **Pipelines** | dataflow specs run by the [Lakeflow Framework](https://github.com/databricks-solutions/lakeflow_framework), pinned as a read-only submodule (v0.24.1) with an overlay of our own |
| **Example bundles** | `bundles/dataops` (synthetic insurance data to bronze, silver, gold), `bundles/mlops` (features, training, gate, scoring, serving), `bundles/aiops` (document parsing, vector index, agent, evaluation) |
| **Metadata** | an ODCS v3.1.0 contract per table (generated from the specs and kept in step), ODPS v1.0.0 products, a three-level domain taxonomy, a SKOS glossary, an Ontos export |
| **Metadata database** | `platform_meta`: versioned definitions, deployments, runs, row counts, quality results and lineage; PostgreSQL, Lakebase and Unity Catalog backends |
| **Onboarding** | a registry repository scaffold: issue form, validation, a pull request as the approval, then a provisioned repository with environments and rulesets |
| **CI/CD** | GitHub Actions for checks, per-target deployment with environment approvals, and a weekly framework-upgrade check |
| **Cost and performance** | provenance tags (project, repo, commit) and a budget policy on every job and pipeline, daily cost per project from `system.billing`, per-run performance, a tuning advisor, an ingestion-method recommender (`make recommend`) and table defaults (`conf/table_defaults.yml`) |
| **Owner insights** | `platform_insights` views over `platform_meta` and the system tables: job and pipeline health, cost, access review, table usage and one ranked list of recommendations (`python -m dbxops.insights`) |
| **The library** | `dbxops` (public API in `src/dbxops/__init__.py`) and the `dbxops-publish-run`, `dbxops-publish-costs` and `dbxops-deploy-insights` job commands; loguru JSON run logging (`dbxops.runlog`) |

## Concepts in one minute

- A table's **data domain** is a path through the taxonomy: `claims__litigation__claim_litigation` is Business Domain *Claims* >
  Sub-domain *Litigation* > Data Domain *Claim Litigation*, so a table always shows what it rolls up to.
- Names carry the project: schemas `<project_id>_<layer>` (`bronze`, `silver`, `gold`, `ml`, `ai`), contracts `dc_<project_id>_<layer>_<table>`,
  products `dp_<project_id>_<name>`.
- Specs are the source of truth for a table's columns and quality rules; `make contracts` regenerates those parts of the contract and keeps the
  human-written parts (descriptions, classification, semantic links).
- Anything not known yet is written `PLACEHOLDER-<what>`; deployment refuses to run while any remain.
- Git is the truth; `platform_meta` is a versioned read model of it. See [`context/03_Architecture/`](context/03_Architecture/).

## Start building

You have just run `cookiecutter` and have a new directory with a git repository in it. In order:

1. **Look at the framework.** `git submodule status` should show `framework/lakeflow_framework` at the commit in `framework/UPSTREAM.lock`. If the
   generator printed a warning (no network?), run `bash scripts/add_framework.sh`.
2. **Commit the starting point.** `git add -A && git commit -m "Initial commit from cookiecutter-dbxops"`. Work on a branch after this:
   `make lint` fails while `main` is checked out (the `no-commit-to-branch` hook); CI uses `make lint-ci`.
3. **Install and prove it works.** `make install`, then `make validate-metadata` and `make test`. They pass on a fresh repository.
4. **Fill in what is not known yet.** `make check-placeholders` lists every `PLACEHOLDER-<what>`: workspace host, tenant, catalogs, owner
   emails, service principal, cost centre, region. Deployments refuse to run while any remain.
5. **Build.** Add a bundle with `make new-bundle NAME=<name> DOMAIN=<data-domain-path>`, or read the three example bundles first. If you
   generated with `include_examples=no` there are none yet, and `bundles/` is empty.
6. **Register the squad.** The generated `metadata/_platform/` is a stand-in made from your answers. The registry (see below) is the owner of the real
   one; once your registration is approved, take its snapshot instead of editing these files.

## Develop

Requirements: `make`, Python 3.11+, `git`, and for the offline bundle checks the [Databricks CLI](https://docs.databricks.com/dev-tools/cli/) (tests skip what is not installed).

```bash
git clone --recurse-submodules <this repo>
make install              # editable install with every extra
make test                 # pytest + coverage (the integration marker needs DBXOPS_TEST_PG_URL)
make lint                 # pre-commit: black, isort, flake8, pylint, mypy, autoflake
make validate-metadata    # contracts, products, domains, specs, snapshots, semver
```

Everyday tasks (`make help` lists the rest; all are documented in [`CLAUDE.md`](CLAUDE.md) §9):

| Task | Command |
|---|---|
| Add a bundle | `make new-bundle NAME=<n> DOMAIN=<data-domain-path>` |
| Regenerate contracts / per-target config | `make contracts`, `make sync-substitutions` (add `CHECK=1` to only report) |
| What still needs filling in | `make check-placeholders`, `make onboard-check` |
| Ontos files | `make export-ontos TARGET=dev` |
| Move the framework pin | `make check-upstream`, `make upgrade-lakeflow TAG=v0.25.0` |

## Onboarding a squad

Squads register through the **registry** (scaffolded in [`templates/registry-repo`](templates/registry-repo/)): an issue form captures the squad,
its owners (technical, data, executive, steward, support), AD groups, data domain, service principal and tags; a pull request is the approval;
provisioning creates the registration and its `metadata/_platform/` snapshots. A repository generated from the template starts with an equivalent
stand-in, so everything runs offline from the first commit. Details: [`Project_Onboarding.md`](context/06_Operations/Project_Onboarding.md).

## Environments and deployment

Five targets (`dev`, `test`, `uat`, `preprod`, `prod`), one catalog each, defined in `conf/environments.yml`. A merge to `main` deploys dev then test; a version
tag promotes through uat, preprod and prod behind GitHub environment reviewers. The platform team deploys the framework itself once per target.
Details: [`Environments_And_Deploy.md`](context/06_Operations/Environments_And_Deploy.md), [`Upgrading_Lakeflow.md`](context/06_Operations/Upgrading_Lakeflow.md).

## Continuous integration and releases

`.github/workflows/ci.yml` runs on pull requests and pushes to `main` (job `checks`): the framework pin, lint, tests, metadata, contract and
substitution drift, specs, vendored skills, the registry scaffold and framework staging.
`.github/workflows/build-test-publish.yml` tests the package across Python 3.11 to 3.13, builds a wheel and tests the installed wheel; releases are tag-driven
(a tag matching `version.txt`): TestPyPI first, then PyPI. Add `TEST_PYPI_TOKEN` and `PROD_PYPI_TOKEN` under *Settings > Secrets and variables > Actions*; to
require approval for a production release, create a GitHub Environment named `pypi` with a required reviewer.

## AI agent harness

This project ships a [Claude Code](https://claude.com/claude-code) harness: rules, commands and skills that make an agent investigate before it acts,
cite `file:line` for claims, keep changes surgical, and prove a change by running something.

| Path | Purpose |
|---|---|
| `CLAUDE.md` | the operational doctrine, read first |
| `.claude/commands/` | slash commands |
| `.claude/skills/` | the project's skills, plus vendored Databricks, MLflow and framework skills locked in `VENDORED.json` |
| `context/` | the spec layer: overview, glossary (binding), architecture, spec reference, operations |
| `LOCAL-MEMORY/` | where analyses, plans and lessons are written |

| Command | Use it for |
|---|---|
| `/qa` | run lint and tests and triage failures |
| `/explain <target>` · `/bug-why <error>` · `/bug-fix <analysis>` | understand, diagnose, fix (failing test first) |
| `/howto-implement <feature>` · `/implement <plan>` | plan before code, then a surgical change |
| `/verify [target]` | adversarially check a change, plan or diagnosis |
| `/new-bundle <name> <domain>` · `/upgrade-lakeflow [tag]` · `/onboard-check` | platform tasks |
| `/databricks-doctor` · `/databricks-setup` | vendored Databricks health check and CLI auth setup |

Project skills: `metadata-ontology`, `dataops-design`, `mlops-design`, `aiops-design`. The global Databricks plugin is disabled for this project
(`.claude/settings.json`); its skills are vendored so they are versioned with the code. Never run with an auto-selected Databricks profile.

Adapted from the Claude Code Agent Harness by Viacheslav Tronko (MIT); see `HARNESS-ATTRIBUTION.md`. Third-party licences: `THIRD_PARTY/`.
