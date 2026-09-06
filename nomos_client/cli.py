import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from .http import Client
from .watch import watch


def main():
    parser = argparse.ArgumentParser(
        description="Nomos local agent client (never starts or supervises agents)."
    )
    parser.add_argument("--base-url", default=os.environ.get("NOMOS_BASE_URL", "http://127.0.0.1:8484"))
    parser.add_argument("--project", type=int, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inbox")
    w = sub.add_parser("watch")
    w.add_argument("--state", default=str(Path.home() / ".nomos-client" / "spool.db"))
    w.add_argument("--once", action="store_true")
    for name in ["ack", "resolve", "delivered"]:
        cmd = sub.add_parser(name)
        cmd.add_argument("request_id", type=int)
        cmd.add_argument("--reason", default="")
    resume = sub.add_parser("resume")
    resume.add_argument("--ticket", type=int)
    progress = sub.add_parser("progress")
    progress.add_argument("resume_id", type=int)
    progress.add_argument("summary")
    checkpoint = sub.add_parser("checkpoint")
    checkpoint.add_argument("file", help="JSON checkpoint file, or - for stdin")
    args = parser.parse_args()
    client = Client(args.base_url, os.environ.get("NOMOS_API_KEY", ""))
    prefix = f"/api/projects/{args.project}"
    try:
        if args.command == "watch":
            watch(client, args.project, args.state, once=args.once, emit=lambda line: print(line, flush=True))
            return
        if args.command == "inbox":
            result = client.request("GET", prefix + "/inbox")
        elif args.command == "resume":
            started = client.request("POST", prefix + "/resumes", {"ticket_number": args.ticket}, str(uuid.uuid4()))
            result = client.request(
                "GET", prefix + "/context" + (f"?ticket_number={args.ticket}" if args.ticket else "")
            )
            result["resume_id"] = started["id"]
        elif args.command == "progress":
            result = client.request("POST", prefix + f"/resumes/{args.resume_id}/progress", {"summary": args.summary}, str(uuid.uuid4()))
        elif args.command == "checkpoint":
            raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
            result = client.request("POST", prefix + "/checkpoints", json.loads(raw), str(uuid.uuid4()))
        else:
            kind = {"ack": "acknowledged", "resolve": "resolved", "delivered": "delivered"}[args.command]
            result = client.request(
                "POST",
                prefix + f"/inbox/{args.request_id}/receipts",
                {"kind": kind, "reason": args.reason},
                str(uuid.uuid4()),
            )
        print(json.dumps(result, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
