"""proteus-mcp [serve|doctor|build <file.asm>]   (serve, the MCP stdio server, is the default)"""
import subprocess
import sys


def main() -> int:
    # Paths here contain Turkish letters (tasarım.pdsprj); run in UTF-8 mode however we were started (uvx,
    # a console script, python -m).  The child inherits stdin/stdout, so the MCP stdio link is unaffected.
    if not sys.flags.utf8_mode:
        return subprocess.call([sys.executable, "-X", "utf8", "-m", "proteus_mcp", *sys.argv[1:]])
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
