"""Migration library core data structures and functions."""

import yaml
import subprocess
import logging
import datetime
import os
import re
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass


@dataclass
class HopConfig:
    yaml_path: Path
    name: str
    from_version: str
    to_version: str
    source_db: str
    target_db: str
    admin_user: str = "admin"
    admin_password: str = ""
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
        admin_user=os.environ.get('ODOO_DB_USER', db.get('admin_user', 'admin')),
        admin_password=os.environ.get('ODOO_DB_PASS', '') or db.get('admin_password', ''),
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
        if parse_hop_version(yaml_file) is None:
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


def wait_for_odoo(host='localhost', port=8069, timeout=60):
    """Wait for Odoo server to be ready."""
    import time
    start = time.time()
    while time.time() - start < timeout:
        try:
            import urllib.request
            urllib.request.urlopen(f"http://{host}:{port}", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def uninstall_modules(config: HopConfig):
    """Uninstall modules via XML-RPC by starting Odoo server in background."""
    modules = config.modules_to_remove
    custom_script = config.custom_cleanup_script

    if not modules and not custom_script:
        logging.info("No cleanup tasks.")
        return

    password = os.environ.get('ODOO_PASSWORD', '')
    if not password:
        logging.warning("ODOO_PASSWORD env var not set")

    venv_python = Path(config.source_odoo) / ".venv" / "bin" / "python"
    odoo_bin = Path(config.source_odoo) / "odoo-bin"
    odoo_conf = Path(config.source_odoo) / config.config_file

    if not venv_python.exists():
        raise FileNotFoundError(f"Virtualenv python not found: {venv_python}")

    log_file = Path(config.yaml_path).parent / 'logs' / f"odoo_cleanup_{config.target_db}.log"
    log_file.parent.mkdir(exist_ok=True)

    logging.info(f"Starting Odoo server in background for {config.target_db}")
    with open(log_file, 'w') as log_f:
        odoo_proc = subprocess.Popen(
            [str(venv_python), str(odoo_bin), "-c", str(odoo_conf), "-d", config.target_db],
            stdout=log_f,
            stderr=subprocess.STDOUT,
            cwd=config.source_odoo
        )

    try:
        if not wait_for_odoo(timeout=60):
            raise RuntimeError(f"Odoo server failed to start for {config.target_db}")
        logging.info(f"Odoo server ready, connecting via XML-RPC")

        import xmlrpc.client as xmlrpclib
        common_url = "http://localhost:8069/xmlrpc/2/common"
        object_url = "http://localhost:8069/xmlrpc/2/object"

        common = xmlrpclib.ServerProxy(common_url)
        uid = common.authenticate(config.target_db, config.admin_user, config.admin_password, {})
        if not uid:
            raise RuntimeError(f"Authentication failed for {config.target_db}")
        objects = xmlrpclib.ServerProxy(object_url)
        logging.info(f"Authenticated as uid={uid}")

        for name in modules:
            try:
                module_ids = objects.execute_kw(
                    config.target_db, uid, password,
                    'ir.module.module', 'search',
                    [[('name', '=', name), ('state', '=', 'installed')]]
                )
                if module_ids:
                    logging.info(f"Uninstalling module: {name}")
                    objects.execute_kw(
                        config.target_db, uid, password,
                        'ir.module.module', 'button_immediate_uninstall',
                        [module_ids]
                    )
                else:
                    logging.info(f"Module {name} not found or not installed.")
            except Exception as e:
                logging.warning(f"Failed to uninstall {name}: {e}")

        if custom_script:
            script_path = config.yaml_path.parent.parent / custom_script
            if script_path.exists():
                with open(script_path, 'r') as f:
                    custom_code = f.read()
                logging.info(f"Executing custom cleanup: {script_path}")
                namespace = {'env': type('Env', (), {
                    'cr': type('Cursor', (), {'commit': lambda self: None})(),
                })()}
                exec(custom_code, namespace)

    finally:
        logging.info(f"Stopping Odoo server for {config.target_db}")
        odoo_proc.terminate()
        odoo_proc.wait(timeout=10)


def run_openupgrade(config: HopConfig):
    """Run OpenUpgrade migration on target database."""
    odoo_bin = Path(config.target_odoo) / "odoo-bin"
    odoo_conf = Path(config.target_odoo) / config.config_file
    venv_python = Path(config.target_odoo) / ".venv" / "bin" / "python"

    if not odoo_bin.exists():
        raise FileNotFoundError(f"Odoo binary not found: {odoo_bin}")

    env = os.environ.copy()
    env['OPENUPGRADE_TARGET_VERSION'] = config.to_version

    cmd = [
        str(venv_python) if venv_python.exists() else str(odoo_bin),
        str(odoo_bin),
        "-c", str(odoo_conf),
        "-d", config.target_db,
        "-u", "all",
        "--stop-after-init"
    ]

    logging.info(f"Starting OpenUpgrade to version {config.to_version}")
    success, output = run_command(cmd, env=env, cwd=config.target_odoo)

    if not success:
        raise RuntimeError(f"OpenUpgrade failed for {config.target_db}")

    logging.info(f"OpenUpgrade completed: {config.target_db}")


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


def generate_report(config: HopConfig, success: bool, log_file: Path,
                   backup_name: Optional[str], report_dir: Path):
    """Generate markdown migration report."""
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
