import sys

if len(sys.argv) > 1 and sys.argv[1] == "hook":
    # fast path: avoid importing the CLI machinery on every hook call
    from .hookclient import main as _main
elif len(sys.argv) > 1 and sys.argv[1] == "wake":
    from .wakeclient import main as _main
elif len(sys.argv) > 1 and sys.argv[1] == "trace-hook":
    from .tracer import main as _main
else:
    from .cli import main as _main

sys.exit(_main())
