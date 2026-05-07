# Odoo Migration Tool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign `migrate.py` to be a YAML-driven, single-hop migration tool with chain execution support. One YAML per hop, per project folder. Auto-chain for pre-validated migration sequences.

**Architecture:**
- `migrate_lib.py` — Core library with all migration logic (Config, Hopper, ChainRunner, Reporter)
- `migrate.py` — CLI entry point that delegates to migrate_lib
- Reuse existing `migrate.py` backup/cleanup/openupgrade logic, refactor into library functions

**Tech Stack:** Python 3, PyYAML, subprocess (pg_dump, createdb, dropdb), argparse

---

## File Structure

```
Odoo_Migration/
  migrate.py                    # CLI entry point (refactor existing)
  migrate_lib.py                # NEW: Core library
  migration_config.yaml         # Global defaults
  BSM/
    BSM_13to14.yaml            # Per-hop config
    BSM_13to14.yaml.validated  # Marker file
    ...
```

---

## Task 1: Core Data Structures

**Files:**
- Create: `Odoo_Migration/migrate_lib.py`

```python
"""Migration library core data structures and functions."""

import yaml
import subprocess
import logging
import datetime
import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HopConfig:
    yaml_path: Path
    name: str
    from_version: str
    to_version: str
    source_db: str
    target_db: str
    modules_to_remove: list[str] = field(default_factory=list)
    custom_cleanup_script: Optional[str] = None
    source_odoo: str = ""
    target_odoo: str = ""
    openupgrade: str = ""
    config_file: str = "odoo.conf"


@dataclass
class ValidationMarker:
    yaml_path: Path
    timestamp: str
    source: str
    target: str
    success: bool
    backup: str

    @staticmethod
    def from_file(path: Path) -> Optional['ValidationMarker']:
        if not path.exists():
            return None
        data = yaml.safe_load(path) or {}
        return ValidationMarker(
            yaml_path=path.with_suffix(''),
            timestamp=data.get('timestamp', ''),
            source=data.get('source', ''),
            target=data.get('target', ''),
            success=data.get('success', False),
            backup=data.get('backup', '')
        )

    def write(self):
        path = Path(str(self.yaml_path) + '.validated')
        data = {
            'timestamp': self.timestamp,
            'source': self.source,
            'target': self.target,
            'success': self.success,
            'backup': self.backup
        }
        with open(path, 'w') as f:
            yaml.dump(data, f)


def load_hop_config(yaml_path: Path, global_config: Optional[dict] = None) -> HopConfig:
    with open(yaml_path, 'r') as f:
        data = yaml.safe_load(f)

    db = data.get('database', {})
    job = data.get('job', {})
    paths = data.get('paths', {}) or {}

    return HopConfig(
        yaml_path=yaml_path,
        name=job.get('name', yaml_path.stem),
        from_version=job.get('from_version', ''),
        to_version=job.get('to_version', ''),
        source_db=db.get('source_name', ''),
        target_db=db.get('target_name', ''),
        modules_to_remove=db.get('modules_to_remove', []),
        custom_cleanup_script=db.get('custom_cleanup_script'),
        source_odoo=paths.get('source_odoo', ''),
        target_odoo=paths.get('target_odoo', ''),
        openupgrade=paths.get('openupgrade', ''),
        config_file=paths.get('config_file', 'odoo.conf')
    )


def parse_hop_version(yaml_path: Path) -> Optional[tuple[int, int]]:
    """Parse version from filename like BSM_13to14.yaml -> (13, 14)."""
    pattern = r'_(\d+)to(\d+)\.yaml$'
    match = re.search(pattern, str(yaml_path))
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def detect_hops_in_folder(folder: Path) -> list[HopConfig]:
    """Find all valid YAML hops in folder, sorted by from_version."""
    hops = []
    for yaml_file in folder.glob('*.yaml'):
        if yaml_file.suffix == '.validated':
            continue
        if '_to_' not in yaml_file.name:
            logging.warning(f"Skipping non-hop file: {yaml_file.name}")
            continue
        try:
            config = load_hop_config(yaml_file)
            hops.append(config)
        except Exception as e:
            logging.warning(f"Failed to load {yaml_file.name}: {e}")
    hops.sort(key=lambda h: int(h.from_version.split('.')[0]))
    return hops


def check_validated_marker(config: HopConfig) -> bool:
    marker_path = Path(str(config.yaml_path) + '.validated')
    return marker_path.exists()
```

- [ ] **Step 1: Create migrate_lib.py with data structures**

Run: (self-verification by import test)

```bash
cd /Users/wsloh/PerfectWork/Odoo_Migration
python -c "from migrate_lib import HopConfig, load_hop_config, parse_hop_version; print('OK')"
```

Expected: No errors

- [ ] **Step 2: Test parse_hop_version**

```python
from pathlib import Path
from migrate_lib import parse_hop_version

result = parse_hop_version(Path('BSM_13to14.yaml'))
assert result == (13, 14), f"Expected (13, 14), got {result}"

result = parse_hop_version(Path('SEQ_16to17.yaml'))
assert result == (16, 17), f"Expected (16, 17), got {result}"

print("parse_hop_version: PASS")
```

- [ ] **Step 3: Commit**

```bash
git add Odoo_Migration/migrate_lib.py
git commit -m "feat: add core data structures for migration tool"
```

---

## Task 2: Logging and Command Execution

**Files:**
- Modify: `Odoo_Migration/migrate_lib.py`

```python
def setup_logging(log_file: Path):
    """Setup logging to file + stdout."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )


def run_command(command, env=None, cwd=None):
    """Run subprocess command, stream output to logger, return success."""
    if isinstance(command, str):
        shell = True
        cmd_display = command
    else:
        shell = False
        cmd_display = ' '.join(command)

    logging.info(f"Executing: {cmd_display}")

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=shell,
        env=env,
        cwd=cwd,
        text=True
    )

    output = []
    for line in iter(process.stdout.readline, ''):
        line = line.strip()
        if line:
            logging.info(line)
            output.append(line)

    process.wait()
    if process.returncode != 0:
        logging.error(f"Command failed with exit code {process.returncode}")
        return False, output
    return True, output
```

- [ ] **Step 1: Add logging and run_command to migrate_lib.py**

- [ ] **Step 2: Verify import works**

```bash
python -c "from migrate_lib import run_command, setup_logging; print('OK')"
```

Expected: OK

- [ ] **Step 3: Commit**

```bash
git add Odoo_Migration/migrate_lib.py
git commit -m "feat: add logging and run_command utilities"
```

---

## Task 3: Database Backup

**Files:**
- Modify: `Odoo_Migration/migrate_lib.py`

```python
def backup_database(source_db: str, backup_dir: Path, timestamp: str) -> tuple[str, Path]:
    """Create pg_dump backup of source database.

    Returns: (backup_name, backup_path)
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_name = f"{source_db}_backup_{timestamp}"
    backup_path = backup_dir / f"{backup_name}.sql"

    cmd = ['pg_dump', '-Fc', '-f', str(backup_path), source_db]
    success, output = run_command(cmd)

    if not success:
        raise RuntimeError(f"Backup failed: {output[-1] if output else 'unknown error'}")

    logging.info(f"Backup created: {backup_path}")
    return backup_name, backup_path


def create_db_clone(source_db: str, target_db: str):
    """Drop target if exists and clone from source using createdb -T."""
    run_command(['dropdb', '--if-exists', target_db])
    success, _ = run_command(['createdb', '-T', source_db, target_db])
    if not success:
        raise RuntimeError(f"Failed to create clone {target_db} from {source_db}")
    logging.info(f"Database clone created: {target_db}")
```

- [ ] **Step 1: Add backup_database and create_db_clone to migrate_lib.py**

- [ ] **Step 2: Test functions exist**

```bash
python -c "from migrate_lib import backup_database, create_db_clone; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add Odoo_Migration/migrate_lib.py
git commit -m "feat: add database backup and clone functions"
```

---

## Task 4: Module Uninstallation

**Files:**
- Modify: `Odoo_Migration/migrate_lib.py`

```python
def uninstall_modules(config: HopConfig):
    """Run Odoo shell to uninstall modules listed in config."""
    modules = config.modules_to_remove
    custom_script = config.custom_cleanup_script

    if not modules and not custom_script:
        logging.info("No cleanup tasks.")
        return

    odoo_bin = Path(config.source_odoo) / "odoo-bin"
    odoo_conf = Path(config.source_odoo) / config.config_file

    if not odoo_bin.exists():
        raise FileNotFoundError(f"Odoo binary not found: {odoo_bin}")

    custom_logic = ""
    if custom_script:
        script_path = config.yaml_path.parent.parent / custom_script
        if script_path.exists():
            with open(script_path, 'r') as f:
                custom_logic = f.read()
            logging.info(f"Loaded custom cleanup: {script_path}")
        else:
            logging.warning(f"Custom script not found: {script_path}")

    modules_str = str(modules)
    python_script = f"""
import logging
import sys
logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger('migration')

modules_to_remove = {modules_str}
for name in modules_to_remove:
    module = env['ir.module.module'].search([('name', '=', name), ('state', '=', 'installed')])
    if module:
        _logger.info(f"Uninstalling module: {{name}}")
        module.button_immediate_uninstall()
    else:
        _logger.info(f"Module {{name}} not found or not installed.")

{custom_logic}

env.cr.commit()
"""

    logging.info(f"Running Odoo shell cleanup on {config.target_db}")
    process = subprocess.Popen(
        [str(odoo_bin), "-c", str(odoo_conf), "-d", config.target_db,
         "--stop-after-init", "shell", "--no-http"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    stdout, _ = process.communicate(input=python_script)
    logging.info(stdout)

    if process.returncode != 0:
        raise RuntimeError(f"Cleanup failed for {config.target_db}")
```

- [ ] **Step 1: Add uninstall_modules to migrate_lib.py**

- [ ] **Step 2: Verify import**

```bash
python -c "from migrate_lib import uninstall_modules; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add Odoo_Migration/migrate_lib.py
git commit -m "feat: add module uninstallation via Odoo shell"
```

---

## Task 5: OpenUpgrade Execution

**Files:**
- Modify: `Odoo_Migration/migrate_lib.py`

```python
def run_openupgrade(config: HopConfig):
    """Run OpenUpgrade migration on target database."""
    odoo_bin = Path(config.target_odoo) / "odoo-bin"
    odoo_conf = Path(config.target_odoo) / config.config_file

    if not odoo_bin.exists():
        raise FileNotFoundError(f"Odoo binary not found: {odoo_bin}")

    env = os.environ.copy()
    env['OPENUPGRADE_TARGET_VERSION'] = config.to_version

    cmd = [
        str(odoo_bin),
        "-c", str(odoo_conf),
        "-d", config.target_db,
        "-u", "all",
        "--stop-after-init"
    ]

    logging.info(f"Starting OpenUpgrade to version {config.to_version}")
    success, output = run_command(cmd, env=env)

    if not success:
        raise RuntimeError(f"OpenUpgrade failed for {config.target_db}")

    logging.info(f"OpenUpgrade completed: {config.target_db}")
```

- [ ] **Step 1: Add run_openupgrade to migrate_lib.py**

- [ ] **Step 2: Verify import**

```bash
python -c "from migrate_lib import run_openupgrade; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add Odoo_Migration/migrate_lib.py
git commit -m "feat: add OpenUpgrade execution function"
```

---

## Task 6: Single-Hop Migration Runner

**Files:**
- Modify: `Odoo_Migration/migrate_lib.py`

```python
def check_requirements(config: HopConfig):
    """Check psql and paths exist."""
    if subprocess.run(["which", "psql"], capture_output=True).returncode != 0:
        raise RuntimeError("psql not found in PATH")

    for path_key, path_val in [
        ('source_odoo', config.source_odoo),
        ('target_odoo', config.target_odoo),
        ('openupgrade', config.openupgrade)
    ]:
        if not Path(path_val).exists():
            raise FileNotFoundError(f"Path not found: {path_val}")


def run_single_hop(config: HopConfig, backup_dir: Path, log_dir: Path,
                   do_backup: bool = True, do_mark_validated: bool = True) -> bool:
    """Execute a single migration hop.

    Args:
        config: HopConfig for this hop
        backup_dir: Directory to store backups
        log_dir: Directory for log files
        do_backup: Whether to backup before migration
        do_mark_validated: Whether to create .validated marker on success

    Returns:
        True if successful, False otherwise
    """
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"{config.source_db}_migration_{timestamp}.log"

    setup_logging(log_file)
    logging.info(f"Starting migration: {config.name}")

    backup_name = None
    try:
        check_requirements(config)

        if do_backup:
            backup_name, _ = backup_database(config.source_db, backup_dir, timestamp)

        create_db_clone(config.source_db, config.target_db)

        uninstall_modules(config)

        run_openupgrade(config)

        if do_mark_validated:
            marker = ValidationMarker(
                yaml_path=config.yaml_path,
                timestamp=datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                source=config.source_db,
                target=config.target_db,
                success=True,
                backup=backup_name or ""
            )
            marker.write()
            logging.info(f"Validated marker created for {config.yaml_path.name}")

        logging.info(f"Migration completed successfully: {config.name}")
        return True

    except Exception as e:
        logging.error(f"Migration failed: {e}")
        return False
```

- [ ] **Step 1: Add check_requirements and run_single_hop to migrate_lib.py**

- [ ] **Step 2: Verify import**

```bash
python -c "from migrate_lib import run_single_hop; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add Odoo_Migration/migrate_lib.py
git commit -m "feat: add single-hop migration runner"
```

---

## Task 7: Chain Execution

**Files:**
- Modify: `Odoo_Migration/migrate_lib.py`

```python
def run_chain(folder: Path, backup_dir: Path, log_dir: Path,
              force: bool = False, do_backup: bool = True) -> dict:
    """Execute all validated hops in a folder sequentially.

    Args:
        folder: Path to project folder containing YAML hop configs
        backup_dir: Directory for backups
        log_dir: Directory for logs
        force: Skip validation marker check
        do_backup: Whether to backup before first hop

    Returns:
        dict with 'success', 'failed_hop', 'results' per hop
    """
    hops = detect_hops_in_folder(folder)

    if not hops:
        raise ValueError(f"No valid hops found in {folder}")

    if not force:
        unvalidated = [h for h in hops if not check_validated_marker(h)]
        if unvalidated:
            names = ', '.join([h.yaml_path.name for h in unvalidated])
            raise RuntimeError(f"Unvalidated hops found: {names}. Run individually or use --force.")

    results = []
    first_success = True

    for hop in hops:
        logging.info(f"=== Running hop: {hop.name} ===")
        success = run_single_hop(
            config=hop,
            backup_dir=backup_dir,
            log_dir=log_dir,
            do_backup=do_backup and first_success,
            do_mark_validated=True
        )
        results.append({'hop': hop.name, 'success': success})
        first_success = False

        if not success:
            logging.error(f"Hop failed: {hop.name}. Stopping chain.")
            return {
                'success': False,
                'failed_hop': hop.name,
                'results': results
            }

    return {
        'success': True,
        'failed_hop': None,
        'results': results
    }
```

- [ ] **Step 1: Add run_chain to migrate_lib.py**

- [ ] **Step 2: Verify import**

```bash
python -c "from migrate_lib import run_chain; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add Odoo_Migration/migrate_lib.py
git commit -m "feat: add chain execution for sequential hops"
```

---

## Task 8: Report Generation

**Files:**
- Modify: `Odoo_Migration/migrate_lib.py`

```python
def generate_report(config: HopConfig, success: bool, log_file: Path,
                   backup_name: Optional[str], report_dir: Path):
    """Generate markdown migration report."""
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m%d_%H%M%S")
    report_file = report_dir / f"migration_report_{config.target_db}_{timestamp}.md"

    modules_list = '\n'.join([f'- {m}' for m in config.modules_to_remove]) or '- (none)'

    content = f"""# Migration Report: {config.name}

- **Date:** {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
- **Source:** {config.source_db} ({config.from_version})
- **Target:** {config.target_db} ({config.to_version})
- **Status:** {"SUCCESS" if success else "FAILED"}
- **Backup:** {backup_name or 'N/A'}
- **Log:** `{log_file}`

## Modules Uninstalled
{modules_list}

## Environment
- **Source Odoo:** `{config.source_odoo}`
- **Target Odoo:** `{config.target_odoo}`
- **OpenUpgrade:** `{config.openupgrade}`

## Details
See log file for full trace.
"""

    with open(report_file, 'w') as f:
        f.write(content)

    logging.info(f"Report generated: {report_file}")
    return report_file
```

- [ ] **Step 1: Add generate_report to migrate_lib.py**

- [ ] **Step 2: Verify import**

```bash
python -c "from migrate_lib import generate_report; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add Odoo_Migration/migrate_lib.py
git commit -m "feat: add report generation"
```

---

## Task 9: CLI Entry Point

**Files:**
- Modify: `Odoo_Migration/migrate.py`

```python
#!/usr/bin/env python3
"""Odoo Migration Tool — CLI entry point."""

import argparse
import sys
from pathlib import Path
from migrate_lib import (
    load_hop_config,
    detect_hops_in_folder,
    run_single_hop,
    run_chain,
    check_validated_marker,
    generate_report,
    ValidationMarker
)


def main():
    parser = argparse.ArgumentParser(
        description='Odoo Migration Tool — YAML-driven single-hop with chain support'
    )
    parser.add_argument('target', help='YAML hop file or project folder')
    parser.add_argument('--chain', action='store_true',
                        help='Run all hops in folder sequentially (migration day mode)')
    parser.add_argument('--force', action='store_true',
                        help='Skip .validated marker check in chain mode')
    parser.add_argument('--backup', action='store_true', default=True,
                        help='Backup before migration (default: True)')
    parser.add_argument('--no-backup', action='store_true',
                        help='Skip backup')
    parser.add_argument('--mark-validated', action='store_true',
                        help='Mark hop as validated without running migration')
    parser.add_argument('--global-config', default='migration_config.yaml',
                        help='Global config file path')

    args = parser.parse_args()

    target_path = Path(args.target)
    do_backup = not args.no_backup

    if args.chain:
        if not target_path.is_dir():
            print(f"Error: --chain requires a folder, not a file: {target_path}")
            sys.exit(1)

        folder = target_path
        backup_dir = folder / 'backups'
        log_dir = folder / 'logs'
        report_dir = folder / 'reports'

        result = run_chain(
            folder=folder,
            backup_dir=backup_dir,
            log_dir=log_dir,
            force=args.force,
            do_backup=do_backup
        )

        print(f"\n=== Chain Result ===")
        for r in result['results']:
            status = "SUCCESS" if r['success'] else "FAILED"
            print(f"  {r['hop']}: {status}")

        if result['success']:
            print("\nAll hops completed successfully.")
            sys.exit(0)
        else:
            print(f"\nChain failed at: {result['failed_hop']}")
            sys.exit(1)

    else:
        if target_path.is_dir():
            print(f"Error: Expected YAML file, got folder. Use --chain for folder.")
            sys.exit(1)

        config = load_hop_config(target_path)
        project_folder = target_path.parent
        backup_dir = project_folder / 'backups'
        log_dir = project_folder / 'logs'
        report_dir = project_folder / 'reports'

        if args.mark_validated:
            marker = ValidationMarker(
                yaml_path=target_path,
                timestamp="",
                source=config.source_db,
                target=config.target_db,
                success=True,
                backup=""
            )
            marker.write()
            print(f"Marked as validated: {target_path.name}")
            sys.exit(0)

        timestamp = datetime.datetime.now().strftime("%Y-%m%d_%H%M%S")
        log_file = log_dir / f"{config.source_db}_migration_{timestamp}.log"

        setup_logging(log_file)
        success = run_single_hop(
            config=config,
            backup_dir=backup_dir,
            log_dir=log_dir,
            do_backup=do_backup,
            do_mark_validated=True
        )

        generate_report(config, success, log_file, None, report_dir)

        if success:
            print(f"Migration completed: {config.name}")
            sys.exit(0)
        else:
            print(f"Migration failed: {config.name}")
            sys.exit(1)


if __name__ == '__main__':
    main()
```

- [ ] **Step 1: Rewrite migrate.py with new CLI**

- [ ] **Step 2: Test imports**

```bash
python -c "import migrate; print('OK')"
```

Expected: OK (may have missing imports — fix them)

- [ ] **Step 3: Test CLI help**

```bash
python Odoo_Migration/migrate.py --help
```

Expected: Shows usage with --chain, --force, --backup, --no-backup, --mark-validated

- [ ] **Step 4: Commit**

```bash
git add Odoo_Migration/migrate.py
git commit -m "feat: rewrite migrate.py CLI with chain support"
```

---

## Task 10: Integration Test

**Files:**
- Test: Use existing `BSM/BSM_13to14.yaml` to verify the tool loads correctly

```bash
cd /Users/wsloh/PerfectWork/Odoo_Migration
python -c "
from pathlib import Path
from migrate_lib import load_hop_config

config = load_hop_config(Path('BSM/BSM_13to14.yaml'))
print(f'Name: {config.name}')
print(f'From: {config.from_version} -> To: {config.to_version}')
print(f'Source DB: {config.source_db}')
print(f'Target DB: {config.target_db}')
print(f'Modules: {config.modules_to_remove}')
print(f'Source Odoo: {config.source_odoo}')
"
```

Expected: Prints all config values from BSM_13to14.yaml

- [ ] **Step 1: Run integration test**

- [ ] **Step 2: Fix any import or path errors**

- [ ] **Step 3: Final commit**

```bash
git add Odoo_Migration/
git commit -m "feat: complete Odoo migration tool redesign"
```

---

## Credentials Management (Implemented)

Credentials are stored in `.env` files, NOT in YAML files.

### Implementation

1. **`.env` file loading** added to `migrate_lib.py`:
```python
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass
```

2. **HopConfig updated** with `admin_user` and `admin_password` fields.

3. **Priority order in `load_hop_config()`**:
```python
admin_user=os.environ.get('ODOO_DB_USER', db.get('admin_user', 'admin')),
admin_password=os.environ.get('ODOO_DB_PASS', '') or db.get('admin_password', ''),
```

### File Structure

```
Odoo_Migration/
  .env                          # Global credentials (gitignored)
  .env.example                  # Template for credentials
  migrate_lib.py                # Loads .env automatically
  BSM/
    .env                        # BSM-specific credentials
    BSM_13to14.yaml             # No password in YAML
    custom_cleanup.py
```

### .gitignore includes

```
.env
.env.*
```

---

## Verification

After all tasks, verify:
1. `python migrate.py --help` shows all options
2. `python migrate.py BSM/BSM_13to14.yaml --help` shows hop-specific usage
3. `python -c "from migrate_lib import run_single_hop, run_chain"` succeeds
4. Chain detection works: `python -c "from migrate_lib import detect_hops_in_folder; hops = detect_hops_in_folder(Path('BSM')); print([h.name for h in hops])"`