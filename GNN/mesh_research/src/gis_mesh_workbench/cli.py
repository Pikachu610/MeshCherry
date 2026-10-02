import argparse
import json
import sys
from .runner import run


def main():
    if len(sys.argv)>1 and sys.argv[1]=='desktop':
        from .desktop import main as desktop_main
        return desktop_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='ui':
        from .webui import main as ui_main
        return ui_main(sys.argv[2:])
    parser = argparse.ArgumentParser(prog='gis-mesh-workbench')
    parser.add_argument('command',choices=['run'])
    parser.add_argument('config')
    args = parser.parse_args()
    try:
        result = run(args.config)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        return 0 if result['status'] == 'pass' else 2
    except Exception as error:
        print(str(error),file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
