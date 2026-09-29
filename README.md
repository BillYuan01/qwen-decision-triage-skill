# qwen-decision-triage

**千问决策模型（`decision-model-preview`）的判断与分流 skill。**

评论审核、工单分类、内容分级、请求路由 → 按置信度自动放行 / 隐藏 / 转人工。

一个 QwenPaw skill。机制上是一个**仅依赖 Python 标准库**的 CLI 脚本（`scripts/decide.py`），也可脱离 QwenPaw 单独使用。

---

## 它解决什么

「这条评论是广告吗？」这类判断，让对话模型顺手做的问题在于：判定标准散在 prompt 里、返回夹带解释、置信度不可靠、token 贵。

`decision-model-preview` 把判断做成一次结构化调用：

|            | 对话模型              | decision-model-preview                    |
| ---------- | -------------------- | ----------------------------------------- |
| 判定标准   | 掺在 prompt 里，靠改措辞 | 结构化写在 `criteria`，选项名 → 描述          |
| 返回       | 自然语言，常带围栏 / 解释 | 纯 JSON，无代码围栏                        |
| 置信度     | 需额外交代，且不可靠     | 每个问题直接给 `confidence` 与 `probabilities` |
| 输出 token | 高（倾向"解释"）        | 低（只回答案）                             |

代价：决策模型**不支持**流式输出、Function Calling、联网搜索、结构化输出。

## 适用环境

- Python 3.8+，仅标准库，无需 `pip install`
- 能访问 `*.cn-beijing.maas.aliyuncs.com`
- 凭证二选一：环境变量，或已配置模型提供商的 QwenPaw

脚本不硬编码任何机器路径，且**永不打印、落盘或回显 API Key**。

## 两条计费通道：Host 与 Key 严格配对

**这是最容易踩的坑**——两把 Key 交叉使用一律 401。

| 通道             | Host                                      | Key 前缀                  | 环境变量                                          |
| -------------- | ----------------------------------------- | ----------------------- | --------------------------------------------- |
| **百炼**         | `trial.cn-beijing.maas.aliyuncs.com`      | `sk-`（含工作空间级 `sk-ws-…`） | `DASHSCOPE_API_KEY`                           |
| **Token Plan** | `token-plan.cn-beijing.maas.aliyuncs.com` | `sk-sp-`                | `TOKEN_PLAN_API_KEY` / `ANTHROPIC_AUTH_TOKEN` |

路径部分两者都是 `/compatible-mode/v1/systemone`，模型名都是 `decision-model-preview`，请求与响应结构完全一致。区别只在域名首段、环境变量名、Key 类型和计费方式。

## 快速开始

```bash
# 1. 体检：实际发一次探测请求，逐通道报可用性
python3 scripts/decide.py --doctor         # 加 --no-probe 只查本地配置、不联网

# 2. 判定
python3 scripts/decide.py --payload req.json                    # 默认走百炼
python3 scripts/decide.py --payload req.json --lane token-plan  # 指定通道
echo '<json>' | python3 scripts/decide.py                       # 也支持管道
```

配置 Key（**任选一种，不必都配**）：

```bash
# 方式 A：环境变量（通用，非 QwenPaw 环境用这个）
export DASHSCOPE_API_KEY=sk-...        # 百炼
export TOKEN_PLAN_API_KEY=sk-sp-...    # Token Plan
```

方式 B：QwenPaw 控制台新增模型提供商。脚本按 `base_url` 子串匹配通道，再解密取用 Key，**`base_url` 与 Key 必须同通道配对**：

| 通道        | Key 前缀   | 可填的 `base_url`                                                                    |
| --------- | -------- | --------------------------------------------------------------------------------- |
| 百炼        | `sk-`    | `https://dashscope.aliyuncs.com/compatible-mode/v1`，或 `https://trial.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` |
| Token Plan | `sk-sp-` | `https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`              |

> 若把 `base_url` 填成别的区域 / 工作区域名，百炼通道会匹配不上、`--doctor` 显示「未配置」——这时改用方式 A（环境变量）最省事。

## 请求体

`state` 放全部待判定内容，`questions` 里每条对应一个问题：

```json
{
  "state": {"comments": [
    {"id": 1, "text": "<评论1>"}
  ]},
  "questions": {
    "c1": {
      "type": "choice",
      "instructions": "`comments` 中 id 为 1 的评论属于哪一类？",
      "criteria": {
        "pass": "正常讨论，包括批评、质疑和报错反馈",
        "spam": "广告导流、诈骗引流、批量营销话术、买卖账号或课程资源"
      }
    }
  }
}
```

三种题型：

| `type`   | `criteria` 写法       | 返回字段                                                    |
| -------- | ------------------- | ------------------------------------------------------- |
| `choice` | 对象：选项名 → 描述        | `choice`、`confidence`、`probabilities`                   |
| `score`  | 数组：各级描述           | `score`、`legend`、`confidence`、`probabilities`            |
| `noul`   | 不需要（只需 `instructions`） | 只有 `noul`（0~1），**没有 `confidence` 和 `probabilities`**     |

⚠️ `noul` 拿不到置信度。做阈值分流的判断只能用 `choice` 或 `score`。

## 分流规则

默认阈值 **0.9**，按误判成本调整（调高更保守，调低更自动）：

- `confidence >= 0.9` 且判定为拦截类 → 自动执行
- `confidence >= 0.9` 且判定为放行类 → 自动放行
- `confidence < 0.9` → 转人工（附一句说明，引用该条 `probabilities` 中关键选项的概率）

**实测提醒**：置信度分布不理想。同样「正常讨论」的内容，清晰的能到 0.99，带链接分享的可能只有 0.86，平台类文案判成广告可能低到 0.13。上线前用真实样本校准阈值，别直接抄文档的 0.9。

## 文件结构

```
.
├── SKILL.md            # skill 定义：适用环境、通道配对、步骤、注意事项
├── scripts/decide.py   # 唯一可执行文件（标准库 CLI）
├── LICENSE             # Apache License 2.0
└── README.md
```

在 QwenPaw 中使用：把 `SKILL.md` 与 `scripts/` 放进 `skills/qwen-decision-triage/` 即可。

## 许可

[Apache License 2.0](LICENSE)
