"""Tiny test runner (no pytest needed): python3 run_tests.py"""
import sys
import traceback

sys.path.insert(0, ".")
import test_tilltalk as t  # noqa: E402

fails = 0
names = sorted(n for n in dir(t) if n.startswith("test_"))
for name in names:
    try:
        getattr(t, name)()
        print("PASS", name)
    except Exception:
        fails += 1
        print("FAIL", name)
        traceback.print_exc()
print(f"\n{len(names) - fails}/{len(names)} passed")
sys.exit(1 if fails else 0)
