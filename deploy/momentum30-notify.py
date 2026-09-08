"""Hermes no-agent wrapper: send deterministic output; failures must also reach Telegram."""
import subprocess
import sys
from pathlib import Path

root=Path('/home/chang/tw-momentum30')
try:
    r=subprocess.run([sys.executable,str(root/'live_momentum.py')],cwd=root,capture_output=True,text=True,timeout=150)
    if r.stdout.strip():print(r.stdout.strip())
    elif r.returncode:print('【動能30%執行失敗】未產出今日操作，請查 /home/chang/tw-momentum30。')
except Exception as exc:
    print('【動能30%通知故障】'+type(exc).__name__+'；未產出今日操作，請勿沿用舊買單。')
