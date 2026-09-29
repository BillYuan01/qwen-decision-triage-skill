#!/usr/bin/env python3
"""调用千问决策模型 decision-model-preview（支持百炼与 Token Plan 两条计费通道）。

两条通道的 Host 与 Key 严格配对，交叉组合一律 401，不要混用：

  百炼       https://trial.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone
             Key 前缀 sk-（百炼 API Key），环境变量 DASHSCOPE_API_KEY
  Token Plan https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone
             Key 前缀 sk-sp-（套餐 Key），环境变量 TOKEN_PLAN_API_KEY / ANTHROPIC_AUTH_TOKEN

凭证来源按优先级探测，两种方式配任意一种即可：
  1) 环境变量
  2) QwenPaw provider 存储（目录由 qwenpaw.constant.SECRET_DIR 解析，跨机器可移植）

本脚本不硬编码任何机器路径，且永不打印、落盘或回显 API Key。

用法：
  echo '{"state": ..., "questions": ...}' | python3 decide.py
  python3 decide.py --payload req.json --lane token-plan
  python3 decide.py --doctor          # 体检：逐通道实测可用性
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

MODEL = "decision-model-preview"

LANES = {
    "bailian": {
        "label": "百炼",
        "host": "https://trial.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone",
        "envs": ["DASHSCOPE_API_KEY", "BAILIAN_API_KEY"],
        "base_match": ["dashscope.aliyuncs.com/compatible-mode", "trial.cn-beijing.maas.aliyuncs.com"],
        "key_prefix": "sk-",
    },
    "token-plan": {
        "label": "Token Plan",
        "host": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone",
        "envs": ["TOKEN_PLAN_API_KEY", "ANTHROPIC_AUTH_TOKEN"],
        "base_match": ["token-plan."],
        "key_prefix": "sk-sp-",
    },
}


# --------------------------------------------------------------------------
# 环境探测（全部跨机器，不含硬编码路径）
# --------------------------------------------------------------------------

def provider_dir():
    """返回 (目录, 来源说明)。QwenPaw 官方常量优先，其次常见默认路径。"""
    override = os.environ.get("QWENPAW_PROVIDERS_DIR", "").strip()
    if override:
        return Path(override), "$QWENPAW_PROVIDERS_DIR"
    try:
        from qwenpaw.constant import SECRET_DIR

        return Path(SECRET_DIR) / "providers", "qwenpaw.constant.SECRET_DIR"
    except Exception:
        return Path.home() / ".qwenpaw.secret" / "providers", "默认路径（未检测到 qwenpaw 包）"


def has_qwenpaw():
    try:
        import qwenpaw  # noqa: F401

        return True
    except Exception:
        return False


def _dec(value):
    """解密 ENC: 字段；明文原样返回。解密能力缺失时给出明确报错。"""
    if not isinstance(value, str) or not value:
        return ""
    try:
        from qwenpaw.security import secret_store
    except ImportError:
        if value.startswith("ENC:"):
            raise RuntimeError(
                "provider 中的 Key 是加密的，但当前 python 解释器无法导入 "
                "qwenpaw.security.secret_store。请改用 QwenPaw 自带的解释器运行"
                "（在 QwenPaw 环境中直接用 python3 即可），或改用环境变量配置 Key。"
            )
        return value
    try:
        if secret_store.is_encrypted(value):
            return secret_store.decrypt(value)
    except Exception as exc:  # 主密钥不匹配、文件被搬走等
        raise RuntimeError(f"解密 provider Key 失败：{exc}")
    return value


# --------------------------------------------------------------------------
# 凭证解析
# --------------------------------------------------------------------------

def _from_env(lane):
    for name in LANES[lane]["envs"]:
        val = os.environ.get(name, "").strip()
        if val:
            return val, f"环境变量 ${name}"
    return None, None


def _from_providers(lane):
    """在 QwenPaw provider 存储中按 base_url 匹配本通道的 Key。"""
    spec = LANES[lane]
    root, _ = provider_dir()
    if not root.is_dir():
        return None, None
    for path in sorted(glob.glob(str(root / "**" / "*.json"), recursive=True)):
        if path.endswith("active_model.json"):
            continue
        try:
            cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(cfg, dict):
            continue
        entries = [cfg] if "api_key" in cfg else [
            v for v in cfg.values() if isinstance(v, dict)
        ]
        for p in entries:
            try:
                base = _dec(p.get("base_url", ""))
            except RuntimeError:
                return None, None
            if not any(m in base for m in spec["base_match"]):
                continue
            try:
                key = _dec(p.get("api_key", "")).strip()
            except RuntimeError:
                return None, None
            if not key or not key.startswith(spec["key_prefix"]):
                # base_url 匹配但 Key 没配或前缀不符，跳过
                continue
            # 前缀更长的其它通道也要排除（sk-sp- 同样以 sk- 开头，会误判为百炼）
            if any(
                key.startswith(other["key_prefix"])
                and len(other["key_prefix"]) > len(spec["key_prefix"])
                for other in LANES.values()
            ):
                continue
            name = p.get("name") or Path(path).stem
            return key, f"QwenPaw provider「{name}」"
    return None, None


def find_key(lane):
    """按 环境变量 → provider 存储 的优先级找 Key，返回 (key, 来源) 或 (None, None)。"""
    for getter in (_from_env, _from_providers):
        key, src = getter(lane)
        if key:
            return key, src
    return None, None


def resolve(lane):
    key, src = find_key(lane)
    if key:
        return key, src

    spec = LANES[lane]
    sys.exit(
        f"未找到{spec['label']}通道的 Key。\n"
        f"二选一配置：\n"
        f"  1) 环境变量：export {spec['envs'][0]}=<你的 {spec['key_prefix']} 开头的 Key>\n"
        f"  2) QwenPaw 控制台新增/启用 {spec['label']} 模型提供商\n"
        f"     （base_url 填 https://…{' 或 '.join(spec['base_match'])}… 这一条通道的地址）\n"
        f"运行 python3 {os.path.basename(__file__)} --doctor 可随时体检。"
    )


def pick_lane(requested):
    """auto：优先百炼（按量计费、开通门槛低），不可用则退回 Token Plan。"""
    if requested != "auto":
        return requested
    for lane in ("bailian", "token-plan"):
        if find_key(lane)[0]:
            return lane
    return "bailian"  # 交给 resolve() 输出明确指引


# --------------------------------------------------------------------------
# 请求
# --------------------------------------------------------------------------

def post(host, key, body, timeout=120):
    req = urllib.request.Request(
        host,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        hint = {
            400: "请求体不合法，看 detail 里的字段说明",
            401: "Key 无效，或把两条通道的 Host 与 Key 交叉了"
                 "（百炼 Key 只能配 trial. 主机，Token Plan Key 只能配 token-plan. 主机）",
            422: "请求体校验失败，detail 里会指出出错字段",
            429: "触发限流，退避后重试",
            529: "服务过载，退避后重试",
        }.get(exc.code, "")
        sys.exit(f"HTTP {exc.code}  {hint}\n{detail}")
    except urllib.error.URLError as exc:
        sys.exit(f"网络请求失败：{exc}")


PROBE = {
    "model": MODEL,
    "state": {"text": "连通性探测"},
    "questions": {"p": {"type": "choice", "instructions": "能否正常返回判定？",
                        "criteria": {"yes": "正常", "no": "异常"}}},
}


# --------------------------------------------------------------------------
# 体检
# --------------------------------------------------------------------------

def _pad(s, width):
    """按终端显示宽度补空格（中文算 2 格）；超宽则截断，保证表格不错位。"""
    import unicodedata

    def w(t):
        return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in t)

    if w(s) > width:
        s, cut = s[: width - 1], width - 1
        while s and w(s) > cut:
            s = s[:-1]
        s += "…"
    return s + " " * max(0, width - w(s))


def doctor(live):
    root, src_desc = provider_dir()
    print(f"Python 环境 : qwenpaw 包 {'可用' if has_qwenpaw() else '不可用（仅支持环境变量方式）'}")
    print(f"provider 目录: {root}  （来源：{src_desc}）")
    print()
    print(f"{_pad('通道', 12)}{_pad('Key 来源', 38)}状态")
    print("-" * 78)
    usable = []
    for lane, spec in LANES.items():
        key, src = find_key(lane)
        if not key:
            print(f"{_pad(spec['label'], 12)}{_pad('—', 38)}❌ 未配置")
            continue
        if not live:
            print(f"{_pad(spec['label'], 12)}{_pad(src, 38)}⚠️  已找到 Key（未联网探测）")
            usable.append(lane)
            continue
        try:
            post(spec["host"], key, dict(PROBE), timeout=30)
            print(f"{_pad(spec['label'], 12)}{_pad(src, 38)}✅ 可用")
            usable.append(lane)
        except SystemExit as e:
            print(f"{_pad(spec['label'], 12)}{_pad(src, 38)}❌ {str(e).splitlines()[0]}")
    print()
    print(f"可用通道：{', '.join(usable) if usable else '无'}")
    if len(usable) > 1:
        print("默认走百炼；要指定通道加 --lane token-plan")
    if not usable:
        print("两条通道都不可用。缺 Key 请按 --lane 报错里的指引配置，"
              "或用 --no-probe 单独排查本地配置。")
    return 0 if usable else 1


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="调用千问决策模型")
    ap.add_argument("--payload", help="请求体 JSON 文件路径；缺省从 stdin 读")
    ap.add_argument("--lane", default="auto", choices=["auto", "bailian", "token-plan"],
                    help="计费通道，默认 auto（优先百炼）")
    ap.add_argument("--doctor", action="store_true", help="体检：检查两条通道可用性")
    ap.add_argument("--no-probe", action="store_true", help="配合 --doctor，只查本地配置不联网")
    args = ap.parse_args()

    if args.doctor:
        sys.exit(doctor(live=not args.no_probe))

    raw = open(args.payload, encoding="utf-8").read() if args.payload else sys.stdin.read()
    if not raw.strip():
        sys.exit('请求体为空：{"state": ..., "questions": ...}')
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.exit(f"请求体不是合法 JSON：{exc}")
    if not body.get("questions"):
        sys.exit('"questions" 缺失或为空，至少要有一个问题，形如 '
                 '{"c1": {"type":"choice","instructions":"...","criteria":{...}}}')
    body.setdefault("model", MODEL)

    lane = pick_lane(args.lane)
    key, src = resolve(lane)
    result = post(LANES[lane]["host"], key, body)
    result["_lane"] = LANES[lane]["label"]
    result["_key_source"] = src
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
