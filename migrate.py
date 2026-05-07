#!/usr/bin/env python3
"""Odoo Migration Tool — CLI entry point."""

import argparse
import sys
import datetime
from pathlib import Path
from migrate_lib import (
    load_hop_config,
    detect_hops_in_folder,
    run_single_hop,
    run_chain,
    check_validated_marker,
    generate_report,
    ValidationMarker,
    setup_logging
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
                timestamp=datetime.datetime.now().strftime("%Y-%m%dT%H:%M:%S"),
                source=config.source_db,
                target=config.target_db,
                success=True,
                backup=""
            )
            marker.write()
            print(f"Marked as validated: {target_path.name}")
            sys.exit(0)

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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