import sys

if sys.argv[1:3] == ["--engine", "deepsky"]:
    from simplestax.engines.deepsky import main

    raise SystemExit(main(sys.argv[3:]))

from simplestax.app import main  # noqa: E402

raise SystemExit(main())
