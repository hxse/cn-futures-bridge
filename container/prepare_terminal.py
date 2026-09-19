"""从已校验的官方安装包提取终端，保留原生 SimNow 站点。"""

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
    selected = [b for b in brokers if b.get("BrokerID") == lock["broker_id"]]
    if len(selected) != 1 or selected[0].get("BrokerName") != lock["broker_name"]:
        raise ValueError("安装包不含唯一的原生 SimNow 站点，停止构建")
    shutil.copytree(source, target)
    # 只缩减站点列表；不生成认证码、不改写原生服务器或委托协议。
    for broker in list(brokers):
        if broker is not selected[0]:
            brokers.remove(broker)
    ET.ElementTree(brokers).write(target / "broker.xml", encoding="gb2312", xml_declaration=True)
    files = {
        p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(target.rglob("*")) if p.is_file()
    }
    manifest = {
        "version": lock["version"], "installer_sha256": lock["sha256"],
        "executable": lock["executable"], "broker_id": lock["broker_id"],
        "broker_name": lock["broker_name"], "files": files,
    }
    (target / "bridge-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    prepare(*(Path(value) for value in sys.argv[1:]))
