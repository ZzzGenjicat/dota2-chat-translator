"""Supply smaller verified Hy-MT weights for isolated native packaging checks."""
import os
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dota2_map_assistant.local_model_install import NATIVE_FIXTURE
FILES=NATIVE_FIXTURE['files']
MODEL_FOLDER=NATIVE_FIXTURE['folder']

def populate(data: Path):
    destination=data/'models'/MODEL_FOLDER
    destination.mkdir(parents=True,exist_ok=True)
    for name in FILES:
        source=ROOT/'models'/MODEL_FOLDER/name
        target=destination/name
        try: os.link(source,target)
        except OSError: shutil.copy2(source,target)

def smoke_environment(env: dict, data: Path) -> dict:
    populate(data)
    return {**env,'DOTA2_TRANSLATOR_DATA_DIR':str(data),
            'DOTA2_TRANSLATOR_SMOKE_MODEL':str(data/'models'/MODEL_FOLDER/next(iter(FILES)))}
