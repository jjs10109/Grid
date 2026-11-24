"""兼容同事旧命令；策略统一在 live_grid_simulator_current改.py。"""
import importlib
_engine = importlib.import_module("live_grid_simulator_current改")
for _name in dir(_engine):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_engine, _name)
if __name__ == "__main__":
    raise SystemExit(_engine.main())
