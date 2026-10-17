# 贡献指南

感谢你愿意改进 AI-IM-Bot。

## 开发环境

```powershell
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

## 提交前检查

- 新增逻辑补对应测试（`tests/`）
- 不提交 `.env`、`config.yaml`、`data/`、`vendor/`、`dist/`
- 配置项变更同步更新 `config.example.yaml` 和 README

## 适配器

新增平台实现 `aibot/adapters/base.py` 的三个方法：

```python
class MyAdapter(Adapter):
    name = "myplatform"
    async def start(self, on_message): ...
    async def send_text(self, session_type, session_id, text, at_user=None, reply_to=None): ...
    async def stop(self): ...
```

然后在 `aibot/app.py` 的 `build_adapter()` 注册，策略、记忆、控制台会自动复用。

## 提交信息

建议使用 `feat: / fix: / docs: / test:` 前缀，例如：

```
feat: 群助手支持天气城市别名
fix: 修复定时问候跨天重复发送
```
