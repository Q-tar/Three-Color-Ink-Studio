"""Copy exactly the build's flash offsets and binaries, with integrity hashes."""
import hashlib
import json
from pathlib import Path
import shutil

root = Path(__file__).resolve().parent
args = json.loads((root / "build/flasher_args.json").read_text())
output = root / "bin"
output.mkdir(exist_ok=True)
records = []
for offset, relative in args["flash_files"].items():
    source = root / "build" / relative
    target = output / source.name
    shutil.copy2(source, target)
    records.append({"offset": offset, "file": target.name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
manifest = {"chip": "esp32c3", "flash_mode": args["flash_settings"]["flash_mode"],
            "flash_freq": args["flash_settings"]["flash_freq"],
            "flash_size": args["flash_settings"]["flash_size"], "files": records}
(output / "bundle.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print("Firmware bundle ready:", output)
