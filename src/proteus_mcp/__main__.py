"""python -m proteus_mcp [doctor|serve|build <file.asm>]"""
import sys


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "doctor":
        from .doctor import main as doctor
        return doctor()
    if cmd == "build":
        from .firmware import build
        res = build(open(sys.argv[2], encoding="latin-1").read())
        print(res.summary())
        return 0 if res.ok else 1
    if cmd == "serve":
        from .server import main as serve
        serve()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
