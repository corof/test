"""Einstiegspunkt für die gepackte Windows-App (PyInstaller)."""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from swinglab.app.main import main
    main()
