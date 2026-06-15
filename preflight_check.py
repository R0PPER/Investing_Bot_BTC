import importlib
from pathlib import Path

REQUIRED = ["pandas", "numpy", "requests", "yfinance"]


def main():
    print("Preflight check for cloud deployment")
    print("=" * 60)
    missing = []
    for name in REQUIRED:
        try:
            importlib.import_module(name)
            print(f"OK   {name}")
        except Exception as e:
            missing.append(name)
            print(f"MISSING {name}: {e}")

    files = [
        "config.py",
        "daily_strategy.py",
        "state.py",
        "telegram_notify.py",
        "data_validator.py",
        "bootstrap_mvrv.py",
        "backtest.py",
        "requirements.txt",
    ]
    for f in files:
        p = Path(f)
        print(f"{'OK' if p.exists() else 'MISSING'} {f}")

    print("=" * 60)
    if missing:
        print("PRECHECK FAILED — install missing packages with: pip install -r requirements.txt")
        raise SystemExit(1)

    print("PRECHECK OK — project is ready for cloud execution")


if __name__ == "__main__":
    main()
