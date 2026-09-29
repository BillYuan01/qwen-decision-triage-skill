---
name: qwen-decision-triage
description: "在QwenPaw调用千问决策模型 decision-model-preview 做判断类任务：评论审核、工单分类、内容分级、请求路由。按置信度自动放行/隐藏/转人工。同时支持百炼（DASHSCOPE_API_KEY）与 Token Plan（sk-sp- 套餐 Key）两条计费通道。触发词：审核评论、过滤垃圾评论、内容分级、工单分类、决策模型、decision model、置信度分流、接入决策模型、帮我接上 decision model。当用户要求按规则批量判定一批文本并给出处置动作，或要求把决策模型接入某个工具时激活。"
---
# 千问决策模型判断与分流

用 `decision-model-preview` 做分类/评分/是非判断。相比让对话模型顺手判一下：判定标准结构化写在 `criteria`、返回纯 JSON 无代码围栏、不生成自然语言、输出 token 开销小。如需接入可说：帮我接上decision-model-preview

## 适用环境

- Python 3.8+，仅用标准库，无需 `pip install`
- 能访问 `*.cn-beijing.maas.aliyuncs.com`
- 凭证二选一：环境变量，或已配置模型提供商的 QwenPaw

在 QwenPaw 环境中直接用 `python3` 即可（脚本会自动找到自带的解释器与密钥目录）。非 QwenPaw 环境用环境变量方式。

脚本不硬编码任何机器路径，且**永不打印、落盘或回显 API Key**。

## 两条计费通道：Host 与 Key 严格配对

**这是最容易踩的坑**：两把 Key 交叉使用一律 401，脚本会明确提示。

| 通道             | Host                                      | Key 前缀   | 环境变量                                          |
| -------------- | ----------------------------------------- | -------- | --------------------------------------------- |
| **百炼**         | `trial.cn-beijing.maas.aliyuncs.com`      | `sk-`（百炼/千问云工作空间级 Key，形如 `sk-ws-…`） | `DASHSCOPE_API_KEY`                           |
| **Token Plan** | `token-plan.cn-beijing.maas.aliyuncs.com` | `sk-sp-` | `TOKEN_PLAN_API_KEY` / `ANTHROPIC_AUTH_TOKEN` |

路径部分两者都是 `/compatible-mode/v1/systemone`，模型名都是 `decision-model-preview`，请求与响应结构完全一致。区别只在域名首段、环境变量名、Key 类型和计费方式。

### 配置

脚本按环境变量 → QwenPaw provider 存储的顺序自动找 Key，**任意一种配好即可，不必都配**。

**方式 A：环境变量**（通用，非 QwenPaw 环境用这个）

```bash
export DASHSCOPE_API_KEY=sk-...            # 百炼
export TOKEN_PLAN_API_KEY=sk-sp-...        # Token Plan
```

**方式 B：QwenPaw provider 存储**

在 QwenPaw 控制台新增模型提供商：**`base_url` 与 Key 必须同通道配对**（脚本按 `base_url` 子串匹配通道，再解密取用 Key；配对错了会 401）。

各通道 `base_url` 可填（子串命中其一即可）：

| 通道        | Key 前缀   | 可填的 `base_url`                                                                                                                                          |
| --------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 百炼        | `sk-`    | `https://dashscope.aliyuncs.com/compatible-mode/v1`（标准 DashScope provider 自带），或 `https://trial.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`（脚本实际请求的 trial 域） |
| Token Plan | `sk-sp-` | `https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`（或任何含 `token-plan.` 的地址）                                                          |

⚠️ 若把 `base_url` 填成**别的区域/工作区**域名（如 `https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/...`），百炼通道会匹配不上，`--doctor` 显示「❌ 未配置」——这时改用**方式 A（环境变量）**最省事。

### 体检

接完先跑一次。`--doctor` 会实际发一次探测请求，逐通道报可用性，并显示 provider 目录的解析来源：

```bash
python3 scripts/decide.py --doctor
```

```
Python 环境 : qwenpaw 包 可用
provider 目录: /path/to/secret/providers  （来源：qwenpaw.constant.SECRET_DIR）

通道          Key 来源                       状态
------------------------------------------------------------------------------
百炼          QwenPaw provider「<名称>」      ✅ 可用
Token Plan    QwenPaw provider「<名称>」      ✅ 可用

可用通道：bailian, token-plan
默认走百炼；要指定通道加 --lane token-plan
```

加 `--no-probe` 只查本地配置不联网。跨机器时 provider 的**名称各不相同**，属正常。

## 步骤

### 1. 读取待判定内容

用户给文件路径就读文件，直接粘贴的文本就用原文。每条一个元素，从 1 开始编号。

### 2. 组装请求体并调用

`state` 放全部待判定内容，`questions` 里**每条对应一个问题**，key 用 `c<编号>` 或语义化命名（`severity`、`escalate`），`instructions` 里引用对应 id。

```bash
python3 scripts/decide.py --payload req.json          # 默认走百炼
python3 scripts/decide.py --payload req.json --lane token-plan
```

```json
{
  "state": {"comments": [
    {"id": 1, "text": "<评论1>"},
    {"id": 2, "text": "<评论2>"}
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

也可用管道：`echo '<json>' | python3 scripts/decide.py`

**默认 `--lane auto`：优先百炼**（按量计费、开通门槛低），不可用时自动退回 Token Plan。返回的 JSON 带 `_lane` 与 `_key_source`，据此告诉用户实际走了哪条通道、消耗哪边额度。

### 3. 读答案

| `type`   | 请求写法                      | 返回字段                                                                   |
| -------- | ------------------------- | ---------------------------------------------------------------------- |
| `choice` | `criteria` 是**对象**：选项名→描述 | `choice`、`confidence`、`probabilities`                                  |
| `score`  | `criteria` 是**数组**：各级描述   | `score`（带小数）、`legend`、`confidence`、`probabilities`（key 为 `"0"`\~`"3"`） |
| `noul`   | 只需 `instructions`         | **只有 `noul`**（0\~1），**没有 `confidence` 和 `probabilities`**              |

⚠️ `noul` 拿不到置信度。做阈值分流的判断只能用 `choice` 或 `score`。

⚠️ `choice` 与 `score` 可在一次调用中混合并行评估，但每个问题只问一件事，多维度分开判定、在分流规则里组合结果。

### 4. 按置信度分流

默认阈值 0.9，按误判成本调整（调高更保守，调低更自动）：

- `confidence >= 0.9` 且判定为拦截类 → 自动执行
- `confidence >= 0.9` 且判定为放行类 → 自动放行
- `confidence < 0.9` → 转人工

转人工的条目附一句说明，引用该条 `probabilities` 中关键选项的概率。

**实测提醒**：置信度分布不理想。同样"正常讨论"的内容，清晰的能给 0.99，带链接分享的可能只有 0.86，平台类文案判成广告可能低到 0.13。上线前用真实样本校准阈值，别直接抄文档的 0.9。

### 5. 输出

先一张表格（编号、内容摘要、判定、confidence、动作），再一行统计：自动处理 X 条，转人工 Y 条。最后注明走的哪条计费通道。

## 注意事项

- **判定边界写在 `criteria` 里。** 灰区（带链接的技术分享、负评、报错）要写成明确的选项描述，比反复改 `instructions` 措辞有效得多。
- **判定看内容性质，不看语气。** 批评、质疑、报错反馈属于正常讨论，这条要显式写进 `criteria`，否则容易被误判。
- **`confidence` 低是模型明确表示拿不准**，分流到人工比重试有效。
- 封号、资金处置等高风险动作保留人工复核。模型给的是判断和把握，不是事实担保。
- 判定内容会随请求发给阿里云服务。涉及用户隐私的数据，调用前先与用户确认合规范围。

## 给用户接入时

用户说"帮我接上decision-model-preview"时，按这个顺序做：

1. 跑 `--doctor`，看清两条通道各自缺什么
2. 缺 Key 就让对方去[百炼控制台](https://platform.qianwenai.com/home)申请，**明确告知要哪个前缀**（百炼 `sk-` / Token Plan `sk-sp-`），并说明买哪种计费方式
3. 让对方二选一配置方式（环境变量或控制台 provider），别两边都要求配
4. 配完再跑一次 `--doctor` 确认，然后跑一条真实数据验证返回结构

不要让对方把 Key 贴在对话里。

## 错误处理

脚本已对常见状态码给出提示：

| 状态            | 含义              | 处理                  |
| ------------- | --------------- | ------------------- |
| `400`         | 请求体不合法          | 看响应 `detail`        |
| `401`         | Key 无效，或两条通道交叉了 | 核对 Host 与 Key 配对    |
| `422`         | 请求体校验失败         | 响应 `detail` 会指出出错字段 |
| `429` / `529` | 限流 / 服务过载       | 退避后重试               |

若提示无法导入 `qwenpaw.security.secret_store`，说明当前解释器不在 QwenPaw 环境中，改用环境变量方式即可。

## 其他

- 决策模型**不支持**流式输出、Function Calling、联网搜索、结构化输出。
- 一次请求可并行判定多条，建议单批不超过 50 条。
- 百炼控制台API申请页面：<https://platform.qianwenai.com/home/api-keys>
- 官方文档：<https://platform.qianwenai.com/docs/api-reference/decision-model-api>
- Token Plan 实践页（多工具接入示例）：<https://platform.qianwenai.com/docs/token-plan/best-practices/decision-model>
