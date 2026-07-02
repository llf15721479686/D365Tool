import argparse
import json
import hashlib
import hmac
import os
from pathlib import Path
import re
import sys


# 必须与 d365_field_creator.py 里的 ACTIVATION_SECRET 保持一致
DEFAULT_ACTIVATION_SECRET = ""


def load_activation_secret(config_path: str = "config.json") -> str:
    env_secret = os.getenv("D365_ACTIVATION_SECRET", "").strip()
    if env_secret:
        return env_secret
    path = Path(config_path)
    if path.exists():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
            return str(cfg.get("activation_secret", "")).strip()
        except Exception:
            return ""
    return ""


def normalize_machine_code(machine_code: str) -> str:
    normalized = re.sub(r"[^A-Fa-f0-9]", "", machine_code).upper()
    if len(normalized) != 24:
        raise ValueError("机器码格式错误，必须是 24 位十六进制字符串。")
    return normalized


def generate_activation_code(machine_code: str, secret: str) -> str:
    sig = hmac.new(
        secret.encode("utf-8"),
        machine_code.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()
    return f"D365-{sig[:8]}-{sig[8:16]}-{sig[16:24]}"


def main() -> None:
    parser = argparse.ArgumentParser(description="根据机器码生成 D365 工具激活码")
    parser.add_argument("machine_code", nargs="?", help="客户提供的机器码（24位十六进制）")
    parser.add_argument(
        "--secret",
        default="",
        help="签名密钥（默认读取 D365_ACTIVATION_SECRET 环境变量）",
    )
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    args = parser.parse_args()
    secret = args.secret.strip() or load_activation_secret(args.config) or DEFAULT_ACTIVATION_SECRET
    if not secret:
        raise ValueError("activation_secret 未配置，请在 config.json 中设置或配置 D365_ACTIVATION_SECRET 环境变量。")

    machine_code: str
    machine_code_input = (args.machine_code or "").strip()
    while True:
        if not machine_code_input:
            machine_code_input = input("请输入机器码（24位十六进制）: ").strip()
        try:
            machine_code = normalize_machine_code(machine_code_input)
            break
        except ValueError as ex:
            print(f"错误: {ex}")
            machine_code_input = ""

    activation_code = generate_activation_code(machine_code, secret)
    print("机器码:", machine_code)
    print("激活码:", activation_code)
    input("已生成激活码，按回车退出...")


if __name__ == "__main__":
    main()
