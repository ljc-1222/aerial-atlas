"""PyInstaller entry point. Keep package imports absolute."""
from aerial_atlas.app import main

if __name__=="__main__":
    raise SystemExit(main())
