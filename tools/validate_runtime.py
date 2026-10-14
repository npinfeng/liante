"""Preflight production configuration, directory access and existing SQLite integrity."""
import json
import os
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from config import APP_ENV,DEBUG,ADMIN_TOKEN,DATA_DIR,OUTPUT_DIR,LOG_DIR,PREDICTION_ALLOWED_DIRS,STAGE2_INPUT_DIRS,AOI_ALLOWED_DIRS

def main():
    errors=[];warnings=[]
    if APP_ENV not in {'development','production'}: errors.append('APP_ENV_INVALID')
    if APP_ENV=='production' and (DEBUG or not ADMIN_TOKEN): errors.append('PRODUCTION_AUTH_OR_DEBUG_INVALID')
    for path in (DATA_DIR,OUTPUT_DIR,LOG_DIR):
        try:
            path.mkdir(parents=True,exist_ok=True)
            if not os.access(path,os.R_OK|os.W_OK):errors.append('DIRECTORY_ACCESS_FAILED')
        except OSError: errors.append('DIRECTORY_ACCESS_FAILED')
    if not PREDICTION_ALLOWED_DIRS: warnings.append('LEGACY_FILE_PREDICTION_DISABLED_IN_PRODUCTION')
    if not STAGE2_INPUT_DIRS: warnings.append('STAGE2_FOLDER_SOURCE_NOT_CONFIGURED')
    if not AOI_ALLOWED_DIRS: warnings.append('AOI_PATH_INPUT_DISABLED_IN_PRODUCTION')
    for path in (OUTPUT_DIR/'tasks/index.sqlite3',OUTPUT_DIR/'qualifications.sqlite3',OUTPUT_DIR/'stage2_inputs.sqlite3',DATA_DIR/'aoi_frames.sqlite3'):
        if path.exists():
            with sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True) as db:
                if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':errors.append('DATABASE_INTEGRITY_FAILED')
    print(json.dumps(dict(status='ERROR' if errors else 'OK',errors=errors,warnings=warnings)))
    return bool(errors)
if __name__=='__main__':sys.exit(main())
