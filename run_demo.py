"""
run_demo.py
===========
One-click setup and launcher script for BCSBatighor-GK Interactive Showcase App.
Executes app.py and generates public link for MacBook presentation.
"""

import sys
import subprocess
import os

def check_and_install_dependencies():
    required = ["gradio", "plotly", "pandas", "networkx", "pyvis"]
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
            
    if missing:
        print(f"📦 Installing missing web app dependencies: {missing}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing)
        print("✅ Dependencies installed successfully!")

def main():
    print("==================================================================")
    print("🚀 BCSBatighor-GK Showcase Web Application Launcher")
    print("   Supervisor: Dr. Sumaiya Tabassum Nimi")
    print("   Cutoff Date: t* = 2023-04-19 (45th BCS Preliminary Exam)")
    print("==================================================================")
    
    check_and_install_dependencies()
    
    print("\n🌐 Starting Gradio Server with Public Link Sharing enabled...")
    print("🔗 A temporary public URL (e.g., https://xxxx.gradio.live) will be printed below.")
    print("📱 You can open this link directly on your MacBook, iPad, or Phone!\n")
    
    import app
    demo = app.build_app()
    demo.launch(share=True, show_error=True)

if __name__ == "__main__":
    main()
