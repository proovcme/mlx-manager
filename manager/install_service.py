"""Install a per-user LaunchAgent using the current interpreter and checkout."""
import argparse
import plistlib
import subprocess
import sys
import socket
from pathlib import Path
import config


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default='org.mlx-manager.service')
    args=parser.parse_args()
    if not all(c.isalnum() or c in '.-' for c in args.label):
        parser.error('Invalid LaunchAgent label')
    try:
        with socket.create_connection((config.MANAGER_HOST,config.MANAGER_PORT),timeout=1):
            parser.error('A service is already listening on port 1924; stop it before installing another LaunchAgent')
    except OSError:
        pass
    config.DATA_ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    agents=Path.home()/'Library/LaunchAgents';agents.mkdir(parents=True,exist_ok=True)
    destination=agents/(args.label+'.plist')
    if destination.exists():
        parser.error('LaunchAgent already exists; stop and edit it explicitly before reinstalling')
    content=dict(Label=args.label,ProgramArguments=[sys.executable,str(config.ROOT/'server.py')],WorkingDirectory=str(config.ROOT),StandardOutPath=str(config.DATA_ROOT/'stdout.log'),StandardErrorPath=str(config.DATA_ROOT/'stderr.log'),RunAtLoad=True,KeepAlive=True,ProcessType='Background')
    destination.write_bytes(plistlib.dumps(content));destination.chmod(0o600)
    import os
    subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(destination)],check=True)
    print(f'Installed {destination}. Open http://127.0.0.1:1924/')

if __name__=='__main__':main()
