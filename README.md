# 海外古籍研究谱系库

本项目整理海外馆藏中的中华插图古籍。作品、版本、实体卷册、馆藏机构和数字图像分别编号；书名异称、残卷关系、刊刻年代与流传路径通过可引用的研究观点表达，不把尚有争议的判断写成固定字段。

观点可处于草拟、讨论、采用或撤回状态，正式出版会冻结采用观点及其页叶证据。图像许可按用途、地域、清晰度和有效期约束。样例涉及《湖山胜概》《无双谱》《帝鉴图说》和《新镌海内奇观》。

## 运行

- `python3 service.py --check`：基础检查（含样例数据载入）。
- `python3 service.py --port 8000 --seed data/seed.json`：启动 API 服务并幂等导入样例数据。
- `python3 service.py --data data/store.json`：指定持久化文件，变更后自动保存。
- `npm test`：运行全部测试（基础契约、领域行为、HTTP 接口）。

## 领域模型

| 实体 | 说明 |
| --- | --- |
| 作品 work | 书名与异名；刊刻年代、作者等判断不写成固定字段 |
| 版本 edition | 作品的刊本，馆藏著录标识仅作标签 |
| 卷册 volume | 实体册，含索书号、完整/残卷、存卷；迁移不改编号 |
| 机构 institution | 馆藏机构；更名入 `name_history`，编号不变 |
| 图像 image | 卷册某页叶的数字图像，关联许可 |
| 许可 license | 用途、地域、清晰度上限、到期日 |
| 观点 claim | 断代/作者/套印技法/流传路径/归属，含置信程度与状态 |
| 证据 evidence | 页叶证据：关联图像，或卷册+页叶，含置信程度 |
| 评论 comment / 合并建议 merge | 协作讨论与去重 |
| 出版物 publication | 冻结采用观点与证据快照 |

行为规则：

- 观点状态机：草拟 → 讨论 → 采用；任何状态可撤回（终态，不删除历史引用）。
- 采用一条观点前必须已有至少一条可定位的页叶证据；采用后，同一对象同一类型的其他采用观点自动退回讨论（竞争观点并存，采用唯一）。
- 修订通过新观点的 `supersedes` 字段链接旧观点。
- 合并建议被接受后，被合并方保留 `merged_into` 墓碑，版本/图像/异名并入存续方，谱系由事件日志维系。
- 出版冻结只接受采用状态的观点；快照含观点内容、证据与图像位置，之后撤回或改选不影响快照。
- 导出（`POST /api/exports/evaluate`）逐项检查图像许可，越权图像阻断并给出中文理由。
- 审计（`POST /api/audits`）按出版清单反向抽查：采用版本是否仍被采用、证据是否漂移、图像是否越权；评估日期 `as_of` 由调用方给出，同样输入重复生成字节一致的结果。
- 导入（`POST /api/ingest`）按自然键去重（机构=名称、卷册=机构+索书号等），重复导入不会产生第二份卷册。

## API 概览

查询：`GET /api/works|editions|volumes|institutions|images|licenses|claims|merges|publications|events`，详情加 `/{id}`；观点支持 `?subject_type=&subject_id=&kind=&status=` 过滤。

协作操作（POST）：

- `/api/works` `/api/editions` `/api/volumes` `/api/institutions` `/api/images` `/api/licenses`
- `/api/claims`，`/api/claims/{id}/evidence|comments|status`
- `/api/institutions/{id}/rename`，`/api/volumes/{id}/transfer`
- `/api/merges`，`/api/merges/{id}/resolve`
- `/api/ingest`，`/api/publications`
- `/api/exports/evaluate`，`/api/audits`

## 目录结构

- `service.py`：入口（`--check` / `--port` / `--data` / `--seed`）
- `lineage/`：领域包（model 常量、store 存储、ops 业务、policy 许可、audit 审计、api 接口）
- `data/seed.json`：书目与许可样例（可查询数据，非硬编码）
- `service_contract.py`、`test_lineage.py`、`test_api.py`：测试
