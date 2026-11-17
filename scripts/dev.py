import argparse, subprocess, sys
p=argparse.ArgumentParser(); p.add_argument("--host",default="0.0.0.0"); p.add_argument("--port",default="7100"); a,e=p.parse_known_args()
raise SystemExit(subprocess.call([sys.executable,"-m","streamlit","run","app.py","--server.address",a.host,"--server.port",str(a.port),*e]))
