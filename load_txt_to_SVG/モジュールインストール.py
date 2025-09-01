# モジュールインストール.py
import subprocess, sys

pkgs = [
    "lxml>=5.2.1",
    "cssselect>=1.2.0",
    "tinycss2>=1.2.1",
]

def pip_install(pkg):
    print(f"Installing {pkg} ...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", pkg])

if __name__ == "__main__":
    for p in pkgs:
        try:
            pip_install(p)
        except Exception as e:
            print(f"[WARN] {p}: {e}")
    print("Done.")
