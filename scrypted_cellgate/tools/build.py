"""Use the official SDK build, excluding locally generated Python bytecode."""
from pathlib import Path
import subprocess
from zipfile import ZipFile

subprocess.run(['scrypted-webpack'], check=True)
bundle = Path('out/plugin.zip')
clean = bundle.with_suffix('.clean.zip')
with ZipFile(bundle) as source, ZipFile(clean, 'w') as target:
    for entry in source.infolist():
        if '__pycache__' not in Path(entry.filename).parts and not entry.filename.endswith(('.pyc', '.pyo')):
            target.writestr(entry, source.read(entry))
clean.replace(bundle)
