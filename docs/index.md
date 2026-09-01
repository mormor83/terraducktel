# Terraducktel

Self-hosted Terraform and Helm orchestration with a human approval gate.

Terraducktel imports modules from a Git repository and runs them through a
consistent, audited pipeline — `plan → policy scan → cost estimate → human
approval → apply` — against your own cloud accounts. It runs on Docker Compose,
costs nothing to license, and depends on no SaaS.

[Back to the project site](../){ .md-button }
[Source on GitHub](https://github.com/mormor83/terraducktel){ .md-button }

## Where to start

<div class="grid cards" markdown>

-   **[Getting started](ONBOARDING.md)**

    Stand up a local stack, connect a Git repository, import your first
    workspace and take it through a run.

-   **[The `tdt` CLI](CLI.md)**

    Drive workspaces and runs from a terminal, CI, or an AI agent — with
    approval gates you can enforce rather than remember.

-   **[Architecture](ARCHITECTURE.md)**

    Services, the run lifecycle, state handling, multi-tenancy and where the
    trust boundaries sit.

-   **[API reference](API.md)**

    Every HTTP endpoint, with auth, scoping and payloads.

</div>

## The idea

Most Terraform automation makes one of two trades: run everything by hand and
accept that it drifts and nobody can audit it, or fully automate and accept that
a bad plan applies itself at 3am.

Terraducktel keeps the pipeline automated and the *decision* manual. Every apply
stops at an approval gate showing exactly what will change. What differs from a
CI job that pauses is that the pause is a first-class object: it has a diff, a
policy result, a cost estimate, an audit trail, and an operator who owns it.

The [CLI](CLI.md) extends that to unattended callers by turning the review into
a condition — `--max-destroy 0` refuses a plan that would delete anything,
rather than trusting whoever is watching to notice.

## Operations

- [Disaster recovery runbook](RUNBOOK-disaster-recovery.md)
- [Security policy](https://github.com/mormor83/terraducktel/blob/main/SECURITY.md)
- [Contributing](https://github.com/mormor83/terraducktel/blob/main/CONTRIBUTING.md)
