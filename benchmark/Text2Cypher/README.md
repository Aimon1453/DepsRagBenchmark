# Text2Cypher benchmark (`t2c_*`)

| File | Role |
|------|------|
| `t2c_purdue_dataset.json` | Gold dataset (17 cases) |
| `t2c_evaluator.py` | F1 / exact match on Cypher results |
| `t2c_agent_evaluator.py` | Run DepsRAG Team + score |
| `t2c_purdue_cypher_templates.md` | Human-editable templates |

```powershell
python benchmark/Text2Cypher/t2c_agent_evaluator.py
```
