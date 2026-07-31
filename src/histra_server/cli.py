from __future__ import annotations

import argparse
import uvicorn


def main() -> None:
    parser=argparse.ArgumentParser(prog="histra-server")
    parser.add_argument("--host",default="0.0.0.0")
    parser.add_argument("--port",type=int,default=8000)
    parser.add_argument("--reload",action="store_true")
    parser.add_argument("--log-level",default="info")
    args=parser.parse_args()
    uvicorn.run("histra_server.main:app",host=args.host,port=args.port,reload=args.reload,log_level=args.log_level)

if __name__ == "__main__": main()
