import sys

if sys.argv[1:3] == ["--engine", "deepsky"]:
    from simplestax.engines.deepsky import main

    raise SystemExit(main(sys.argv[3:]))

if sys.argv[1:3] == ["--engine", "finish"]:
    from simplestax.engines.finish import main

    raise SystemExit(main(sys.argv[3:]))

if sys.argv[1:3] == ["--engine", "planetary"]:
    from simplestax.engines.planetary import main

    raise SystemExit(main(sys.argv[3:]))

if sys.argv[1:3] == ["--engine", "pss-install"]:
    from simplestax.engines.planetary import main

    raise SystemExit(main([], installing=True))

from simplestax.app import main  # noqa: E402

raise SystemExit(main())
