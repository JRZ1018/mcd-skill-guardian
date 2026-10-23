"""Check competition packaging and the selected live evidence, without network."""
from hashlib import sha1
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("README.md", "MCP_INTEGRATION.md", "CONTEST_DECLARATION.md", "mcp-config.example.json", "skills/mcd-skill-guardian/SKILL.md", "mcd_guardian/core.py", "mcd_guardian/client.py", "docs/index.html", "docs/demo-data.json", "evidence/live-verification.json")


def main():
    for relative in REQUIRED:
        if not (ROOT / relative).is_file():
            raise SystemExit("缺少规定文件：" + relative)
    declaration = (ROOT / "CONTEST_DECLARATION.md").read_bytes()
    blob = sha1(b"blob " + str(len(declaration)).encode("ascii") + b"\0" + declaration).hexdigest()
    if blob != "136e76f3160317049f19f5ba03f4d0fedd70fc3c":
        raise SystemExit("官方参赛声明字节与已核验原文不一致")
    record = json.loads((ROOT / "evidence/live-verification.json").read_text(encoding="utf-8"))
    if record.get("mode") != "live" or record.get("passed") != 3 or record.get("total") != 3:
        raise SystemExit("三项真实查询摘要未全部通过")
    if {c.get("tool") for c in record["calls"]} != {"campaign-calendar", "list-nutrition-foods", "query-nearby-stores"}:
        raise SystemExit("查询摘要工具不匹配")
    token = os.environ.get("MCD_MCP_TOKEN", "").strip()
    if token:
        marker = token.encode("utf-8")
        for path in ROOT.rglob("*"):
            if path.is_file() and not {"private", ".git", ".venv", "__pycache__"}.intersection(path.relative_to(ROOT).parts):
                if marker in path.read_bytes():
                    raise SystemExit("公开候选文件发现环境中的凭证，停止发布；未打印凭证。")
    print("规定文件、官方声明原文与 3/3 实测摘要通过。" + ("环境凭证未出现在公开候选文件中。" if token else "当前未提供环境凭证，未执行精确凭证匹配扫描。"))


if __name__ == "__main__":
    main()
