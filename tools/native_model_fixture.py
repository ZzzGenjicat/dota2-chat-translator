"""Supply the production M2M100 INT8 model for isolated native packaging checks."""
import os
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dota2_map_assistant.lightweight_model_install import FILES, MODEL_FOLDER, install

def populate(data: Path):
    destination=data/'models'/MODEL_FOLDER
    destination.mkdir(parents=True,exist_ok=True)
    for name in FILES:
        source=ROOT/'models'/MODEL_FOLDER/name
        target=destination/name
        try: os.link(source,target)
        except OSError: shutil.copy2(source,target)
    install(destination, verify_only=True)

def smoke_environment(env: dict, data: Path) -> dict:
    populate(data)
    return {**env,'DOTA2_TRANSLATOR_DATA_DIR':str(data),
            'DOTA2_TRANSLATOR_SMOKE_MODEL':str(data/'models'/MODEL_FOLDER)}
