"""从同一锁定安装包生成隔离的券商种子，保留原生站点和认证。"""

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tomllib
import xml.etree.ElementTree as ET


def prepare(extracted: Path, target: Path, lock_path: Path) -> None:
    with lock_path.open("rb") as stream:
        lock = tomllib.load(stream)
    source = extracted / lock["payload"]
    if not (source / lock["executable"]).is_file():
        raise ValueError("安装包内缺少锁定的快期2主程序")
    brokers = ET.fromstring((source / "broker.xml").read_text(encoding="gb18030"))
    for name, profile in lock["profiles"].items():
        selected = [b for b in brokers if b.get("BrokerID") == profile["broker_id"]
                    and b.get("BrokerName") == profile["broker_name"]]
        if len(selected) != 1:
            raise ValueError("安装包不含唯一的目标券商，停止构建")
        sites = [s.findtext("Name") for s in selected[0].findall("./Servers/Server")]
        if sites != profile["sites"]:
            raise ValueError("安装包站点与锁定目录不符，停止构建")
        destination = target / name
        shutil.copytree(source, destination)
        # 只缩减券商列表，不生成认证码或改写交易/行情前置。
        filtered = ET.Element(brokers.tag, brokers.attrib)
        filtered.append(selected[0])
        ET.ElementTree(filtered).write(destination / "broker.xml", encoding="gb2312", xml_declaration=True)
        files = {p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(destination.rglob("*")) if p.is_file()}
        manifest = {"version": lock["version"], "installer_sha256": lock["sha256"],
                    "executable": lock["executable"], "profile": name, **profile, "files": files}
        (destination / "bridge-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    prepare(*(Path(value) for value in sys.argv[1:]))
