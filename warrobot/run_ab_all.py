"""Run the Stage A + Stage B analysis for every symbol."""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\Tse2\orderflow")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")

SRC = r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\stage_ab.py"
exec(open(SRC, encoding="utf-8").read())
