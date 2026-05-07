# Odoo Migration Tool — Design Spec

## Overview

Redesign `migrate.py` to be a YAML-driven, single-hop migration tool that supports chained execution for pre-validated migration sequences.

**Goal:** Simplify migration management by having one YAML file per hop, per project folder. Support both single-hop manual testing and auto-chain for production migration day.

---

## Project Structure

```
Odoo_Migration/
  migrate.py                   # Main CLI tool (redesigned)
  migration_config.yaml        # Global config (Odoo paths, defaults)
  docs/
    superpowers/
      specs/
        2025-05-07-odoo-migration-redesign.md  # This spec

<project>/
  <project>_<from>to<to>.yaml  # One hop per YAML
  <project>_<from>to<to>.yaml.validated  # Marker after success
  custom_cleanup.py            # Optional per-project cleanup
  logs/                        # Per-hop logs
  reports/                     # Per-hop reports
  backups/                      # Database backups

Example:
BSM/
  BSM_13to14.yaml
  BSM_13to14.yaml.validated
  BSM_14to15.yaml
  BSM_14to15.yaml.validated
  custom_cleanup.py
  logs/
  reports/
  backups/
```

---

## CLI Interface

### Single Hop (Testing/Validation)

```bash
python migrate.py BSM/BSM_13to14.yaml
python migrate.py BSM/BSM_13to14.yaml --mark-validated   # Manual validation
```

### Chain (Migration Day)

```bash
python migrate.py BSM/ --chain              # Run all hops in sequence
python migrate.py BSM/ --chain --force      # Skip validation check
```

### Backup Options

```bash
python migrate.py BSM/BSM_13to14.yaml --backup           # Default: backup before migrate
python migrate.py BSM/BSM_13to14.yaml --no-backup        # Skip backup
python migrate.py BSM/ --chain --backup                  # Backup before first hop
```

---

## Single-Hop Flow

1. **Validate YAML** — syntax, required fields, paths exist
2. **Check requirements** — `psql` available, Odoo/OpenUpgrade paths valid
3. **Backup** — Create `{source_db}_backup_{timestamp}` (unless `--no-backup`)
4. **Prepare database** — `dropdb --if-exists <target>`, then `createdb -T <source> <target>`
5. **Run cleanup** — Execute custom cleanup script if defined in YAML
6. **Uninstall modules** — Odoo shell to uninstall listed modules
7. **Run OpenUpgrade** — `odoo-bin -u all --stop-after-init` with `OPENUPGRADE_TARGET_VERSION`
8. **On success** — Create `.validated` marker file
9. **Generate report** — Markdown report in `reports/` with status, log path, timestamps

---

## Chain Flow

1. **Detect hops** — Find all `*.yaml` in folder, exclude `.validated` sidecars. Filename must match pattern `<project>_<X>to<Y>.yaml` where X and Y are version numbers (e.g., `13`, `17`). Invalid filenames are skipped with warning.
2. **Sort hops** — Sort by `from_version` ascending (numeric, not string). Example: 13→14, 14→15, 16→17, 17→18.
3. **Validate markers** — All hops must have `.validated` marker (unless `--force`). Missing markers listed in error output.
4. **Run sequentially** — Execute each hop, log to separate per-hop log file
5. **Stop on failure** — If any hop fails, stop and report which hop failed and error details
6. **Report** — Summary of all hops attempted, success/failure per hop

---

## Validation Marker

After each successful hop execution, tool creates sidecar file:
```
BSM_13to14.yaml.validated
```

Contains:
```
timestamp: 2025-05-07T14:30:22
source: BSM13
target: BSM14
success: true
backup: BSM13_backup_2025-05-07_143022
```

Chain execution checks for this file. If missing and not `--force`, abort with error listing unvalidated hops.

---

## YAML Schema

```yaml
job:
  name: "BSM 13→14"              # Human-readable name
  from_version: "13.0"            # Source Odoo version
  to_version: "14.0"              # Target Odoo version

database:
  source_name: "BSM13"            # Source database name
  target_name: "BSM14"             # Target database name
  admin_user: "admin.synercatalyst"  # Odoo admin user (from .env)
  # admin_password: from .env (ODOO_DB_PASS)
  modules_to_remove:              # Modules to uninstall before migration
    - module_a
    - module_b
  custom_cleanup_script: "BSM/custom_cleanup.py"  # Optional

paths:
  source_odoo: "/opt/PW/PW.3.0"   # Source Odoo binary path
  target_odoo: "/opt/PW/PW.4.0"  # Target Odoo binary path
  openupgrade: "/opt/PW/OpenUpgrade_14.0"  # OpenUpgrade scripts path
  config_file: "odoo.conf"        # Config filename in Odoo path
```

**Note:** `target_version` in `paths` (old config) replaced by `job.to_version`. Backup name auto-computed from `source_name`.

---

## Credentials Management

Credentials are stored in `.env` files, NOT in YAML files. This prevents accidental commit of sensitive data.

### .env File Structure

```bash
# Odoo_Migration/.env (gitignored)
ODOO_DB_USER=admin.synercatalyst
ODOO_DB_PASS=your_password_here
```

### Priority Order

| Source | admin_user | admin_password |
|--------|-----------|----------------|
| Environment variable | `ODOO_DB_USER` | `ODOO_DB_PASS` |
| `.env` file | `ODOO_DB_USER` | `ODOO_DB_PASS` |
| YAML file | `database.admin_user` | `database.admin_password` |

### Project-Specific Credentials

Each project folder can have its own `.env` file:
```
BSM/
  .env                    ← BSM-specific credentials
  BSM_13to14.yaml
  custom_cleanup.py
```

`.env` files are gitignored and must never be committed.

---

## Backup Naming

Auto-computed: `{source_db}_backup_{YYYY-MM-DD_HHMMSS}`

Example: `BSM13_backup_2025-05-07_143022`

Backups stored in `<project>/backups/` directory.

---

## Error Handling

- **YAML syntax error** → Exit with message pointing to invalid YAML
- **Missing paths** → Exit with list of missing paths
- **Backup failure** → Exit before any DB changes
- **OpenUpgrade failure** → Log error, leave target DB in failed state, report path to logs
- **Chain: unvalidated hop** → Stop and list which hops lack `.validated` marker
- **Chain: hop failure** → Stop chain, report which hop failed with summary

---

## Report Format

Generated at `reports/{project}_{from}to{to}_{timestamp}.md`:

```markdown
# Migration Report: BSM 13→14

- **Date:** 2025-05-07 14:30:22
- **Source:** BSM13 (13.0)
- **Target:** BSM14 (14.0)
- **Status:** SUCCESS
- **Backup:** BSM13_backup_2025-05-07_143022
- **Log:** logs/BSM_13to14_2025-05-07_143022.log

## Modules Uninstalled
- module_a
- module_b

## Environment
- **Source Odoo:** /opt/PW/PW.3.0
- **Target Odoo:** /opt/PW/PW.4.0
- **OpenUpgrade:** /opt/PW/OpenUpgrade_14.0

## Notes
See log file for full trace.
```

---

## Implementation Notes

- Tool reads global config from `migration_config.yaml` for defaults (paths, etc.)
- Per-project YAML overrides global config
- All logging: file + stdout simultaneously
- Real-time output streaming from subprocess
- Version sorting: parse `X.Y` from YAML filename pattern `<project>_<X>to<Y>.yaml`