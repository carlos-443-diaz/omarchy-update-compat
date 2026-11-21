# SPDX-FileCopyrightText: 2026 Carlos Diaz
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import json
import os
from pathlib import Path
import sys

from .core import Coordinator, POLICIES, configuration, discover
from . import handoff


def main(argv=None):
    home = Path.home()
    parser = argparse.ArgumentParser(description='Opt-in Arch update compatibility POC. No command installs host packages.')
    parser.add_argument('--config', type=Path, default=Path(os.environ.get('OMARCHY_COMPAT_CONFIG', home / '.config/omarchy/update-compat.toml')))
    parser.add_argument('--state-dir', type=Path, default=Path(os.environ.get('OMARCHY_COMPAT_STATE', home / '.local/state/omarchy/update-compat')))
    parser.add_argument('--cache-dir', type=Path, default=Path(os.environ.get('OMARCHY_COMPAT_CACHE', home / '.cache/omarchy/update-compat')))
    parser.add_argument('--pacman-config', default='/etc/pacman.conf')
    parser.add_argument('--dbpath', default='/var/lib/pacman')
    parser.add_argument('--offline', action='store_true', help='Use checksum-verified cached publisher archives')
    modes = parser.add_subparsers(dest='command', required=True)
    for mode in ['plan', 'stage']:
        sub = modes.add_parser(mode)
        sub.add_argument('--refresh', action='store_true', help='Refresh target metadata in an isolated database')
        sub.add_argument('--json', action='store_true')
    sub = modes.add_parser('track')
    sub.add_argument('application', choices=['gaze'])
    sub.add_argument('--policy', choices=sorted(POLICIES), default='prefer-upstream')
    sub = modes.add_parser('discover')
    sub.add_argument('log', type=Path)
    modes.add_parser('status')
    sub = modes.add_parser('verify')
    sub.add_argument('bundle', type=Path)
    sub = modes.add_parser('transaction-args', help='Experimental container-only Omarchy bridge')
    sub.add_argument('bundle', type=Path)
    sub.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'track':
            applications = configuration(args.config)
            applications[args.application] = args.policy
            args.config.parent.mkdir(parents=True, exist_ok=True)
            temp = args.config.with_suffix('.tmp')
            temp.write_text('[applications]\n' + ''.join(f'{name} = "{value}"\n' for name, value in applications.items()))
            os.replace(temp, args.config)
            print(f'Tracking {args.application}: {args.policy}')
            return 0
        if args.command == 'discover':
            print(json.dumps(discover(args.log.read_text(errors='replace'), configuration(args.config)), indent=2))
            return 0
        if args.command == 'status':
            print((args.state_dir / 'latest.json').read_text())
            return 0
        if args.command == 'verify':
            bundle, _ = handoff.verify(args.bundle, args.pacman_config, args.dbpath, args.cache_dir / 'native')
            print(json.dumps({'verified': True, 'targets': bundle['targets'], 'host_packages_changed': False}))
            return 0
        if args.command == 'transaction-args':
            arguments = handoff.transaction_args(args.bundle, args.output_dir, args.pacman_config,
                                                 args.dbpath, args.cache_dir / 'native')
            sys.stdout.buffer.write(b''.join(a.encode() + b'\0' for a in arguments))
            return 0
        coordinator = Coordinator(args.config, args.state_dir, args.cache_dir,
                                  args.pacman_config, args.dbpath, args.offline)
        plan = coordinator.run(args.command == 'stage', args.refresh)
        if args.json:
            print(json.dumps(plan, indent=2))
        else:
            print(f'Compatibility plan: {plan["status"]}')
            for item in plan['applications']:
                print(f'  {item["application"]}: {item["action"]} — {item["reason"]}')
                if item.get('handoff_to_upstream'):
                    print('    Compatible publisher packages will replace the local build.')
            print(f'Plan: {plan["plan_file"]}')
            if plan.get('bundle'): print(f'Transaction bundle: {plan["bundle"]}')
        return {'ready': 0, 'build-required': 10, 'blocked': 20}[plan['status']]
    except (RuntimeError, ValueError, OSError, KeyError) as error:
        print(f'Compatibility POC: {error}', file=sys.stderr)
        return 2
