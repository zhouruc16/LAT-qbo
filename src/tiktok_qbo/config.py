from pathlib import Path
import yaml

def load_shops(path: Path | str = "config/shops.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))["shops"]
