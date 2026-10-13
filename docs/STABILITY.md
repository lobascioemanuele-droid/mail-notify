# Stability: what 1.0 promises

ccmail follows [Semantic Versioning](https://semver.org/). From 1.0.0 the items below are the public surface: they
only change in a backwards-compatible way inside 1.x, and a breaking change needs 2.0.

## Stable (1.x)

| Surface | Promise |
|---|---|
| **Command line** | The commands and options listed in `mail-notify --help`: `init`, `on`, `off`, `status [--cwd]`, `test`, `open`, `stop`, `doctor [--network]`, `install-hooks [--autostart]`, `uninstall-hooks [--autostart]`, `install-skill`, `uninstall-skill`, `install-cli`, `uninstall-cli`, `diagnose`, `trace`, `migrate`, `daemon [--foreground]`. Exit codes: `0` success, non-zero failure. New commands and options may be added. |
| **Settings** | Existing setting names keep their meaning and their allowed values. New settings get a default that preserves the previous behaviour. A setting is removed only after one minor release in which it is ignored and documented as deprecated. |
| **Data** | The SQLite database is migrated forward automatically at start-up and never needs a manual step. Downgrading to an older minor release is not supported. Events and mails are purged after 30 days. |
| **Reply commands** | `OFF`, `ON`, `PAUSE`/`PAUSA`/`SNOOZE`/`SLEEP [minutes]` on the first line, the `Rif: ccm-…` reference and the 14-day validity. |
| **Security properties** | Loopback-only daemon, no telemetry, credentials only in the system store (announced fallback file), mail text never executed, the checks listed in [SECURITY.md](../SECURITY.md). These can be tightened, never loosened, in 1.x. |
| **Hooks** | The hook client always exits 0 and never blocks Claude Code. |

## Not covered

- **The wording and layout of mails and of the web app**: texts, colours, the HTML structure, the summary heuristics
  (`summarize.py`) and the English/Italian catalogs can change in any release.
- **The local HTTP API** under `/v1/…`: it is private to the bundled web app and the command line, protected by a
  token, and may change without notice.
- **Internal modules**: ccmail is an application, not a library. Import nothing from `ccmail.*`.
- **Platforms**: Windows is tested for real. macOS and Linux are covered by automated tests only and are marked
  *to be tested in a later release*; behaviour there can be corrected in a patch release even if that changes what
  you saw.
- **Providers other than Gmail**: the Outlook, iCloud and Yahoo presets exist but are *to be tested in a later
  release*.

## Support

Only the latest 1.x receives fixes (see [SECURITY.md](../SECURITY.md)). Python 3.10 and later; support for a Python
version is dropped only in a minor release and announced in the changelog.
