"""
Launcher script — starts both FastAPI backend and Vite dev server.
Usage:  python start.py
"""

import subprocess
import sys
import os
import signal

ROOT = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(ROOT, "frontend")


def main():
    # Check .env exists
    env_path = os.path.join(ROOT, ".env")
    if not os.path.exists(env_path):
        print("ERROR: .env file not found. Create it with your API keys.")
        sys.exit(1)

    # Check node_modules exist
    if not os.path.isdir(os.path.join(FRONTEND, "node_modules")):
        print("Installing frontend dependencies...")
        subprocess.run(["npm", "install"], cwd=FRONTEND, check=True, shell=True)

    print("\n" + "=" * 50)
    print("  Social Scout")
    print("  Backend:  http://localhost:8000")
    print("  Frontend: http://localhost:3000")
    print("  Press Ctrl+C to stop both servers")
    print("=" * 50 + "\n")

    procs = []

    try:
        # Start FastAPI backend
        backend = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server:app", "--reload", "--port", "8000"],
            cwd=ROOT,
        )
        procs.append(backend)

        # Start Vite frontend
        frontend = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=FRONTEND,
            shell=True,
        )
        procs.append(frontend)

        # Wait for either to exit
        while True:
            for proc in procs:
                ret = proc.poll()
                if ret is not None:
                    print(f"\nProcess exited with code {ret}. Shutting down...")
                    raise KeyboardInterrupt
            import time
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nShutting down...")
        for proc in procs:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        print("Done.")


if __name__ == "__main__":
    main()
