# SPDX-FileCopyrightText: 2026 Carlos Diaz
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import json
import os
from pathlib import Path
import sys

from .core import Coordinator, POLICIES, configuration, discover
from . import handoff, workflow


def main(argv=None):
    home = Path.home()
    parser = argparse.ArgumentParser(description='Opt-in native application compatibility for Omarchy updates.')
    parser.add_argument('--config', type=Path, default=Path(os.environ.get('OMARCHY_COMPAT_CONFIG', home / '.config/omarchy/update-compat.toml')))
    parser.add_argument('--state-dir', type=Path, default=Path(os.environ.get('OMARCHY_COMPAT_STATE', home / '.local/state/omarchy/update-compat')))
    parser.add_argument('--cache-dir', type=Path, default=Path(os.environ.get('OMARCHY_COMPAT_CACHE', home / '.cache/omarchy/update-compat')))
    parser.add_argument('--pacman-config', default='/etc/pacman.conf')
    parser.add_argument('--dbpath', default='/var/lib/pacman')
    parser.add_argument('--offline', action='store_true', default=os.environ.get('OMARCHY_COMPAT_OFFLINE') == '1', help='Use checksum-verified cached publisher archives')
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
    sub = modes.add_parser('upgrade', help='Run the stock Omarchy update with automatic compatibility handling')
    sub.add_argument('-y', '--yes', action='store_true')
    sub = modes.add_parser('prepare-session', help=argparse.SUPPRESS)
    sub.add_argument('session', type=Path)
    sub = modes.add_parser('verify-installed', help=argparse.SUPPRESS)
    sub.add_argument('session', type=Path)
    sub = modes.add_parser('session-bundle', help=argparse.SUPPRESS)
    sub.add_argument('session', type=Path)
    sub = modes.add_parser('aur-ignore', help=argparse.SUPPRESS)
    sub.add_argument('session', type=Path)
    sub = modes.add_parser('apply', help='Commit a prepared bundle through the packaged native handoff')
    sub.add_argument('bundle', type=Path)
    sub.add_argument('flags', nargs=argparse.REMAINDER)
    sub = modes.add_parser('yay', help=argparse.SUPPRESS)
    sub.add_argument('flags', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.command == 'upgrade':
            return workflow.upgrade(args.yes, args.offline)
        if args.command == 'prepare-session':
            workflow.prepare(args, args.session)
            return 0
        if args.command == 'verify-installed':
            workflow.verify_installed(args.session)
            return 0
        if args.command == 'session-bundle':
            print(workflow.session(args.session).get('bundle', ''))
            return 0
        if args.command == 'aur-ignore':
            print(','.join(workflow.bundle_targets(workflow.session(args.session))))
            return 0
        if args.command in ('apply', 'yay'):
            flags = args.flags[1:] if args.flags[:1] == ['--'] else args.flags
            if args.command == 'apply': workflow.apply(args.bundle, flags)
            else: workflow.yay(flags)
            return 0
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
    except KeyboardInterrupt:
        print('Compatibility request interrupted. Inspect status before retrying.', file=sys.stderr)
        return 130
    except (RuntimeError, ValueError, OSError, KeyError) as error:
        print(f'Compatibility: {error}', file=sys.stderr)
        return 2
