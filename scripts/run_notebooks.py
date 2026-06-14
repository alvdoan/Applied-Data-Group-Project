import json
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
print(f"Base Directory: {BASE}")

def convert_and_run(nb_path):
    print(f"\n==========================================")
    print(f"Processing notebook: {nb_path.name}")
    print(f"==========================================")
    
    if not nb_path.exists():
        raise FileNotFoundError(f"Notebook not found at: {nb_path}")
        
    with open(nb_path, "r", encoding="utf-8") as f:
        nb = json.load(f)
    
    python_lines = []
    # Add mocks for notebook-specific variables and displays
    python_lines.append("import pandas as pd\n")
    python_lines.append("import numpy as np\n")
    python_lines.append("import sys\n")
    python_lines.append("from pathlib import Path\n")
    python_lines.append("def display(*args):\n")
    python_lines.append("    for a in args:\n")
    python_lines.append("        if hasattr(a, 'head'):\n")
    python_lines.append("            print(a.head(2))\n")
    python_lines.append("        else:\n")
    python_lines.append("            print(a)\n")
    python_lines.append("\n")
    
    for cell in nb["cells"]:
        if cell["cell_type"] == "code":
            source_lines = cell["source"]
            for line in source_lines:
                clean_line = line.strip()
                # Remove magic commands and shell execution
                if clean_line.startswith("%") or clean_line.startswith("!"):
                    python_lines.append(f"# {line}")
                elif clean_line.startswith("get_ipython()"):
                    python_lines.append(f"# {line}")
                else:
                    # Clean out common unicode emojis in print statements to avoid cp1252 Windows console crashes
                    safe_line = line.replace("✅", "[SUCCESS]").replace("❌", "[ERROR]").replace("🎉", "[SUCCESS]").replace("⚠️", "[WARNING]")
                    python_lines.append(safe_line)
            python_lines.append("\n\n")
            
    py_path = nb_path.with_suffix(".py")
    with open(py_path, "w", encoding="utf-8") as f:
        f.writelines(python_lines)
    
    print(f"Generated clean Python script: {py_path.name}")
    print(f"Executing script programmatically...")
    
    # Run the script with UTF-8 support in the subprocess environment
    python_exe = sys.executable
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    
    result = subprocess.run(
        [python_exe, str(py_path)],
        cwd=str(BASE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    
    # Clean up script first so we don't leave messy intermediate files
    if py_path.exists():
        os.remove(py_path)
        
    safe_stdout = result.stdout or ""
    safe_stderr = result.stderr or ""
        
    if result.returncode != 0:
        print(f"[ERROR] Error executing {nb_path.name}!")
        print("--- STDOUT ---")
        print(safe_stdout)
        print("--- STDERR ---")
        print(safe_stderr)
        raise RuntimeError(f"Execution failed for {nb_path.name}")
    else:
        print(f"[SUCCESS] Successfully completed execution of: {nb_path.name}")
        print("Output snippet:")
        lines = safe_stdout.strip().split("\n")
        for line in lines[-15:]:
            # Avoid Windows cp1252 console crashes on unicode from notebook output
            print(line.encode("ascii", errors="replace").decode("ascii"))

if __name__ == "__main__":
    # Order of execution based on dependency graph (Sequencing all layers to predictive output)
    notebooks = [
        BASE / "02_silver_cleaning_draft.ipynb",
        BASE / "03_gold_customer_orders.ipynb",
        BASE / "03_gold_customer_profiles.ipynb",
        BASE / "03_gold_first_order_products.ipynb",
        BASE / "03_gold_subscription_behaviour.ipynb",
        BASE / "03_gold_discount_analysis.ipynb",
        BASE / "03_gold_churn_features.ipynb",
        BASE / "03_gold_geographic_segments.ipynb",
        BASE / "03_gold_retention_cohorts.ipynb",
        BASE / "05_ds1_repeat_purchase_prediction.ipynb",
    ]
    
    for nb in notebooks:
        try:
            convert_and_run(nb)
        except Exception as e:
            print(f"\n[ERROR] Pipeline failed at notebook {nb.name}: {e}")
            sys.exit(1)
            
    print("\n==========================================")
    print("[SUCCESS] PIPELINE COMPLETED SUCCESSFULLY!")
    print("==========================================")
